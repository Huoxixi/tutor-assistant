import re


def redact(text: str, sender: str = "", *, preserve_street_numbers: bool = False) -> str:
    replacements = []

    def hide(match):
        replacements.append(match.group(0))
        return f"【隐私{len(replacements)}】"

    if sender and sender != "未知发布者":
        text = text.replace(sender, "【发布者】")
    # Keep the street/district used for matching, hide only the precise door number.
    if not preserve_street_numbers:
        text = re.sub(r"([\u4e00-\u9fff]{1,30}(?:路|街|大道|巷))\d+(?:号|弄)(?:\d+(?:号|室|栋|幢))*", lambda m: m[1] + "【门牌已隐去】", text)
    patterns = [
        r"(?i)https?://\S+", r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
        r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d(?:[- ]?\d){8}(?!\d)",
        r"(?<!\d)\d{17}[0-9Xx](?!\d)",
        r"(?i)(?:微信号?|wx|wechat|QQ|电话|手机|联系(?:方式|人)?)[：:\s]*[A-Za-z0-9_+\-]{5,}",
        r"\d+(?:栋|幢|单元|室|门牌)(?:\d+(?:栋|幢|单元|室))*",
        r"(?:姓名|学生姓名|联系人)[：:\s]*[\u4e00-\u9fff]{2,4}",
        r"[赵钱孙李周吴郑王冯陈蒋沈韩杨朱秦许何吕施张孔曹严华金魏陶姜谢邹苏潘葛范彭鲁马方任袁柳唐罗梁宋黄徐高林刘郭胡曾肖田董雷钟邱杜叶程傅余汪邓蔡贾丁夏薛姚顾孟江白廖石邵尹崔康毛邢龚][\u4e00-\u9fff]?(?:同学|先生|女士|妈妈|爸爸|老师)",
    ]
    for pattern in patterns:
        text = re.sub(pattern, hide, text)
    return text
