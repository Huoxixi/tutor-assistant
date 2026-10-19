import json
import re
from pathlib import Path

DATA_PATH = Path(__file__).parent / "assets" / "regions.json"
REGIONS = json.loads(DATA_PATH.read_text(encoding="utf-8")) if DATA_PATH.exists() else {"provinces": []}
REGION_INDEX = {}
for province in REGIONS["provinces"]:
    REGION_INDEX[province["code"]] = {"name": province["name"], "level": 1, "path": [province["code"]]}
    for city in province["children"]:
        REGION_INDEX[city["code"]] = {"name": city["name"], "level": 2, "path": [province["code"], city["code"]]}
        for county in city["children"]:
            REGION_INDEX[county["code"]] = {"name": county["name"], "level": 3, "path": [province["code"], city["code"], county["code"]]}


def aliases(name):
    short = re.sub(r"(特别行政区|壮族自治区|回族自治区|维吾尔自治区|自治区|新区|省|市|区|县)$", "", name)
    return {name, short} if len(short) >= 2 else {name}


def administrative_name_in_text(name, text):
    # 高青路 is a road, not evidence for 高青县. Likewise 南京路 is not 南京市.
    for alias in aliases(name):
        if alias not in text:
            continue
        for found in re.finditer(re.escape(alias), text):
            if not re.match(r"(?:市|省)?(?:支路|路|街|大道|巷|站|地铁|线|大学|学校|学院|大厦|小区|新村|大桥|寺)", text[found.end():]):
                return True
    return False


def infer_location(facts):
    texts = {key: str(facts.get(key, {}).get("value") or "") for key in ["province", "city", "area"]}
    combined = " ".join(texts.values())
    if not combined.strip():
        return []
    # Explicit higher levels disambiguate repeated district names.
    context = {}
    for level, field in [(1, "province"), (2, "city")]:
        context[level] = {code for code, node in REGION_INDEX.items() if node["level"] == level and administrative_name_in_text(node["name"], texts[field])}
    for level in [3, 2, 1]:
        candidates = []
        for code, node in REGION_INDEX.items():
            if node["level"] != level or node["name"] in {"市辖区", "县", "省直辖县", "市辖县"}:
                continue
            if not administrative_name_in_text(node["name"], combined):
                continue
            if any(context[higher] and not set(node["path"]).intersection(context[higher]) for higher in [1, 2] if higher < level):
                continue
            candidates.append(code)
        if candidates:
            return candidates
    return []


def match_location(facts, selected_codes):
    candidates = infer_location(facts)
    return match_location_codes(candidates, selected_codes)


def match_location_codes(candidates, selected_codes):
    if not candidates:
        return None
    outcomes = []
    for code in candidates:
        path = REGION_INDEX[code]["path"]
        if any(selected in path for selected in selected_codes):
            outcomes.append(True)
        elif any(code in REGION_INDEX[selected]["path"] for selected in selected_codes):
            outcomes.append(None)  # Known city/province, but district omitted.
        else:
            outcomes.append(False)
    if all(outcome is True for outcome in outcomes):
        return True
    if all(outcome is False for outcome in outcomes):
        return False
    return None
