"""Local field-header taxonomy; delimiters never change source evidence."""
import re

GROUPS={
    'student_notes':'现阶段成绩|目前成绩|当前成绩|学生成绩|学员成绩',
    'student':'学员|学员情况|学生|学生情况|年级|学员信息|学生信息|学生年级|孩子情况|性别|年级性别|年级及性别|学生性别|学员性别|孩子年级|年级与性别|小孩性别和个性|信息|学习情况|情况',
    'student_subject':'年级科目|年级及科目|年级与科目|年级和科目|科目年级|学生年级科目',
    'subject':'科目|辅导科目|补习科目|授课科目|教学科目|授课内容|辅导内容|补习内容|学习内容|补课科目|内容',
    'pay':'课酬|薪资|报酬|费用|薪酬|课时费|工资|时薪|价格|待遇|预算|报价|课费报酬|课时报酬|薪资待遇|老师薪水|老师报酬|补习薪资|薪资价格|课费|薪水|辅导报酬|辅导薪资|报酬标准|时薪范围|薪酬待遇',
    'time':'时间|上课时间|授课时间|辅导时间|频率|时长|课时|课次|上课频率|时间次数|时间安排|补习时间|上课时间段|试课时间|课时安排|授课安排|每次时长|每周次数|一周次数|上课次数|每周几次|时段|什么时候开始',
    'address':'地址|上课地址|授课地址|地点|上课地点|授课地点|区域|位置|教学地址|辅导地点|家庭地址|学员地址|联系地址|补习地址|家教地址|大概位置|辅导地址|教学地点|补课地址|补习地点|家庭住址|学生地址|住址',
    'teacher':'教员要求|老师要求|教师要求|家教要求|教员条件|要求|招聘要求|对老师的要求|对老师要求|家长对老师要求|老师性别要求|老师性别|教员性别|教师性别|大概要求|老师|教员|对教员要求|对教师要求',
    'contact':'联系|联系方式|联系人|微信|电话|报名方式|学生姓名|学员姓名|姓名|联系电话|家长电话',
    'notes':'备注|说明|注意事项|其他说明|温馨提示|其他|补充说明',
    'reference':'编号|单号|订单编号|家教编号|订单号',
}
LABEL_KIND={label:kind for kind,labels in GROUPS.items() for label in labels.split('|')}
LABEL_PATTERN='|'.join(re.escape(label) for label in sorted(LABEL_KIND,key=len,reverse=True))
BRACKET_RE=re.compile(r'[【\[「〔(](?P<label>[\u4e00-\u9fff]{2,14})\s*:?[】\]」〕)]\s*:?\s*')
DECORATION=r'[\U0001F300-\U0001FAFF\u2600-\u27BF\ufe0f\u200d]'
PLAIN_RE=re.compile(r'(?<![\u4e00-\u9fff])(?P<label>'+LABEL_PATTERN+r')[ \t]*(?:'+DECORATION+r'[ \t]*)*:\s*')


def classify(label):
    label=label.strip(' :：')
    if label in LABEL_KIND:return LABEL_KIND[label]
    # Bounded header semantics, never applied to arbitrary prose.
    if re.fullmatch(r'(?:学生|学员|辅导|授课|上课|教学|补课|补习|家庭|大概|家教)?(?:地址|地点|位置|住址)',label):return 'address'
    if re.fullmatch(r'(?:家长)?(?:对)?(?:老师|教员|教师)(?:的)?(?:要求|条件|性别要求)',label):return 'teacher'
    if re.fullmatch(r'(?:年级)(?:和|与|及)?科目',label):return 'student_subject'
    if re.fullmatch(r'[\u4e00-\u9fff]{2,8}家教单',label):return 'reference'
    return None


def numbered_headers(text):
    matches=list(re.finditer(r'(?m)^[ \t]*(?P<label>[1-6])[、.)][ \t]*',text))
    if len(matches)!=6 or [m['label'] for m in matches]!=list('123456'):
        return []
    values=[text[m.end():matches[i+1].start() if i<5 else len(text)] for i,m in enumerate(matches)]
    if not re.search(r'年级|[初高][一二三123]|[小][一二三四五六1-6]|岁|幼儿园|学前',values[1]):return []
    if not re.search(r'周|星期|小时|点',values[3]):return []
    if not re.search(r'(?:/|每)\s*(?:[hH]|小时|时|次|节|月)',values[5]):return []
    return [(m,kind,'序号'+m['label']) for m,kind in zip(matches,['address','student','subject','time','teacher','pay'])]


def teacher_role_evidence(evidence,text):
    """Accept explicit teacher fields and validated six-row order templates."""
    from .text_rules import Source
    proof=Source(evidence).text
    for marker,kind,_ in headers(proof):
        if kind=='teacher' and not re.match(r'\s*(?:学生|学员|孩子)(?!证)',proof[marker.end():]):
            return True
    source=Source(text)
    numbered=numbered_headers(source.text)
    if numbered:
        marker=numbered[4][0];end=numbered[5][0].start()
        return evidence in source.evidence(marker.start(),end)
    return False


def headers(text):
    found=[]
    for match in BRACKET_RE.finditer(text):
        kind=classify(match['label'])
        if kind:
            found.append((match,kind,match['label']))
    occupied=[m.span() for m,_,_ in found]
    for match in PLAIN_RE.finditer(text):
        if not any(start<=match.start()<end for start,end in occupied):
            found.append((match,LABEL_KIND[match['label']],match['label']))
    numbered=numbered_headers(text)
    if numbered:
        found=[entry for entry in found if entry[0].start()<numbered[0][0].start()]+numbered
    return sorted(found,key=lambda item:item[0].start())
