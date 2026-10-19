"""Convert the attributed Vant area dataset into a self-contained local tree."""
import hashlib
import json
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "app" / "assets"
raw = (root / "regions-source.ts").read_text(encoding="utf-8-sig")
sections = {}
for key in ["province_list", "city_list", "county_list"]:
    section = re.search(key + r":\s*\{([^}]+)\}", raw)
    if not section:
        raise SystemExit("Missing data section: " + key)
    sections[key] = dict(re.findall(r"(\d{6}):\s*'([^']+)'", section[1]))
provinces = []
for code, name in sections["province_list"].items():
    cities = []
    for city_code, city in sections["city_list"].items():
        if city_code[:2] != code[:2]:
            continue
        counties = [{"code": c, "name": n} for c, n in sections["county_list"].items() if c[:4] == city_code[:4]]
        cities.append({"code": city_code, "name": city, "children": counties})
    provinces.append({"code": code, "name": name, "children": cities})
result = {"provinces": provinces, "source": "https://github.com/youzan/vant/tree/main/packages/vant-area-data", "license": "MIT", "downloaded_at": "2026-10-18", "sha256": hashlib.sha256(raw.encode()).hexdigest()}
(root / "regions.json").write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(json.dumps({"provinces": len(provinces), "cities": len(sections["city_list"]), "counties": len(sections["county_list"])}, ensure_ascii=False))
