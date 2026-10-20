"""Conservative order boundaries, with identifiers and original source offsets."""
import re
from dataclasses import dataclass
from .text_rules import Source
from .templates import headers as field_headers, DECORATION
from .regions import REGION_INDEX, aliases

DISTRICT_PREFIXES={alias for node in REGION_INDEX.values() if node['level']==3 for alias in aliases(node['name'])}

TOKEN = r"(?:[A-Za-z\u4e00-\u9fff]{1,20}(?:[ \t]+[A-Za-z\u4e00-\u9fff]{1,12}){0,2}[ \t]*[-_]?[ \t]*\d{1,14}|\d{6,14})[A-Za-z]{0,4}(?:[-_]\d{1,4})?"
PREFIX = r"(?:家教|订单)?(?:单号|编号)|订单号"
HEADER = re.compile(r"(?m)^[ \t\u3000\"'“”‘’]*(?:(?:"+DECORATION+r"|[#*•·]|\[[^\]\n]{1,8}\])[ \t]*)*(?:[\u4e00-\u9fff]{2,8}线下[ \t]*#)?(?:(?:【|\[)?(?:" + PREFIX + r")[】\]]?\s*[:：]?\s*)?[【\[]?(?P<ref>" + TOKEN + r")[】\]]?(?:号家教)?[^\S\n\"']*[\"']?[:：]?[^\S\n]*")
# A forward may concatenate numbered orders with semicolons instead of lines.
# Keep the separator requirement so prices, phone numbers and prose do not
# become order starts merely because they contain digits.
ORDER_HEADER = re.compile(HEADER.pattern.replace(r"(?m)^", r"(?m)(?:^|(?<=[;；|]))"))
EXPLICIT = re.compile(r"(?:【|\[)?(?:" + PREFIX + r")[】\]]?\s*[:：]?\s*[【\[]?(?P<ref>" + TOKEN + r")[】\]]?")
STUDENT = re.compile(r"(?m)^[ \t]*(?:【(?:学员|学生|学生情况|学员情况|学生年级)】\s*:?|(?:学员|学生情况|学员情况|学生年级)\s*:)")
ORDER_SIGNAL = re.compile(r"学员|科目|课酬|薪资|教员要求|[初高][一二三123]|[一二三四五六1-6]年级|\d\s*(?:元/|/h)", re.I)


@dataclass(frozen=True)
class Block:
    reference: str | None
    text: str


def valid_reference(ref, explicit=False):
    if re.search(r"电话|微信|联系|手机|课酬|薪资|地址|年级|高考|费用", ref):
        return False
    if re.match(r'每|一次|一周|课时|时薪|周[一二三四五六日天]',ref):
        return False
    if re.search(r'距离|步行|打车|车程',ref) or re.search(r'\d(?:min|km|cm|分钟|小时)$',ref,re.I):
        return False
    if not explicit and re.search(r'(?:路|街|巷|弄|大道)\d',ref):
        return False
    if ref.isdigit():
        if re.fullmatch(r"1[3-9]\d{9}|\d{17}[0-9Xx]", ref):
            return False
        if not explicit and re.fullmatch(r"(?:19|20)\d{6}", ref):
            return False
    return True


def reference_in_update(text):
    source = Source(text)
    for marker,kind,_ in field_headers(source.text):
        token=re.match(r'\s*[【\[「〔(]?(?P<ref>'+TOKEN+r')',source.text[marker.end():]) if kind=='reference' else None
        if token and valid_reference(token['ref'],True):
            return source.evidence(marker.end()+token.start('ref'),marker.end()+token.end('ref'))
    explicit = EXPLICIT.search(source.text)
    if explicit and valid_reference(explicit['ref'], True):
        return source.evidence(explicit.start('ref'), explicit.end('ref'))
    m = re.search(r"(?<![A-Za-z0-9])(?P<ref>" + TOKEN + r")(?=\s*(?:已出|已接|已找到|已招到|取消|补充|更正|改为|调整))", source.text)
    if m and valid_reference(m['ref']):
        return source.evidence(m.start('ref'), m.end('ref'))
    return None


def split_orders(text: str) -> list[Block]:
    source = Source(text)
    fields=field_headers(source.text)
    references=[]
    for marker,kind,_ in fields:
        token=re.match(r'\s*[【\[「〔(]?(?P<ref>'+TOKEN+r')',source.text[marker.end():]) if kind=='reference' else None
        if token and valid_reference(token['ref'],True):
            references.append((marker,token))
    def body_between(start,end):
        kinds={kind for marker,kind,_ in fields if start<=marker.start()<end and kind!='reference'}
        return bool(kinds & {'student','student_subject','subject'}) and bool(kinds & {'pay','address'})
    # Recognize complete repeated templates whose identifiers are footers.
    # A leading unnumbered order followed by a normal header is not this format.
    if references and body_between(0,references[0][0].start()) and not body_between(references[-1][0].end()+references[-1][1].end(),len(source.text)) and all(body_between(references[i-1][0].end()+references[i-1][1].end() if i else 0,marker.start()) for i,(marker,_) in enumerate(references)):
        result=[];start=0
        for i,(marker,token) in enumerate(references):
            token_end=marker.end()+token.end()
            next_ref=references[i+1][0].start() if i+1<len(references) else len(source.text)
            newline=source.text.find('\n',token_end,next_ref)
            end=len(source.text) if i==len(references)-1 else (newline+1 if newline>=0 else token_end)
            result.append(Block(source.evidence(marker.end()+token.start('ref'),marker.end()+token.end('ref')),source.evidence(start,end).strip()))
            start=end
        return result
    headers = []
    header_ranges=[]
    for m in ORDER_HEADER.finditer(source.text):
        explicit = bool(re.search(PREFIX, m.group()))
        if not valid_reference(m['ref'], explicit):
            continue
        rest = source.text[m.end():].split('\n', 1)[0]
        if rest and not re.match(r"【|\[|#|[，,;；(]|(?:学员|学生|科目|[一二三四五六1-6]年级|小学|初中|高中|幼儿园|学前班|初[一二三123]|高[一二三123]|补充|已出|已接|已找到|回收)", rest):
            continue
        if rest and not explicit and rest[0].isalnum() and not ORDER_SIGNAL.search(rest):
            continue
        if not explicit and not re.match(r"[\u4e00-\u9fff]{1,8}[A-Za-z]{0,4}\d{6,14}", m['ref']) and not ORDER_SIGNAL.search(source.text[m.end():m.end()+500]) and not re.match(r"已出|已接|已找到|补充", rest):
            continue
        start=m.start()
        prelude=re.search(r'(?m)^[ \t]*#[\u4e00-\u9fff]{2,8}(?:回收单|新单)?[ \t]*\n$',source.text[:start])
        if prelude:start=prelude.start()
        headers.append((start, source.evidence(m.start('ref'), m.end('ref'))))
        header_ranges.append(m.span())
    explicit_markers=list(EXPLICIT.finditer(source.text))
    for i,m in enumerate(explicit_markers):
        if any(start <= m.start() < end for start,end in header_ranges):
            continue
        end=explicit_markers[i+1].start() if i+1<len(explicit_markers) else len(source.text)
        if valid_reference(m['ref'],True) and len(ORDER_SIGNAL.findall(source.text[m.end():end]))>=2:
            headers.append((m.start(),source.evidence(m.start('ref'),m.end('ref'))))
            header_ranges.append(m.span())
    for marker,kind,_ in fields:
        if kind!='reference' or any(start<=marker.start()<end for start,end in header_ranges):
            continue
        token=re.match(r'\s*[【\[「〔(]?(?P<ref>'+TOKEN+r')',source.text[marker.end():])
        if token and valid_reference(token['ref'],True):
            ref=source.evidence(marker.end()+token.start('ref'),marker.end()+token.end('ref'))
            headers.append((marker.start(),ref))
    # A selected-message label may prefix a publisher before an explicit
    # NNNNNN号家教 heading. Preserve that entire line, but recover its ID.
    for marker in re.finditer(r'(?m)(?<![A-Za-z0-9])(?P<ref>\d{6,14})号家教[ \t]*(?=\n|$)',source.text):
        line_start=source.text.rfind('\n',0,marker.start())+1
        if any(start<=marker.start()<end for start,end in header_ranges):continue
        if valid_reference(marker['ref'],True) and len(ORDER_SIGNAL.findall(source.text[marker.end():marker.end()+500]))>=2:
            if not any(start==line_start for start,_ in headers):
                headers.append((line_start,source.evidence(marker.start('ref'),marker.end('ref'))))
    headers.sort(key=lambda header:header[0])
    # Repeated mentions of the current reference do not create another order.
    # NFKC is used only for comparison; identifiers and blocks retain raw text.
    numbered=[]
    for header in headers:
        if not numbered or Source(numbered[-1][1]).text != Source(header[1]).text:
            numbered.append(header)
    headers=numbered
    result = []
    positions = headers or [(0, None)]
    if headers and headers[0][0] and len(ORDER_SIGNAL.findall(source.text[:headers[0][0]])) >= 2:
        positions = [(0, None), *positions]
    numbered_message=len({Source(value).text for _,value in headers})>=2
    for i, (start, ref) in enumerate(positions):
        end = positions[i+1][0] if i+1 < len(positions) else len(source.text)
        if ref is not None and numbered_message:
            # In a numbered multi-order message, repeated student/course fields
            # belong to this identifier until the next order header. Do not
            # turn one numbered order into additional anonymous orders.
            result.append(Block(ref,source.evidence(start,end).strip()))
            continue
        starts=[(m,kind,label) for m,kind,label in fields if start<=m.start()<end and kind in {'student','student_subject'}]
        def address_start(marker,active_start):
            boundary=marker.start()
            pay_markers=[m for m,k,_ in fields if active_start<=m.start()<boundary and k=='pay']
            if pay_markers:
                pay_end=pay_markers[-1].end()
                gap=re.search(r'\n[ \t]*\n',source.text[pay_end:boundary])
                if gap:
                    tail=source.text[pay_end+gap.end():boundary].lstrip()
                    if '家教' in tail.split('\n',1)[0] and any(tail.startswith(alias) for alias in DISTRICT_PREFIXES):
                        boundary=pay_end+gap.start()
            return boundary
        addresses=[m for m,k,_ in fields if start<=m.start()<end and k=='address']
        boundaries=set();address_boundaries=[];address_active=start
        for index,marker in enumerate(addresses[1:],1):
            before={k for m,k,_ in fields if address_active<=m.start()<marker.start()}
            next_address=addresses[index+1].start() if index+1<len(addresses) else end
            after={k for m,k,_ in fields if marker.start()<=m.start()<next_address}
            previous_pay=[m for m,k,_ in fields if address_active<=m.start()<marker.start() and k=='pay']
            body_already_started=bool(previous_pay and any(k in {'subject','student','student_subject'} and previous_pay[-1].end()<=m.start()<marker.start() for m,k,_ in fields))
            boundary=address_start(marker,address_active)
            # Address order by itself is insufficient: many valid templates
            # place the address after their course/grade. A separate address
            # anchor needs an explicit new-order introduction as well.
            if boundary<marker.start() and not body_already_started and 'pay' in before and before & {'subject','student','student_subject'} and 'pay' in after and after & {'subject','student','student_subject'}:
                address_boundaries.append(boundary);boundaries.add(boundary);address_active=boundary
        active_start=start
        for index,(marker,kind,label) in enumerate(starts[1:],1):
            previous,previous_kind,previous_label=starts[index-1]
            active_start=max([active_start,*[b for b in address_boundaries if b<=marker.start()]])
            if previous.start()<active_start:continue
            before=[k for m,k,_ in fields if active_start<=m.start()<marker.start()]
            next_start=starts[index+1][0].start() if index+1<len(starts) else end
            after=[k for m,k,_ in fields if marker.start()<=m.start()<next_start]
            # Compound grade/course and later student descriptions can belong
            # to one order; a new order needs independent course evidence.
            if {'subject','student_subject','pay'} & set(before) and {'subject','student_subject'} & set(after) and (label==previous_label or 'pay' in after):
                boundary=marker.start()
                addresses=[m for m,k,_ in fields if active_start<m.start()<marker.start() and k=='address']
                if addresses:
                    last_address=addresses[-1]
                    preceding={k for m,k,_ in fields if active_start<=m.start()<last_address.start()}
                    if 'pay' in preceding and preceding & {'student','student_subject','subject'}:
                        boundary=address_start(last_address,active_start)
                boundaries.add(boundary)
                active_start=boundary
        cuts = [start, *sorted(boundaries), end]
        for j in range(len(cuts)-1):
            raw = source.evidence(cuts[j], cuts[j+1]).strip() if cuts[j+1] > cuts[j] else ''
            if raw:
                block_ref=ref if j==0 else None
                # A wrapped identifier at the end labels the preceding order;
                # it does not create an empty extra order or supply an address.
                if block_ref is None:
                    footer=re.search(r'(?:^|\n)[ \t]*(?:[\u4e00-\u9fff]{2,8})?[【\[](?P<ref>'+TOKEN+r')[】\]][^\n]*$',Source(raw).text)
                    if footer and valid_reference(footer['ref']) and len(ORDER_SIGNAL.findall(Source(raw).text[:footer.start()]))>=2:
                        raw_source=Source(raw)
                        block_ref=raw_source.evidence(footer.start('ref'),footer.end('ref'))
                result.append(Block(block_ref, raw))
    return result or [Block(None, text)]
