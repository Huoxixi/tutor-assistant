import re

from .models import LABELS, Preferences


def values(value):
    if isinstance(value, list):
        return value
    return [x.strip() for x in re.split(r"[,，、;；]", str(value)) if x.strip()]


def compare(field, actual, wanted):
    if actual is None or actual == "" or actual == []:
        return None
    if field in {"hourly_rate", "lesson_rate", "monthly_rate", "agency_fee", "experience", "english_score"}:
        low, high = (actual, actual) if isinstance(actual, (float, int)) else (actual.get("min"), actual.get("max")) if isinstance(actual, dict) else (None, None)
        if low is None or high is None:
            return None
        if field == "agency_fee":
            return True if high <= float(wanted) else False if low > float(wanted) else None
        if isinstance(wanted, dict):
            minimum, maximum = wanted.get("min", 0), wanted.get("max", float("inf"))
            if high < minimum or low > maximum:
                return False
            return True if low >= minimum and high <= maximum else None
        return True if low >= float(wanted) else False if high < float(wanted) else None
    if field in {"weekly_sessions", "duration_hours"}:
        if isinstance(actual, dict):
            return compare("hourly_rate", actual, wanted)
        if not isinstance(actual, (float, int)):
            return None
        bounds = wanted if isinstance(wanted, dict) else {"min": float(wanted), "max": float(wanted)}
        return bounds.get("min", 0) <= actual <= bounds.get("max", 1000)
    if field == "schedule":
        if not isinstance(actual, list) or not isinstance(wanted, list) or not actual:
            return None
        return all(any(w["day"] == a["day"] and w["start"] <= a["start"] and w["end"] >= a["end"] for w in wanted) for a in actual)
    if field == "trial":
        # actual=True means a trial is required; wanted=False means unwilling.
        return not (actual is True and wanted is False)
    if field in {"subject", "grade"}:
        own = set(values(wanted))
        if field == "subject" and "全科" in own:
            return True
        return set(values(actual)).issubset(own)
    return any(str(w) == str(a) or (field == "area" and str(w) in str(a)) for a in values(actual) for w in values(wanted))


def match(facts: dict, prefs: Preferences, text: str, closed=False, time_unknown=False, location_codes=None):
    reasons, unknowns, conflicts, score = [], [], [], 0
    location_status = "not_requested"
    if closed:
        conflicts.append("原文明确标明已出或已找到老师")
    if time_unknown:
        unknowns.append("发布时间无法确认是否在最近48小时")
    actuals = {k: v["value"] for k, v in facts.items()}
    options = actuals.get("rate_options", [])
    applicable_rates = []
    for option in options if isinstance(options, list) else []:
        eligible = True
        for key in ["teacher_type", "education"]:
            if key in option:
                own = prefs.profile.get(key)
                if key == "education":
                    rank = {"高中": 0, "专科": 1, "本科": 2, "硕士": 3, "博士": 4}
                    eligible &= own is not None and rank.get(str(own), -1) >= rank.get(str(option[key]), 999)
                else:
                    eligible &= own == option[key]
        if eligible and "hourly_rate" in option:
            applicable_rates.append(option["hourly_rate"])
    if applicable_rates:
        actuals["hourly_rate"] = min(applicable_rates)
    if "hourly_rate" not in actuals and "lesson_rate" in actuals and isinstance(actuals.get("duration_hours"), (int, float)) and actuals["duration_hours"] > 0:
        rate, hours = actuals["lesson_rate"], actuals["duration_hours"]
        if isinstance(rate, (int, float)):
            actuals["hourly_rate"] = rate / hours
        elif isinstance(rate, dict):
            actuals["hourly_rate"] = {k: v / hours for k, v in rate.items()}
    for rule in prefs.rules:
        if rule.field == "exclude_keywords":
            bad = [k for k in values(rule.value) if k in text]
            outcome = not bad
        elif rule.field == "location":
            from .regions import match_location, match_location_codes
            outcome = match_location_codes(location_codes,rule.value) if location_codes else match_location(facts, rule.value)
            location_status = "matched" if outcome is True else "outside" if outcome is False else "unconfirmed"
        else:
            outcome = compare(rule.field, actuals.get(rule.field), rule.value)
        label = LABELS[rule.field]
        if outcome is True:
            reasons.append(label + "符合")
            score += rule.mode == "prefer"
        elif rule.mode == "must":
            (conflicts if outcome is False else unknowns).append(label + ("冲突" if outcome is False else "待确认"))
    for field in ["teacher_type", "education", "school", "major", "experience", "teacher_gender", "school_tier", "english_score", "english_level", "teaching_language"]:
        requirement = actuals.get(field)
        if requirement is None:
            continue
        if field == "teacher_gender" and requirement == "不限":
            continue
        requirement_mode = facts[field].get("mode", "must")
        own = prefs.profile.get(field)
        if own in (None, "", []):
            if requirement_mode == "must":
                unknowns.append(("请设置我的" + LABELS[field]) if field in {"teacher_gender", "school_tier"} else ("订单另有" + LABELS[field] + "要求，需向发布者确认"))
            continue
        if field == "education":
            rank = {"高中": 0, "专科": 1, "本科": 2, "硕士": 3, "博士": 4}
            outcome = rank.get(str(own), -1) >= rank.get(str(requirement), 999)
        elif field == "experience":
            outcome = float(own) >= float(requirement) if isinstance(requirement, (int, float)) else None
        elif field == "english_score":
            outcome = compare("english_score", own, requirement)
        elif field == "school_tier":
            tiers = set(values(own))
            if "985" in tiers:
                tiers.add("211")
            if tiers.intersection({"985", "211"}):
                tiers.add("一本")
            if "双非一本" in tiers:
                tiers.update({"一本", "双非"})
            if "二本" in tiers:
                tiers.add("双非")
            if "普通本科" in tiers:
                tiers.add("双非")
            outcome = bool(tiers.intersection(values(requirement)))
            if not outcome and (("双非" in tiers and "一本" in values(requirement) and "二本" not in tiers) or "双一流" in tiers or "其他" in tiers):
                outcome = None
        elif field == "english_level":
            outcome = True if any(str(r) == str(o) for r in values(requirement) for o in values(own)) else None
        else:
            outcome = compare(field, own, requirement)
        if requirement_mode == "prefer":
            if outcome is True:
                reasons.append("满足发布者的" + LABELS[field] + "偏好")
                score += 1
        elif outcome is False:
            conflicts.append("订单的" + LABELS[field] + "要求与我的资料冲突")
        elif outcome is None:
            unknowns.append("订单的" + LABELS[field] + "要求待确认")
        else:
            reasons.append("我的" + LABELS[field] + "满足订单要求")
    if actuals.get("manual_requirements") and facts["manual_requirements"].get("mode", "must") == "must":
        unknowns.append("其他硬性要求需人工核对：" + "、".join(values(actuals["manual_requirements"])))
    if not prefs.rules:
        unknowns.append("请先设置接单条件")
    return {"bucket": "rejected" if conflicts else "pending" if unknowns else "matched", "reasons": reasons, "unknowns": unknowns, "conflicts": conflicts, "score": score, "location_status": location_status}
