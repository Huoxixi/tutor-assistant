"""Subject lexicon, role scoping, exclusions, focus clauses and partial coverage."""
import re

SUBJECTS = {s: s for s in ('全科', '作业辅导', '语文', '数学', '英语', '物理', '化学', '生物', '历史', '地理', '政治', '科学', '奥数', '编程', '钢琴', '美术', '法语', '日语', '雅思', '托福', '书法', '围棋', '计算机', '心理学','琵琶','声乐','羽毛球')}
SUBJECTS.update({'英文':'英语', '全学科':'全科', '全科辅导':'全科', '作业陪读':'作业辅导', '陪写作业':'作业辅导', '陪读作业':'作业辅导', '数理化':'数学、物理、化学', '语数英':'语文、数学、英语', '数英':'数学、英语', '语数':'语文、数学', '理化':'物理、化学', '政史地':'政治、历史、地理', '理化生':'物理、化学、生物'})
SUBJECTS.update({'辅导作业':'作业辅导','语数外':'语文、数学、外语','新概念英语':'英语','国学':'国学','中国舞':'中国舞','古典舞':'古典舞'})
SUBJECTS.update({'新概念'+n:'英语' for n in '1234一二三四'})
ABBREVIATIONS = {'语':'语文','数':'数学','英':'英语','物':'物理','化':'化学','生':'生物','史':'历史','地':'地理','政':'政治'}
SUBJECT_RE = re.compile('|'.join(re.escape(s)+(r'(?![0-9一二三四五六七八九十])' if s.startswith('新概念') and s!='新概念英语' else '') for s in sorted(SUBJECTS, key=len, reverse=True)))
FOCUS = re.compile(r'侧重|重点|主要(?:辅导|补习)?')


def recognize(text, explicit=False, ignore_spans=()):
    scoped=list(text)
    for request in re.finditer(r'(?:找|招聘|需要|要求)[^,，;；。\n]{0,40}?(?:专业|英专)[^,，;；。\n]{0,20}?(?:老师|教师|教员|家教)',text):
        scoped[request.start():request.end()]=' '*(request.end()-request.start())
    scoped=''.join(scoped)
    stop = re.search(r'教员|教师|老师|家教要求|要求|限[男女]|(?:地址|时间|课酬|薪资)[:：]', scoped)
    relevant = scoped[:stop.start()] if stop else scoped
    entries, focus, issues, covered = [], [], [], []
    matches = list(SUBJECT_RE.finditer(relevant))
    if explicit:
        abbreviation_text=list(relevant)
        for m in matches:
            abbreviation_text[m.start():m.end()]=' '*(m.end()-m.start())
        for m in re.finditer(r'(?<![\u4e00-\u9fff])([语数英物化生史地政]{1,10})(?![\u4e00-\u9fff])', ''.join(abbreviation_text)):
            if not any(a.start() <= m.start() < a.end() for a in matches):
                matches.append(m)
    all_subjects = any(m.group() in {'全科', '全学科', '全科辅导'} for m in matches)
    for m in sorted(matches, key=lambda x:x.start()):
        token = m.group()
        before, after = relevant[max(0,m.start()-12):m.start()], relevant[m.end():m.end()+8]
        covered.append((m.start(),m.end()))
        if token == '英文' and (re.search(r'全$|教材|授课|教学', before) or re.match(r'教材|授课|教学|水平|发音', after)):
            continue
        if re.search(r'(?:不补|不教|无需|不需要|排除|不要|不含|除外)\s*$', before) or re.match(r'\s*(?:不补|不教|除外|不需要)',after):
            if all_subjects: issues.append('全科包含排除科目，需核对范围')
            continue
        if all_subjects and token == '作业辅导' and re.search(r'[（(][^）)]*$', relevant[:m.start()]):
            continue
        value = SUBJECTS[token].split('、') if token in SUBJECTS else [ABBREVIATIONS[c] for c in token]
        entry = (value,m.start(),m.end())
        if FOCUS.search(relevant[:m.start()]) or re.match(r'\s*(?:为主|为重点)',after): focus.append(entry)
        else: entries.append(entry)
    if not entries and focus:
        entries = list(focus)
    elif not all_subjects:
        entries.extend(focus)
    if re.search(r'或|任选|二选一|选一', relevant) and len({v for vals,_,_ in entries for v in vals}) > 1:
        issues.append('科目为备选关系，需确认实际辅导科目')
    if explicit:
        residual = list(relevant)
        for start,end in ignore_spans:
            residual[start:min(end,len(residual))]=' '*max(0,min(end,len(residual))-start)
        for start,end in covered:
            residual[start:end] = ' '*(end-start)
        residual = ''.join(residual)
        residual=re.sub(r'[（(]\s*[一二两三四五六七八九十0-9]+\s*(?:位|名)(?:学员|学生)?\s*[）)]','',residual)
        known={value for vals,_,_ in [*entries,*focus] for value in vals}
        if '英语' in known:
            residual=re.sub(r'语法|词汇|单词|阅读技巧|阅读理解|英语启蒙|启蒙|背诵|背|新概念\d*|还可以|零基础','',residual)
        if '语文' in known:
            residual=re.sub(r'文言文|现代文|作文|阅读技巧|阅读理解|背诵','',residual)
        # 文科/理科 is a scope label only when an explicit parenthesized list
        # follows it. Unknown subjects inside that list still survive below.
        residual = re.sub(r'(?:文科|理科)(?=\s*[（(])','',residual)
        if ignore_spans:
            residual=re.sub(r'男学员|女学员|男孩子|女孩子|男生|女生|男孩|女孩|[男女]|学生|学员|[一二两三四五六七八九十0-9]+岁|一对[一二两三四五六七八九十0-9]+','',residual)
        residual = re.sub(r'[（(](?:教材[^）)]*|全英文[^）)]*)[）)]', '', residual)
        residual = re.sub(r'不需要|不补|不教|无需|排除|不要|不含|除外|只补|只教|侧重|为重点|为主|重点|主要|辅导|补习|教学|授课|作业|查漏补缺|同步|基础|提升|巩固|成绩|分数|[\d\s+、,，;；/（()）:：和及与-]+', '', residual)
        if re.search(r'[\u4e00-\u9fffA-Za-z]', residual):
            issues.append('科目含未识别内容，需核对完整范围')
    return entries, focus, list(dict.fromkeys(issues))
