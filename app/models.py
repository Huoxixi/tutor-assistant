from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

SHANGHAI = timezone(timedelta(hours=8))


def now() -> datetime:
    return datetime.now(SHANGHAI)


class Message(BaseModel):
    group: str = Field(min_length=1, max_length=120)
    sender: str = Field(default="未知发布者", max_length=120)
    text: str = Field(max_length=300000)
    sent_at: datetime | None = None
    kind: str = "text"
    source_id: str | None = None
    synthetic: bool = False

    @field_validator("sent_at")
    @classmethod
    def localize(cls, value):
        if value is None:
            return value
        return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)


class Rule(BaseModel):
    field: str
    value: Any
    mode: Literal["must", "prefer"] = "must"


class Preferences(BaseModel):
    profile: dict[str, Any] = Field(default_factory=dict)
    rules: list[Rule] = Field(default_factory=list, max_length=30)
    supplement: str = Field(default="", max_length=4000)


class Fact(BaseModel):
    value: Any
    evidence: str = Field(min_length=1, max_length=4000)
    mode: Literal["must", "prefer"] = "must"


class Draft(BaseModel):
    kind: Literal["order", "update"] = "order"
    reference: str | None = None
    facts: dict[str, Fact] = Field(default_factory=dict)
    status: Literal["open", "closed"] = "open"
    status_evidence: str | None = None
    source_text: str | None = None
    validation_issues: list[str] = Field(default_factory=list, max_length=50)


LABELS = {
    "subject": "科目", "grade": "年级", "city": "城市", "area": "区县",
    "province": "省份", "location": "教学地区",
    "mode": "授课方式", "hourly_rate": "时薪范围", "lesson_rate": "单次课酬范围",
    "agency_fee": "最高中介费（元）", "weekly_sessions": "每周次数", "duration_hours": "每次时长（小时）",
    "term": "长期/短期", "trial": "是否接受试课", "schedule": "可授课时段", "exclude_keywords": "排除关键词",
    "teacher_type": "教师身份", "education": "学历", "school": "学校", "major": "专业", "experience": "教学年限",
    "teacher_gender": "教员性别", "school_tier": "院校类别", "english_score": "高考英语分数",
    "english_level": "英语能力", "teaching_language": "授课语言", "student_gender": "学员性别",
    "students_count": "学员人数", "focus_subjects": "重点科目", "monthly_rate": "月薪",
    "rate_options": "条件课酬", "manual_requirements": "其他硬性要求", "notes": "补充说明",
}
FACT_FIELDS = set(LABELS) - {"exclude_keywords"}
