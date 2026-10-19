"""Local tutoring-order grammar. No network client, model or external service."""
import re
from dataclasses import dataclass

from .models import Draft, Fact, LABELS
from .regions import REGION_INDEX, administrative_name_in_text, aliases
from .splitting import split_orders, reference_in_update
from .subjects import recognize as recognize_subjects
from .text_rules import NUMBER, Source, number
from .templates import GROUPS as LABEL_GROUPS, LABEL_KIND, headers

PARSER_VERSION = "local-v7"
PREFER = re.compile(r"优先|最好|优选|更希望|尽量|倾向")
RANGE = rf"(?P<lo>{NUMBER})(?:\s*[-~～—–至到]\s*(?P<hi>{NUMBER}))?"
GRADE_RE = re.compile(r"(?:小学)?([一二三四五六七八九1-9])年级|小([一二三四五六1-6])|(?<![提拔])([初高])(?:中)?([一二三123])|([初高])中|幼儿园|学前班")
GRADE_ORDER = [n + "年级" for n in "一二三四五六"] + [stage + n for stage in "初高" for n in "一二三"]
RATE_RE = re.compile(RANGE + r"\s*(?:元|块)?\s*(?:/\s*(?:个)?\s*(?P<unit>小时|时|[hH]|次|节|月)|每\s*(?P<per>小时|时|次|节|月)|(?P<hour>一小时))")
QUALIFIER = re.compile(r"(研究生|硕士|博士|大学生|本科生|在职教师|在校大学生|毕业生)\s*[:：]?\s*$")
CLOSED = re.compile(r"(?<!未)(?:已出|已找到(?:老师)?|已招到(?:老师)?|已接单|已接|停止招聘|已结束|取消招聘|订单取消)")
REGION_ALIASES = {}
for _node in REGION_INDEX.values():
    if _node["name"] not in {"市辖区", "县", "市辖县", "省直辖县"}:
        for _alias in aliases(_node["name"]):
            REGION_ALIASES.setdefault(_alias, []).append(_node)
LOCATION_RE = re.compile("|".join(re.escape(a) for a in sorted(REGION_ALIASES, key=len, reverse=True)))
GENERIC_DISTRICTS={'东区','西区','南区','北区','城区','郊区','矿区','开发区','新区'}
DISTRICT_TAG = re.compile(r'(?m)^[ \t\U0001F300-\U0001FAFF\u2600-\u27BF\ufe0f]*#(?P<place>'+ '|'.join(re.escape(a) for a in sorted(REGION_ALIASES,key=len,reverse=True) if any(n['level']==3 for n in REGION_ALIASES[a]))+r')(?:回收单|新单)?(?=[ \t]*(?:#|\n|$))')


@dataclass
class Segment:
    kind: str
    start: int
    end: int
    text: str
    label: str = ""
    header_start: int = 0


def segments(source):
    matches=headers(source.text)
    if not matches:
        return [Segment("general",0,len(source.text),source.text)]
    result=[]
    if matches[0][0].start():
        result.append(Segment("general",0,matches[0][0].start(),source.text[:matches[0][0].start()]))
    for index,(found,kind,label) in enumerate(matches):
        end=matches[index+1][0].start() if index+1<len(matches) else len(source.text)
        result.append(Segment(kind,found.end(),end,source.text[found.end():end],label,found.start()))
    return result


class LocalExtractor:
    def extract(self, message):
        blocks = split_orders(message["text"])
        if len(blocks) > 100:
            from .extraction import ModelError
            raise ModelError("单条消息订单过多，已保存，需分批重试")
        return [draft for block in blocks if (draft := self.parse(block)) is not None]

    def parse(self, block):
        from .extraction import validate_model_order
        source = Source(block.text)
        # A reference is an identifier only, never a location or a number field.
        if block.reference:
            offset = source.text.find(Source(block.reference).text)
            source.text = source.text[:offset] + " " * len(Source(block.reference).text) + source.text[offset + len(Source(block.reference).text):]
        parts = segments(source)
        candidates, issues = {}, []

        def issue(text):
            if text not in issues:
                issues.append(text)

        def add(field, value, start, end, mode="must"):
            if value in (None, [], "") or end <= start:
                return
            evidence = source.evidence(start, end)
            if len(evidence) > 4000:
                issue(LABELS[field] + "证据过长，需核对原文")
                return
            candidate = Fact(value=value, evidence=evidence, mode=mode)
            existing = candidates.get(field)
            if existing and field in {'area','city','province'} and isinstance(existing.value,str) and isinstance(value,str):
                level={'province':1,'city':2,'area':3}[field]
                old={n['path'][-1] for n in REGION_ALIASES.get(existing.value,[]) if n['level']==level}
                new={n['path'][-1] for n in REGION_ALIASES.get(value,[]) if n['level']==level}
                if old and old==new:
                    if len(value)>len(existing.value):candidates[field]=candidate
                    return
            if existing and existing.mode!=mode:
                # A stated preference cannot erase an explicit hard constraint.
                if existing.mode=='prefer':candidates[field]=candidate
                return
            if existing and (existing.value != value or existing.mode != mode):
                candidates.pop(field, None)
                issue(LABELS[field] + "存在多个或冲突表述，需核对原文")
            elif not any(i.startswith(LABELS[field] + "存在") for i in issues):
                candidates[field] = candidate

        def found(field, value, part, match, mode="must", whole=False):
            add(field, value, part.start if whole else part.start + match.start(), part.end if whole else part.start + match.end(), mode)

        def collect(field, entries):
            if entries:
                vals = list(dict.fromkeys(v for value, _, _ in entries for v in (value if isinstance(value, list) else [value])))
                add(field, vals, min(s for _, s, _ in entries), max(e for _, _, e in entries))

        grades, subjects, focus, rate_options, schedules, manual_entries = [], [], [], [], [], []
        body_parts = [p for p in parts if p.kind != "contact"]
        explicit_subjects=any(p.kind in {"subject","student_subject"} for p in parts)
        for part in body_parts:
            text = part.text
            # Standalone body tags are explicit message metadata. The reference
            # itself has already been masked and cannot provide location proof.
            for tag in DISTRICT_TAG.finditer(text):
                location_part=Segment('address',part.start+tag.start('place'),part.start+tag.end('place'),tag['place'])
                self.location(location_part,found,issue)
            # Some sources swap the teacher/pay labels. Only unambiguous
            # whole-value signatures may override the declared field role.
            if part.kind=='teacher' and RATE_RE.fullmatch(text.strip()):
                part.kind='pay'
            elif part.kind=='pay' and not re.search(NUMBER,text) and re.match(r'\s*(?:老师性别|教师要求|教员要求|性别)',text):
                part.kind='teacher'
            if part.kind in {"student", "student_subject", "subject", "general"}:
                if part.kind in {'student','student_subject'} and (adult:=re.match(r'\s*#?(成人)',text)):
                    grades.append(('成人',part.start+adult.start(1),part.start+adult.end(1)))
                for m in GRADE_RE.finditer(text):
                    if m[1] or m[2]:
                        n = int(number(m[1] or m[2]))
                        grade = ("一二三四五六"[n - 1] + "年级") if n <= 6 else "初" + "一二三"[n - 7]
                    elif m[3]:
                        grade = m[3] + "一二三"[int(number(m[4])) - 1]
                    else:
                        grade = {"初中": "初中", "高中": "高中"}.get(m.group(), m.group())
                    if grade in {"初中", "高中"}:
                        issue("年级仅注明学段，需核对具体年级")
                        continue
                    grades.append((grade, part.start + m.start(), part.start + m.end()))
                for m in re.finditer(r"(?:一|1)\s*对\s*(" + NUMBER + r")|(" + NUMBER + r")\s*(?:名|个|位)\s*(?:学员|学生|孩子)", text):
                    found("students_count", number(m[1] or m[2]), part, m)
                gender = re.search(r"男孩|男生|男学员|女童|女孩|女生|女学员", text)
                mixed = re.search(r'一男一女|1男1女|一女一男|1女1男',text)
                if mixed:
                    found('student_gender','混合',part,mixed)
                elif gender:
                    value = "混合" if re.search(r"男孩|男生|男学员", text) and re.search(r"女童|女孩|女生|女学员", text) else ("女" if gender.group().startswith("女") else "男")
                    found("student_gender", value, part, gender, whole=True)
                elif part.kind in {"student","student_subject"} and (gender := re.search(r"(?<![\u4e00-\u9fff])[男女](?![\u4e00-\u9fff])", text)):
                    found("student_gender", gender.group(), part, gender)
            if part.kind in {"subject","student_subject"} or part.kind in {"student","general"} and not explicit_subjects:
                ignored=[m.span() for pattern in (GRADE_RE,re.compile(NUMBER+r'岁(?:半)?'),re.compile(r'^\s*#?成人')) for m in pattern.finditer(text)] if part.kind in {"student_subject","subject"} else []
                entries, focused, subject_issues = recognize_subjects(text, part.kind in {"subject","student_subject"}, ignored)
                for values, start, end in entries:
                    subjects.append((values, part.start + start, part.start + end))
                for values, start, end in focused:
                    focus.append((values, part.start + start, part.start + end))
                for problem in subject_issues:
                    issue(problem)
                if part.kind in {"subject","student_subject"} and not entries and text.strip():
                    issue("科目尚未识别，需核对原文")

            if part.kind in {"pay", "time", "general"}:
                fractional=[]
                if part.kind=='pay':
                    for m in re.finditer(RANGE+r'\s*(?:元|块)?\s*/\s*(?P<hours>'+NUMBER+r')\s*(?:小时|[hH])',text):
                        if m.start() and text[m.start()-1] in '-−':
                            issue('课酬出现负数或异常格式，需核对原文');continue
                        hours=number(m['hours'])
                        if hours<=0:
                            issue('课酬小时数异常，需核对原文')
                            continue
                        value={'min':number(m['lo'])/hours,'max':number(m['hi'])/hours} if m['hi'] else number(m['lo'])/hours
                        found('hourly_rate',value,part,m)
                        fractional.append(m.span())
                    for m in re.finditer(r'(?<![\d./])'+RANGE+r'\s*(?:元|块)?\s*[hH](?!\s*/)',text):
                        if any(s<=m.start()<e for s,e in fractional) or re.search(r'每次|一次|时长',text[max(0,m.start()-6):m.start()]):continue
                        if m.start() and text[m.start()-1] in '-−':
                            issue('课酬出现负数或异常格式，需核对原文');continue
                        value={'min':number(m['lo']),'max':number(m['hi'])} if m['hi'] else number(m['lo'])
                        add('hourly_rate',value,part.header_start,part.start+m.end())
                if "时薪" in part.label and not RATE_RE.search(text):
                    m = re.search(RANGE + r"\s*(?:元|块)?", text)
                    if m:
                        add("hourly_rate", {"min": number(m["lo"]), "max": number(m["hi"])} if m["hi"] else number(m["lo"]), part.header_start, part.start + m.end())
                for m in RATE_RE.finditer(text):
                    if any(s<=m.start()<e for s,e in fractional):continue
                    if m['hour'] and re.search(r'周|星期|礼拜',text[max(0,m.start()-3):m.start()]):continue
                    if m.start() and text[m.start()-1] in "-−":
                        issue("课酬出现负数或异常格式，需核对原文")
                        continue
                    unit = (m["unit"] or m["per"] or "小时").lower()
                    value = number(m["lo"])
                    if m["hi"]:
                        value = {"min": value, "max": number(m["hi"])}
                    field = "hourly_rate" if unit in {"小时", "时", "h"} else "monthly_rate" if unit == "月" else "lesson_rate"
                    qualifier = QUALIFIER.search(text[max(0, m.start() - 12):m.start()])
                    if qualifier:
                        if field != "hourly_rate":
                            issue("存在条件课酬，需核对适用身份和单位")
                            continue
                        name = qualifier[1]
                        key, qual = ("education", "博士" if name == "博士" else "硕士") if name in {"研究生", "硕士", "博士"} else ("teacher_type", {"在职教师": "在职教师", "毕业生": "毕业生"}.get(name, "在校大学生"))
                        rate_options.append(({key: qual, "hourly_rate": value}, part.start + max(0, m.start() - 12) + qualifier.start(), part.start + m.end()))
                    elif not re.search(r"中介费|信息费|服务费", text[max(0, m.start() - 8):m.start()]):
                        found(field, value, part, m)
                for m in re.finditer(r"(?:每|一|1)(?:周|星期|礼拜)\s*" + RANGE + r"\s*(?:次|节)|" + RANGE.replace("lo", "lo2").replace("hi", "hi2") + r"\s*(?:次|节)\s*/\s*(?:周|星期)", text):
                    lo, hi = m["lo"] or m["lo2"], m["hi"] or m["hi2"]
                    found("weekly_sessions", {"min": number(lo), "max": number(hi)} if hi else number(lo), part, m)
                duration_re = re.compile(r"(?:每次|每节|一次|一节)\s*" + RANGE + r"\s*(?:个\s*)?(?P<before_half>半)?(?P<u>小时|分钟|[hH])(?:\s*(?P<half>半))?|" + RANGE.replace("lo", "lo2").replace("hi", "hi2") + r"\s*(?:个\s*)?(?P<u2>小时|分钟|[hH])\s*(?:/\s*(?:次|节)|每次|每节)")
                for m in duration_re.finditer(text):
                    lo, hi = number(m["lo"] or m["lo2"]), m["hi"] or m["hi2"]
                    divisor = 60 if (m["u"] or m["u2"]) == "分钟" else 1
                    val = {"min": lo / divisor, "max": number(hi) / divisor} if hi else (lo + (0.5 if m["half"] or m['before_half'] else 0)) / divisor
                    found("duration_hours", val, part, m)
                if part.kind=='time':
                    # The header, or an immediately preceding named lesson day,
                    # supplies the per-lesson role; travel and weekly totals do not.
                    prefix=r'^\s*' if part.label in {'每次时长','时长'} else r'(?:周末|(?:周|星期|礼拜)[一二三四五六日天1-7])\s*'
                    duration=re.search(prefix+RANGE+r'\s*(?:个\s*)?(?P<unit>小时|分钟|[hH])(?:\s*(?P<half>半))?',text)
                    if duration and not re.match(r'\s*(?:内|以内|以上|以下|起|左右|每周|/周)',text[duration.end():]):
                        lo=number(duration['lo']);hi=duration['hi']
                        divisor=60 if duration['unit']=='分钟' else 1
                        val={'min':lo/divisor,'max':number(hi)/divisor} if hi else (lo+(0.5 if duration['half'] else 0))/divisor
                        clock_lengths=[]
                        if part.label in {'每次时长','时长'} and re.search(r'周|星期|礼拜',text):
                            for clock in re.finditer(r'(?<![\d.])(?P<h>\d{1,2}):(?P<m>\d{2})\s*[-~—–至到]\s*(?P<h2>\d{1,2}):(?P<m2>\d{2})(?!\d)',text):
                                h,m,h2,m2=(int(clock[k]) for k in ('h','m','h2','m2'))
                                if 0<=h<=h2<=23 and 0<=m<60 and 0<=m2<60 and (h,m)<(h2,m2):
                                    clock_lengths.append((h2*60+m2-h*60-m)/60)
                        low,high=(val['min'],val['max']) if isinstance(val,dict) else (val,val)
                        if any(not low-1e-6<=length<=high+1e-6 for length in clock_lengths):
                            candidates.pop('duration_hours',None)
                            issue(LABELS['duration_hours']+'存在与时间段不一致的表述，需核对原文')
                        else:
                            add('duration_hours',val,part.header_start,part.start+duration.end())
                # A stated budget without a unit must never become an hourly rate.
                if part.kind == "pay" and "时薪" not in part.label and not RATE_RE.search(text) and not fractional and 'hourly_rate' not in candidates and re.search(NUMBER, text):
                    issue("课酬单位或格式待确认")
                self.schedule(part, schedules, issue)

            if part.kind in {"address", "general", "reference"}:
                self.location(part, found, issue)
                if part.kind == "address" and text.strip():
                    add("location", text.strip(), part.start, part.end)
            if part.kind not in {"student","student_subject"}:
                for m in re.finditer(r"(?:无需|不用|不需要|免|不接受|不|需要|要求|先)?试课", text):
                    found("trial", not bool(re.match(r"无需|不用|不需要|免|不接受|不", m.group())), part, m)
                if re.search(r"(?:中介费|信息费|服务费)[^\n。;；]{0,12}(?:%|百分|成)", text):
                    issue("比例中介费需核对")
                for m in re.finditer(r"(?:无|免)(?:中介费|信息费)|(?:中介费|信息费)\s*[:：]?\s*(" + NUMBER + r")\s*(?:元)?(?![\d.%成])", text):
                    # Percent fees cannot be converted into a fixed amount.
                    if re.match(r"\s*[%％成]", text[m.end():]):
                        issue("比例中介费需核对")
                    else:
                        found("agency_fee", number(m[1]) if m[1] else 0, part, m)
            if part.kind in {"teacher", "general", "notes"}:
                self.requirements(part, found, issue, add, manual_entries)
            elif part.kind in {'student','student_subject'}:
                self.learner_field_requirements(part,issue,manual_entries)
            if part.kind in {"time", "general", "address"}:
                for m in re.finditer(r"线上|线下|网课|上门", text):
                    found("mode", "线上" if m.group() in {"线上", "网课"} else "线下", part, m)
                for m in re.finditer(r"长期|短期|寒假|暑假", text):
                    found("term", "长期" if m.group() == "长期" else "短期", part, m)

        collect('manual_requirements',manual_entries)
        expanded_grades = []
        for index, entry in enumerate(grades):
            grade, start, end = entry
            if index and re.fullmatch(r"\s*[-~—–至到]\s*", source.text[grades[index-1][2]:start]):
                previous = grades[index-1][0]
                if previous in GRADE_ORDER and grade in GRADE_ORDER and GRADE_ORDER.index(previous) <= GRADE_ORDER.index(grade):
                    expanded_grades.extend((g, start, end) for g in GRADE_ORDER[GRADE_ORDER.index(previous)+1:GRADE_ORDER.index(grade)])
                else:
                    issue("年级范围存在歧义，需核对原文")
            expanded_grades.append(entry)
        if len(grades) > 1 and re.search(r"升|转入", source.text[grades[0][1]:grades[-1][2]]):
            issue("升学年级存在歧义，需核对原文")
        elif not any(i.startswith("年级范围存在") for i in issues):
            collect("grade", expanded_grades)
        if not any(i.startswith(("科目为备选", "全科包含排除")) for i in issues):
            collect("subject", subjects)
        collect("focus_subjects", focus)
        if rate_options:
            add("rate_options", [v for v, _, _ in rate_options], min(s for _, s, _ in rate_options), max(e for _, _, e in rate_options))
        if schedules:
            add("schedule", [v for v, _, _ in schedules], min(s for _, s, _ in schedules), max(e for _, _, e in schedules))
        closed = CLOSED.search(source.text)
        if closed and re.search(r"未|没有|还没|尚未", source.text[max(0, closed.start() - 5):closed.start()]):
            closed = None
        reference = block.reference
        update = re.search(r"补充|更正|改为|调整为|课酬调整", source.text)
        if not reference and (closed or update):
            reference = reference_in_update(block.text)
        is_update = bool((closed or update) and not any(p.kind in {"student","student_subject","subject"} for p in parts) and not ("grade" in candidates and "subject" in candidates))
        signal = any(p.kind in {"student","student_subject","subject","pay","teacher"} for p in parts)
        if not reference and not is_update and not (signal or ("subject" in candidates and ("grade" in candidates or any(k in candidates for k in ("hourly_rate", "monthly_rate", "lesson_rate"))))):
            return None
        if not candidates and not is_update:
            issue("未识别出可核对字段，需核对原文")
        if not reference and is_update:
            issue("更新未提供明确单号，需人工关联")
        item = {"reference": reference, "kind": "update" if is_update else "order", "facts": {k: v.model_dump() for k, v in candidates.items()}, "status": "closed" if closed else "open", "status_evidence": source.evidence(closed.start(), closed.end()) if closed else None}
        draft = validate_model_order(item, block.text) if candidates or reference or is_update else Draft.model_validate(item)
        draft.source_text = block.text
        draft.validation_issues = list(dict.fromkeys([*issues, *draft.validation_issues]))[:50]
        return draft

    @staticmethod
    def location(part, found, issue):
        # Match explicit administrative names only. Higher levels are never inferred.
        text = part.text
        by_level = {}
        matches=list(LOCATION_RE.finditer(text))
        context={1:set(),2:set()}
        # Explicit canonical names can disambiguate weaker district aliases.
        # An explicit second district is retained even when it conflicts.
        for m in matches:
            for node in REGION_ALIASES[m.group()]:
                if m.group()!=node['name'] or node['name'] in GENERIC_DISTRICTS or not administrative_name_in_text(node['name'],m.group()+text[m.end():]):
                    continue
                for higher in (1,2):
                    if len(node['path'])>=higher:
                        context[higher].add(node['path'][higher-1])
        # In a contiguous address such as 闵行浦江, a unique initial district
        # constrains a following weak alias; separate alternatives stay intact.
        if len(matches)>1 and not any(context.values()):
            first,second=matches[:2]
            nodes=[n for n in REGION_ALIASES[first.group()] if n['level']==3]
            parents={n['path'][1] for n in nodes}
            if nodes and len(parents)==1 and first.start()==len(text)-len(text.lstrip(' \t\n#')) and first.end()==second.start():
                context[2].update(parents)
        for m in matches:
            alias = m.group()
            for node in REGION_ALIASES[alias]:
                name, level = node["name"], node["level"]
                # Short district aliases are only accepted in address sections.
                if level == 3 and alias != name and part.kind != "address":
                    continue
                if level == 3 and alias != name and re.match(r'省|市|心',text[m.end():]):
                    continue
                if level == 3 and alias != name and any(codes and node['path'][higher-1] not in codes for higher,codes in context.items()):
                    continue
                if level==3 and name in GENERIC_DISTRICTS and any(codes and node['path'][higher-1] not in codes for higher,codes in context.items()):continue
                if administrative_name_in_text(name, m.group() + text[m.end():]):
                    by_level.setdefault(level, []).append((m.start(), m.end(), alias))
        for level, field in [(1, "province"), (2, "city"), (3, "area")]:
            entries = by_level.get(level, [])
            # Prefer complete/longest names at the same source position.
            entries = [v for v in entries if not any(s <= v[0] and e >= v[1] and e-s > v[1]-v[0] for s, e, _ in entries)]
            names = list(dict.fromkeys(v[2] for v in entries))
            names = [name for name in names if not any(len(other) > len(name) and {n["name"] for n in REGION_ALIASES[name]} & {n["name"] for n in REGION_ALIASES[other]} for other in names)]
            if len(names) == 1:
                match = re.search(re.escape(names[0]), text)
                found(field, names[0], part, match)
            elif len(names) > 1:
                issue(LABELS[field] + "存在多个地点，需核对原文")
        if part.kind == "address" and not by_level and text.strip():
            issue("教学地区只有地标或未识别地名，需核对原文")

    @staticmethod
    def schedule(part, schedules, issue):
        text = part.text
        day_re = re.compile(r"(?:周|星期|礼拜)([一二三四五六日天1-7])(?![次节])")
        time_re = re.compile(r"(?P<period>上午|早上|下午|晚上|晚间|中午)?\s*(?P<h>\d{1,2})(?:[:.点](?P<m>\d{1,2})?)?\s*[-~—–至到]\s*(?P<h2>\d{1,2})(?:[:.点](?P<m2>\d{1,2})?)?(?:点)?")
        for clause in re.finditer(r"[^\n;；。]+", text):
            chunk = clause.group()
            days = list(day_re.finditer(chunk))
            times = list(time_re.finditer(chunk))
            if not days:
                continue
            if len(times) != 1:
                continue
            m = times[0]
            # Do not read 2-3h/次 as a clock interval.
            if re.match(r"\s*(?:小时|[hH]|次)", chunk[m.end():]):
                continue
            h, h2, minute, minute2 = int(m["h"]), int(m["h2"]), int(m["m"] or 0), int(m["m2"] or 0)
            period = m["period"]
            if not period and h <= 12 and h2 <= 12:
                continue
            if period in {"下午", "晚上", "晚间", "中午"}:
                h += 12 if h < 12 else 0
                h2 += 12 if h2 < 12 else 0
            if not (0 <= h <= 23 and 0 <= h2 <= 23 and minute < 60 and minute2 < 60 and (h, minute) < (h2, minute2)):
                issue("上课时间格式或跨日安排待确认")
                continue
            expanded = []
            for idx, day in enumerate(days):
                n = 7 if day[1] in "日天" else int(number(day[1]))
                if idx and re.fullmatch(r"\s*[-~—–至到]\s*", chunk[days[idx-1].end():day.start()]):
                    previous = expanded[-1]
                    if n <= previous:
                        issue("星期范围待确认")
                        expanded = []
                        break
                    expanded.extend(range(previous + 1, n + 1))
                else:
                    expanded.append(n)
            for n in dict.fromkeys(expanded):
                schedules.append(({"day": n, "start": f"{h:02}:{minute:02}", "end": f"{h2:02}:{minute2:02}"}, part.start + clause.start(), part.start + clause.end()))

    @staticmethod
    def learner_field_requirements(part,issue,manual_entries):
        # Explicit requests for a teacher remain requirements even when a
        # publisher put them under student information. Do not read the
        # learner's major, native language or test results as teacher facts.
        for m in re.finditer(r'(?:找|招聘|需要|要求)[^,，;；。!！\n]{0,50}?(?:老师|教师|教员|家教)',part.text):
            clause_start=max((part.text.rfind(c,0,m.start()) for c in ',，;；。!！\n'),default=-1)+1
            request=part.text[clause_start:m.end()]
            if re.search(r'不找|不需要|不要求|无需|不用',request):
                issue('学员情况内教员要求含否定条件，需核对原文')
                manual_entries.append(([request.strip()],part.start+clause_start,part.start+m.end()))
                continue
            if (PREFER.search(request) or re.match(r'\s*(?:优先|最好)',part.text[m.end():])) and not re.search(r'必须|仅限|只要|硬性',request):
                continue
            issue('学员情况内另有教员要求，需核对原文')
            manual_entries.append(([m.group()],part.start+m.start(),part.start+m.end()))
        # High-precision recovery for a misplaced numbered tutor specification.
        # A child's bilingual background alone never satisfies this role guard.
        if not (re.search(r'母语(?:是|为)?英[语文]',part.text) and re.search(r'中文流利',part.text)
                and re.search(r'讲题(?:目)?|讲知识点',part.text) and re.search(r'(?:能|会|用|以)中文安抚|安抚(?:孩子|小朋友)',part.text)):
            return
        numbered=list(re.finditer(r'(?<!\d)[1-9][.、)]\s*',part.text))
        if len(numbered)<2:return
        for i,m in enumerate(numbered):
            end=numbered[i+1].start() if i+1<len(numbered) else len(part.text)
            text=part.text[m.end():end]
            # Split off preferences within an otherwise hard requirement.
            for clause in re.finditer(r'[^,，;；。!！\n]+',text):
                value=clause.group().strip()
                if not value or PREFER.search(value):continue
                if not re.search(r'母语|中文流利|发音标准|中英切换|讲题|讲规则|安抚|解释知识点',value):continue
                start=part.start+m.end()+clause.start();finish=part.start+m.end()+clause.end()
                manual_entries.append(([value],start,finish))
                issue('学员情况内另有教员要求，需核对原文')

    @staticmethod
    def requirements(part, found, issue, add, manual_entries):
        # Clause-local preference markers never weaken another clause's hard rule.
        learner_context=False
        tier_text=re.sub(r'(?<!\d)(?:985\s*[,，]\s*211|211\s*[,，]\s*985)(?!\d)',lambda m:m.group().replace(',','、').replace('，','、'),part.text)
        for clause in re.finditer(r"[^#,，;；。\n]+", tier_text):
            text = clause.group()
            if re.fullmatch(r'[\s#🧸!！]*?(?:加急|新单|回收单|老家长(?:重新找|回购|很靠谱))[\s#🧸!！]*(?:(?:加急)[\s!！]*)*',text):continue
            local = Segment(part.kind, part.start + clause.start(), part.start + clause.end(), text)
            learner_lead=re.match(r'\s*(?:学生|学员|孩子)(?!证)',text)
            if learner_lead and not re.search(r'老师|教师|教员|要求|必须|需要|投递|证',text):
                learner_context=True
                gender=re.search(r'男生|男孩|女生|女孩',text)
                if gender:found('student_gender','女' if gender.group().startswith('女') else '男',local,gender,whole=True)
                continue
            if learner_context and re.fullmatch(r'\s*(?:基础(?:不太好|不好|差|一般|薄弱)|(?:成绩)?不及格|成绩(?:不好|较差|一般))\s*',text):
                continue
            learner_context=False
            requirement = re.search(r"教员要求|老师要求|教师要求|家教要求|男女不限|性别不限|不限男女|不要求|不一定|不限|不要|要求|必须|仅限|只要|需要|限[男女]|[男女](?:老师|家教|教师|教员)", text)
            if part.kind == "general" and requirement:
                local.start += requirement.start()
                text = text[requirement.start():]
                local.text = text
            mode = "prefer" if PREFER.search(text) and not re.search(r"必须|仅限|只要|硬性", text) else "must"
            handled = False
            patterns = [
                ("teacher_gender", r"男女不限|性别不限|不限男女|性别无要求|无性别要求|性别无所谓|(?:[男女](?:老师|教师|教员|家教)?\s*[/、或和]\s*[男女]|男女|女男)\s*(?:老师|教师|教员|家教|(?:在校)?大学生)(?:均可|都可|不限)?|男女(?:在校)?大学生|(?:仅?限|必须|只要)\s*[男女](?:生|老师|家教|教员|教师)?|[男女]\s*(?:(?:专职|在职)?(?:老师|家教|教员|教师)|(?:在校)?(?:大学生|本科生|研究生))(?:优先)?", None),
                ("teacher_type", r"(?:在校)?大学生(?:老师|教师|教员|家教)?|在职(?:教师|老师)|专职(?:教师|老师|教员|家教)|毕业生", None),
                ("education", r"博士|硕士|研究生|本科(?:及以上|以上|学历)?|大专(?:及以上|以上|学历)?", None),
                ("english_score", r"高考英语\s*(\d{2,3})\s*(?:分|\+|以上|及以上)?", None),
                ("english_level", r"英[语文](?:水平[高好]|发音(?:要)?标准|基础好|好)|(?:英语)?四六级|英语[四六]级|[四六]级(?:以上)?", None),
                ("teaching_language", r"全英(?:文|语)?(?:授课|教学)?|中文授课", None),
                ("experience", r"(" + NUMBER + r")\s*年(?:以上|及以上)?(?:的)?(?:教学|家教|授课|辅导)?经验", None),
            ]
            if part.kind == "teacher" and not re.search(patterns[0][1], text):
                gender = re.search(r"(?<![\u4e00-\u9fff])([男女])(?:生|孩)?(?![\u4e00-\u9fff])|(?:优先|最好(?:是)?)([男女])(?:生|孩)?|([男女])(?:生)?(?=优先|即可|均可|最好|$)", text)
                if gender:
                    # Include the template label so evidence identifies the role.
                    add("teacher_gender", gender[1] or gender[2] or gender[3], part.header_start, part.start + clause.end(), mode)
                    handled = True
            for field, pattern, _ in patterns:
                for m in re.finditer(pattern, text):
                    if part.kind == "general" and field not in {"teacher_gender", "english_score"} and not re.search(r"要求|需要|必须|限|老师|教师|家教|优先|最好", text):
                        continue
                    if field=='teacher_gender' and part.kind!='teacher' and re.search(r'大学生|本科生|研究生',m.group()) and not requirement:
                        continue
                    raw = m.group()
                    if field in {"teacher_type", "education"} and RATE_RE.match(text[m.end():].lstrip()):
                        handled = True
                        continue
                    if re.search(r"不要求|不限|无需|不要|不一定|非|非得|不是|不$", text[max(0, m.start()-5):m.start()]):
                        if not (field == "teacher_gender" and "不限" in raw):
                            issue("存在否定或排除要求，需核对原文")
                            handled = True
                            continue
                    value = raw
                    if field == "teacher_gender":
                        value = "不限" if re.search(r'不限|无要求|无所谓',raw) or ('男' in raw and '女' in raw) else "女" if "女" in raw else "男"
                    elif field == "teacher_type":
                        value = "在校大学生" if "大学生" in raw else "专职教师" if raw.startswith('专职') else "在职教师" if raw.startswith('在职') else raw
                    elif field == "education":
                        value = "硕士" if raw == "研究生" else "本科" if raw.startswith("本科") else "专科" if raw.startswith("大专") else raw
                    elif field in {"english_score", "experience"}:
                        value = number(m[1])
                    elif field == "teaching_language":
                        value = "中文" if raw.startswith("中文") else "全英文"
                    if field=='teacher_gender' and part.kind=='teacher':
                        add(field,value,part.header_start,local.end,mode)
                    else:
                        found(field, value, local, m, mode, whole=True)
                    handled = True
            tiers = [m.group() for m in re.finditer(r"985|211|双一流|双非一本|双非|一本|二本|普通本科", text)]
            if tiers and (part.kind == "teacher" or re.search(r"要求|限|老师|家教|优先|最好|教员", text)):
                if re.search(r"不要|不收|不接受|非985|非211|除外", text):
                    issue("院校类别存在排除条件，需核对原文")
                else:
                    if "双非一本" in tiers or re.search(r"且|同时|都要", text):
                        issue("院校类别同时要求多类条件，需核对原文")
                    else:
                        found("school_tier", list(dict.fromkeys(tiers)), local, clause, mode, whole=True)
                handled = True
            if (part.kind == "teacher" or requirement) and text.strip():
                # Unknown qualifiers remain visible and block automatic acceptance.
                remainder = text
                soft=r'有(?:相关)?(?:家教|辅导|教学|授课)?经验|(?:家教|辅导|教学|授课)?经验丰富|有丰富(?:家教|辅导|教学|授课)?经验|有耐心|耐心|认真负责|责任心强|有责任心|负责任|负责|责任心|认真|严厉(?:一点|一些)?|教学有方法|需要经常学习复盘'
                soft_present=bool(re.search(soft,text))
                hard_soft=soft_present and bool(re.search(r'必须|仅限|硬性|务必',text))
                remainder=re.sub(soft,'',remainder)
                if tiers:
                    remainder=re.sub(r'(?:学校|院校|学历)?(?:就行|即可|就可以)','',remainder)
                for _, pattern, _ in patterns:
                    remainder = re.sub(pattern, "", remainder)
                if part.kind == "teacher":
                    remainder = re.sub(r"男生|女生|(?<![\u4e00-\u9fff])[男女](?![\u4e00-\u9fff])", "", remainder)
                remainder = re.sub(r"985|211|双一流|双非一本|双非|一本|二本|普通本科|优先|最好|优选|更希望|可以|需要|要求|必须|仅限|限|有经验|经验丰富|负责任|负责|责任心|认真|耐心|教学有方法|经常学习复盘|有|的|且|及|以上|男女不限|[\s、/和或()]+", "", remainder)
                if hard_soft or re.search(r"[\u4e00-\u9fffA-Za-z0-9]", remainder) or not (handled or soft_present):
                    if hard_soft or not re.fullmatch(r"[\s、]*(?:有经验|经验丰富|负责任|负责|责任心|认真|耐心|教学有方法|需要经常学习复盘|有|的|且|及|[\s、])+", text):
                        if mode == "must":
                            issue("教员要求含未结构化条件，需核对原文")
                            manual_entries.append(([text.strip()],local.start,local.end))
                        else:
                            add("notes", text.strip(), local.start, local.end, "prefer")
