# -*- coding: utf-8 -*-
"""tdac_validate.py — 关键字段格式校验（B1）与正式预填门禁（G1 验收新增）。

只做**格式**层面的本地校验，用于在录入 / 保存前尽早发现明显错误：
护照号、手机号、收件邮箱、日期。所有校验都不联网、不落盘、不写日志。

返回值统一为 ``(ok: bool, message: str)``，message 为中文提示；
调用方（GUI / CLI）自行决定如何展示。

本节外另提供 ``prefill_blockers``：判断“是否允许开始官网预填”。
它**不参与**草稿暂存——暂存草稿允许资料不完整。

本文件不含任何敏感数据。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

# 中国大陆手机号：1 开头，第二位 3-9，共 11 位
_CN_MOBILE = re.compile(r"^1[3-9]\d{9}$")

# 护照号：常见为中国普通护照「E + 8 位数字」，也兼容「1-2 位字母 + 7-8 位数字」
_PASSPORT_CN = re.compile(r"^[Ee]\d{8}$")
_PASSPORT_GENERIC = re.compile(r"^[A-Za-z]{1,2}\d{7,8}$")

# 邮箱：保守但足够的本地校验（不追求完整 RFC 5322）
_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
                    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)+$")


def _norm(value: Any) -> str:
    return "" if value is None else str(value).strip()


def validate_passport(value: Any) -> Tuple[bool, str]:
    """校验护照号格式。空值判为不通过（关键字段必填）。"""
    s = _norm(value)
    if not s:
        return False, "护照号不能为空"
    if not s.isascii():
        return False, "护照号只能是英文字母和数字"
    if _PASSPORT_CN.match(s):
        return True, "格式正常（中国普通护照 E+8 位数字）"
    if _PASSPORT_GENERIC.match(s):
        return True, "格式可接受（1–2 位字母 + 7–8 位数字）"
    return False, "护照号格式不符合常见规则（应为字母开头 + 7–8 位数字，如 E12345678）"


def validate_phone(value: Any, phone_code: str = "86") -> Tuple[bool, str]:
    """校验手机号格式。区号为 86 时按中国大陆手机号校验，其它区号只做长度校验。"""
    s = _norm(value)
    if not s:
        return False, "手机号不能为空"
    if not s.isdigit():
        return False, "手机号只能填数字（不含 + 号、空格或短横线）"
    code = _norm(phone_code) or "86"
    if code == "86":
        if _CN_MOBILE.match(s):
            return True, "格式正常（中国大陆手机号）"
        return False, "中国大陆手机号应为 11 位、以 1 开头且第二位为 3–9"
    if 6 <= len(s) <= 15:
        return True, "格式可接受（6–15 位数字）"
    return False, "手机号应为 6–15 位数字"


def validate_email(value: Any) -> Tuple[bool, str]:
    """校验收件邮箱格式。"""
    s = _norm(value)
    if not s:
        return False, "收件邮箱不能为空"
    if len(s) > 254:
        return False, "收件邮箱过长"
    if not _EMAIL.match(s):
        return False, "收件邮箱格式不正确（例如 name@example.com）"
    return True, "格式正常"


def validate_date(value: Any) -> Tuple[bool, str]:
    """校验官网日期格式（固定 YYYY/MM/DD）。空值由“必填检查”负责，这里判为通过。"""
    import datetime as dt

    s = _norm(value)
    if not s:
        return True, "（未填写）"
    try:
        dt.datetime.strptime(s, "%Y/%m/%d")
    except Exception:
        return False, "应填 YYYY/MM/DD（例如 2026/01/01）"
    return True, "格式正常"


# --------------------------------------------------------------------------- #
# 正式（官网）预填门禁
#
# 与“暂存草稿”严格分离：草稿保存**不调用**本节的任何函数，因此缺必填项、
# 格式不对、日期未填都照样能暂存；而下面这些检查只决定
# “是否允许开始官网预填”。
# --------------------------------------------------------------------------- #

# 必填字段清单（键 → 中文标签）。字段集合与 tdac_fill.check_config 保持一致，
# 由 tests/test_draft.py 中的一致性用例锁定，避免两处清单漂移。
REQUIRED_PROFILE_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("familyName", "英文姓"),
    ("firstName", "英文名"),
    ("nationality", "国籍"),
    ("dateOfBirth", "出生日期"),
    ("gender", "性别"),
    ("occupation", "职业"),
    ("countryOfResidence", "居住国家/地区"),
    ("cityOfResidence", "居住城市"),
    ("phoneCode", "电话区号"),
    ("email", "收件邮箱"),
    ("passportNo", "护照号"),
    ("phoneNo", "手机号"),
)
REQUIRED_TRIP_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("arrivalDate", "入境日期"),
    ("departureDate", "离境日期"),
    ("countryOfBoarding", "出发/登机国家地区"),
    ("purposeOfTravel", "旅行目的"),
    ("arrivalMode", "入境交通方式"),
    ("arrivalFlightType", "入境交通类型"),
    ("arrivalFlightNo", "入境航班/车船号"),
    ("departureMode", "离境交通方式"),
    ("departureFlightType", "离境交通类型"),
    ("departureFlightNo", "离境航班/车船号"),
    ("accommodationType", "住宿类型"),
    ("accommodationProvince", "府/省"),
    ("accommodationDistrict", "区/县"),
    ("accommodationSubdistrict", "分区/街道"),
    ("accommodationPostalCode", "邮编"),
    ("accommodationAddress", "详细地址"),
)


def _missing_for(fields: Tuple[Tuple[str, str], ...], data: Dict[str, Any]) -> List[str]:
    """按给定的字段清单返回缺失项的中文标签（不含任何字段值）。"""
    out: List[str] = []
    for key, label in fields:
        if not _norm((data or {}).get(key)):
            out.append(label)
    return out


def missing_required_fields(profile: Dict[str, Any],
                            trip: Dict[str, Any]) -> List[str]:
    """返回缺失必填项的中文标签列表（不含任何字段值）。"""
    return (_missing_for(REQUIRED_PROFILE_FIELDS, profile)
            + _missing_for(REQUIRED_TRIP_FIELDS, trip))


def _window_blocker(trip: Dict[str, Any]) -> str:
    """日期窗口不一致时的提示；日期本身未填时返回空串（由必填检查覆盖）。"""
    raw = _norm((trip or {}).get("arrivalDate"))
    if not raw or not validate_date(raw)[0]:
        return ""
    try:
        import tdac_fill
        ok, msg = tdac_fill.check_window(trip)
    except Exception:
        return "入境日期无法解析，无法判断申报窗口。"
    return "" if ok else msg


def _health_content_blocker(dynamic: Dict[str, Any]) -> str:
    """健康声明的**内容**是否齐备（与“是否本人确认”是两件事）。"""
    countries = (dynamic or {}).get("last14DaysCountries")
    if isinstance(countries, (list, tuple)):
        has_countries = any(_norm(c) for c in countries)
    else:
        has_countries = bool(_norm(countries))
    if not has_countries:
        return "健康声明的『过去 14 天停留国家/地区』为空（请到『健康声明』页填写后保存）。"
    return ""


# 阻断原因的分组名（界面按此分组展示，便于阅读）
GROUP_PROFILE = "个人资料"
GROUP_TRIP = "行程住宿"
GROUP_HEALTH = "健康声明"


def prefill_blocker_groups(profile: Dict[str, Any], trip: Dict[str, Any],
                           dynamic: Dict[str, Any], *,
                           critical_confirmed: bool,
                           health_reconfirmed: bool) -> List[Tuple[str, List[str]]]:
    """官网预填门禁：按「个人资料 / 行程住宿 / 健康声明」分组的阻断原因。

    返回 ``[(组名, [原因, ...]), ...]``，只包含**有阻断项**的组，顺序固定。
    空列表 = 允许开始官网预填。本函数只做判断，不写文件、不联网。
    """
    profile, trip, dynamic = profile or {}, trip or {}, dynamic or {}
    groups: List[Tuple[str, List[str]]] = []

    # --- 个人资料 -------------------------------------------------------
    items: List[str] = []
    missing_p = _missing_for(REQUIRED_PROFILE_FIELDS, profile)
    if missing_p:
        items.append("资料不完整，缺少：" + "、".join(missing_p) + "（可先暂存草稿，稍后补齐）")
    problems = validate_profile(profile)
    if problems:
        items.append("关键字段格式不正确：" + "；".join(
            f"{p['label']} {p['message']}" for p in problems))
    if not critical_confirmed:
        items.append("护照号 / 手机号 / 收件邮箱尚未由本人二次确认"
                     "（请在『个人资料』页点击『校验并保存个人资料』并逐项核对）。")
    if items:
        groups.append((GROUP_PROFILE, items))

    # --- 行程住宿 -------------------------------------------------------
    items = []
    missing_t = _missing_for(REQUIRED_TRIP_FIELDS, trip)
    if missing_t:
        items.append("行程与住宿资料不完整，缺少：" + "、".join(missing_t) + "（可先暂存草稿，稍后补齐）")
    for field, label in (("arrivalDate", "入境日期"), ("departureDate", "离境日期")):
        raw = _norm(trip.get(field))
        if raw and not validate_date(raw)[0]:
            items.append(f"{label}格式不正确：{validate_date(raw)[1]}")
    window_msg = _window_blocker(trip)
    if window_msg:
        items.append("申报窗口：" + window_msg)
    if items:
        groups.append((GROUP_TRIP, items))

    # --- 健康声明 -------------------------------------------------------
    items = []
    if not health_reconfirmed:
        items.append("健康声明尚未在本次使用中由本人重新确认"
                     "（请到『健康声明』页勾选两项确认后保存）。")
    health_msg = _health_content_blocker(dynamic)
    if health_msg:
        items.append(health_msg)
    if items:
        groups.append((GROUP_HEALTH, items))

    return groups


def prefill_blockers(profile: Dict[str, Any], trip: Dict[str, Any], dynamic: Dict[str, Any],
                     *, critical_confirmed: bool, health_reconfirmed: bool) -> List[str]:
    """官网预填的门禁检查：返回阻断原因列表（空列表 = 允许开始）。

    四道门，全部满足才放行：

    1. **资料完整**：必备字段一个不缺；
    2. **格式正确**：护照号 / 手机号 / 收件邮箱 + 日期格式通过本地校验；
    3. **关键字段经用户确认**：护照号 / 手机号 / 收件邮箱已由本人二次确认，
       且此后未被改动（由 ``tdac_privacy.critical_fingerprint`` 判定）；
    4. **日期窗口**：处于官网允许的申报窗口内（抵达前 3 天内）。

    另外，健康声明即使随草稿暂存过，也必须由本人在**本次使用**中重新确认、
    且内容齐备，否则同样阻断。

    本函数是 :func:`prefill_blocker_groups` 的扁平化版本（供命令行与既有调用方
    使用）；界面用分组版本展示，便于阅读。
    """
    return [item for _group, items in prefill_blocker_groups(
        profile, trip, dynamic,
        critical_confirmed=critical_confirmed,
        health_reconfirmed=health_reconfirmed) for item in items]


def validate_profile(profile: Dict[str, Any]) -> List[Dict[str, str]]:
    """校验 profile 中的关键字段，返回问题列表（为空表示全部通过）。

    每项形如 ``{"field", "label", "value_raw_len", "message"}``，
    **不包含字段明文**，便于安全地展示与记录。
    """
    problems: List[Dict[str, str]] = []
    checks = (
        ("passportNo", "护照号", validate_passport, lambda v: validate_passport(v)),
        ("phoneNo", "手机号", validate_phone,
         lambda v: validate_phone(v, _norm(profile.get("phoneCode")) or "86")),
        ("email", "收件邮箱", validate_email, lambda v: validate_email(v)),
    )
    for field, label, _fn, run in checks:
        raw = _norm(profile.get(field))
        ok, message = run(raw)
        if not ok:
            problems.append({
                "field": field,
                "label": label,
                "value_len": str(len(raw)),
                "message": message,
            })
    return problems


def format_problems(problems: List[Dict[str, str]]) -> str:
    """把 validate_profile 的结果拼成中文多行文本（不含任何明文）。"""
    if not problems:
        return "关键字段格式校验通过。"
    return "\n".join(f"· {p['label']}：{p['message']}" for p in problems)


if __name__ == "__main__":  # 自检
    assert validate_passport("E12345678")[0]
    assert not validate_passport("12345678")[0]
    assert validate_phone("13800138000")[0]
    assert not validate_phone("1380013800")[0]
    assert validate_email("tester@example.invalid")[0]
    assert not validate_email("tester@@example")[0]
    print("tdac_validate 自检 PASS")
