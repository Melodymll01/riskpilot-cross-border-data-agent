"""Memory lifecycle rules and explicit, source-bound profile declarations."""

from __future__ import annotations

import re
from dataclasses import dataclass

from domain.account_memory import MemoryKind, ProfileField

PROFILE_LABELS: dict[ProfileField, str] = {
    "name": "姓名",
    "role": "当前身份 / 职业",
    "organization": "所属组织",
}
_ROLES = frozenset(
    {
        "学生",
        "在校学生",
        "大学生",
        "研究生",
        "硕士生",
        "博士生",
        "工程师",
        "软件工程师",
        "测试工程师",
        "程序员",
        "教师",
        "老师",
        "律师",
        "医生",
        "产品经理",
        "设计师",
        "公务员",
        "自由职业者",
        "实习生",
        "退休人员",
    }
)


@dataclass(frozen=True)
class ProfileDeclaration:
    field: ProfileField
    value: str
    quote: str


@dataclass(frozen=True)
class MemoryPolicy:
    business_ttl_days: float = 90
    profile_ttl_days: float = 365
    profile_review_days: float = 180
    context_chars: int = 600
    context_recent_n: int = 6
    context_ttl_days: float = 30

    def lifecycle(
        self, kind: MemoryKind, field: ProfileField | None, now: float, expiry: float | None = None
    ) -> tuple[float | None, float | None]:
        stable = kind == "preference" or (kind == "profile" and field == "name")
        if expiry is None and not stable:
            days = self.business_ttl_days if kind == "business_context" else self.profile_ttl_days
            expiry = now + days * 86400
        review = None
        if kind == "profile" and field != "name":
            review = now + self.profile_review_days * 86400
            if expiry is not None:
                review = min(review, expiry)
        return expiry, review


def profile_declarations(text: str) -> list[ProfileDeclaration]:
    """Recognize explicit declarations, never infer a role from a name or account."""
    if re.search(
        r"如果|假如|假设|可能|例如|不是|并非|不再|什么|是谁|叫什么|哪里|哪家|[?？]|吗$", text
    ):
        return []
    clauses = [
        part.strip().rstrip("。.!！") for part in re.split(r"[，,；;\n]", text) if part.strip()
    ]
    found: list[ProfileDeclaration] = []
    possible_name: tuple[str, str] | None = None
    for clause in clauses:
        name = re.fullmatch(r"(?:我叫|我的(?:姓名|名字)是|姓名[：:])\s*(.{1,80})", clause)
        role = re.fullmatch(
            r"(?:我(?:目前|现在)?的(?:职业|身份)是|(?:职业|身份)[：:])\s*(.{1,80})", clause
        )
        generic = re.fullmatch(r"(?:我(?:目前|现在)?是|是)\s*(.{1,80})", clause)
        role_value = (
            re.sub(r"^(?:一名|一位|一个|名|位|个)", "", generic[1].strip()) if generic else ""
        )
        organization = re.fullmatch(
            r"(?:我就职于|我的(?:公司|工作单位|所属组织)是|(?:单位|组织)[：:])\s*(.{1,120})", clause
        )
        if name:
            found.append(ProfileDeclaration("name", name[1].strip(), clause))
        elif role:
            found.append(ProfileDeclaration("role", role[1].strip(), clause))
        elif organization:
            found.append(ProfileDeclaration("organization", organization[1].strip(), clause))
        elif generic and role_value in _ROLES:
            found.append(ProfileDeclaration("role", role_value, clause))
        elif (
            generic
            and clause.startswith("我是")
            and re.fullmatch(r"[\u4e00-\u9fff]{2,6}", generic[1])
        ):
            value = generic[1]
            if not re.search(r"人|者|员|师|生|工|公司|企业|机构", value):
                possible_name = (value, clause)
    # The two-clause form "我是马乐乐，是学生" explicitly declares two attributes.
    if possible_name and any(item.field == "role" for item in found):
        found.insert(0, ProfileDeclaration("name", possible_name[0], possible_name[1]))
    result: dict[ProfileField, ProfileDeclaration] = {}
    for item in found:
        if item.field in result and result[item.field].value != item.value:
            raise ValueError("同一声明包含冲突的档案值，请分别明确保存")
        if not item.value or item.value not in item.quote:
            raise ValueError("档案值必须逐字来自用户原话")
        result[item.field] = item
    return list(result.values())


def profile_value(content: str, field: ProfileField) -> str:
    if re.search(r"如果|假如|假设|可能|不是|并非|不再|什么|是谁|[?？]", content):
        raise ValueError("档案值应为明确陈述，不能是问题或假设")
    matches = [item.value for item in profile_declarations(content) if item.field == field]
    if matches:
        return matches[0]
    explicit = re.fullmatch(r"(?:我是|是)\s*(.{1,80})", content.strip())
    if explicit and field in {"name", "role"}:
        value = explicit[1].strip()
        if field == "name" and value in _ROLES:
            raise ValueError("这是一条身份声明，请选择当前身份字段")
        if not re.search(r"[，,；;\n。！？?!]", value):
            return value
    value = content.strip()
    if len(value) > 120 or re.search(r"[，,；;\n。！？?!]", value):
        raise ValueError("请填写该档案字段的值，或使用明确声明，如“我叫马乐乐”")
    if value.startswith(("我", "是")):
        raise ValueError("无法确定档案值，请填写姓名、职业或组织本身")
    return value


_PROFILE_QUESTIONS: dict[str, tuple[ProfileField, ...]] = {
    "我叫什么": ("name",),
    "我叫什么名字": ("name",),
    "我的名字是什么": ("name",),
    "我的姓名是什么": ("name",),
    "你记得我叫什么吗": ("name",),
    "你还记得我叫什么吗": ("name",),
    "我的职业是什么": ("role",),
    "你记得我的职业吗": ("role",),
    "你还记得我的职业吗": ("role",),
    "我的职业是啥": ("role",),
    "我是什么职业": ("role",),
    "我的身份是什么": ("role",),
    "我现在是什么身份": ("role",),
    "我是做什么的": ("role",),
    "我的工作单位是什么": ("organization",),
    "我属于哪个组织": ("organization",),
    "我的公司叫什么": ("organization",),
    "我是谁": ("name", "role", "organization"),
    "介绍一下我": ("name", "role", "organization"),
}


def profile_question_fields(question: str) -> tuple[ProfileField, ...] | None:
    """A closed personal-query contract; mixed/legal requests stay on the Agent path."""
    text = question.strip().rstrip("？?。！!")
    if text.startswith("请问"):
        text = text[2:].strip()
    return _PROFILE_QUESTIONS.get(text)
