"""Deterministic text normalization with original-source offsets."""
import re
import unicodedata

NUMBER = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+|[零〇一二两三四五六七八九十百千万]+(?:点[零一二三四五六七八九]+)?|半)"
DIGITS = dict(zip("零〇一二两三四五六七八九", [0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9]))


def number(value):
    if value == "半":
        return 0.5
    try:
        return float(value.replace(",", ""))
    except ValueError:
        pass
    whole, _, decimal = value.partition("点")
    total = section = digit = 0
    for char in whole:
        if char in DIGITS:
            digit = DIGITS[char]
        elif char in "十百千":
            section += (digit or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            digit = 0
        elif char == "万":
            total += (section + digit) * 10000
            section = digit = 0
    result = total + section + digit
    if decimal:
        result += float("0." + "".join(str(DIGITS[c]) for c in decimal))
    return result


class Source:
    def __init__(self, raw):
        self.raw = raw
        self.offsets = []
        chars = []
        for index, char in enumerate(raw):
            normalized = unicodedata.normalize("NFKC", char)
            chars.append(normalized)
            self.offsets.extend([index] * len(normalized))
        self.text = "".join(chars)

    def evidence(self, start, end):
        return self.raw[self.offsets[start]:self.offsets[end - 1] + 1]


def quoted_numbers(text):
    text = Source(text).text
    return [number(m.group()) for m in re.finditer(NUMBER, text)]
