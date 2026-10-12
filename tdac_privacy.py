# -*- coding: utf-8 -*-
"""tdac_privacy.py — 日志脱敏（A8）与关键字段二次确认（A7）。

本文件不含任何敏感数据。
"""
from __future__ import annotations

import hashlib
import inspect
from typing import Any, Callable, Dict, List, Optional, Tuple

# 值本身即敏感、必须打码的字段
SECRET_FIELDS = ("passportNo", "phoneNo")

# 默认不写入完整值的行程 / 身份字段（日志只记录打码或隐藏后的结果）
# 依据：决策「日志默认脱敏，不记录完整行程地址、航班号、邮编及其他不必要个人信息」
PARTIAL_MASK_FIELDS = ("arrivalFlightNo", "departureFlightNo", "accommodationPostalCode")
FULLY_HIDDEN_FIELDS = (
    "accommodationAddress", "email",
    "familyName", "firstName", "middleName",
)
NO_FULL_LOG_FIELDS = PARTIAL_MASK_FIELDS + FULLY_HIDDEN_FIELDS

_HIDDEN_TOKEN = "<已隐藏>"


def mask(value: Any, head: int = 2, tail: int = 2) -> str:
    """通用打码：保留首尾各若干字符，中间以 * 替代。"""
    s = "" if value is None else str(value)
    if len(s) <= head + tail:
        return "*" * len(s)
    return f"{s[:head]}{'*' * (len(s) - head - tail)}{s[-tail:]}"


def safe_value(field: str, value: Any) -> str:
    """返回可安全写入日志的字段值。

    - 敏感字段（护照号 / 手机号）：首尾打码；
    - 航班号 / 邮编：首尾各留 1 字符；
    - 地址 / 邮箱 / 姓名：整段隐藏；
    - 其余字段原样返回。
    """
    if value is None or str(value) == "":
        return ""
    key = str(field)
    if key in SECRET_FIELDS:
        return mask(value, 2, 2)
    if key in PARTIAL_MASK_FIELDS:
        return mask(value, 1, 1)
    if key in FULLY_HIDDEN_FIELDS:
        return _HIDDEN_TOKEN
    return str(value)


# --------------------------------------------------------------------------- #
# A7：关键字段二次确认（护照号 / 手机号 / 邮箱）
# --------------------------------------------------------------------------- #
CRITICAL_FIELDS = (
    ("passportNo", "护照号"),
    ("phoneNo", "手机号"),
    ("email", "收件邮箱"),
)


def critical_fields_review(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    """列出关键字段及其打码展示，供人工二次确认。"""
    items: List[Dict[str, Any]] = []
    for key, label in CRITICAL_FIELDS:
        raw = str(profile.get(key, "") or "").strip()
        items.append({
            "field": key,
            "label": label,
            "present": bool(raw),
            "masked": mask(raw) if raw else "",
        })
    return items


def critical_fingerprint(profile: Dict[str, Any]) -> str:
    """关键字段（护照号 / 手机号 / 收件邮箱）的变更指纹。

    用于回答“用户**当前**这组关键字段，是否就是本人已二次确认过的那一组”：

    * 只保存 SHA-256 摘要（不可逆），不保存也无法还原原值；
    * 任一字段被改动，指纹随即失效，必须重新确认；
    * 摘要输入为三项拼接，搜索空间远大于单独的手机号，不构成可暴力还原的明文等价物。

    返回空字符串表示三项关键字段尚未齐备，无法判定。
    """
    values = [str(profile.get(key, "") or "").strip() for key, _ in CRITICAL_FIELDS]
    if not all(values):
        return ""
    return hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()


def reveal_is_user_driven(ask: Callable[..., bool]) -> bool:
    """判断 ``ask`` 是否声明了第二个位置参数（即是否愿意自己驱动 reveal）。

    只有声明了 ``(prompt, reveal)`` 的调用方，才会在“用户主动点击显示”时调用
    reveal；单参数 ``(prompt)`` 的旧式 CLI 回调保持原样。
    """
    try:
        sig = inspect.signature(ask)
    except (TypeError, ValueError):
        return False
    positional = 0
    for p in sig.parameters.values():
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
            positional += 1
        elif p.kind == p.VAR_POSITIONAL:
            return True
    return positional >= 2


def confirm_critical_fields(profile: Dict[str, Any],
                            ask: Callable[..., bool],
                            reveal: Optional[Callable[[str], Optional[str]]] = None,
                            revise: Optional[Callable[[str], Optional[str]]] = None) -> Tuple[bool, List[str]]:
    """逐项要求确认关键字段（护照号 / 手机号 / 收件邮箱）。

    返回 (是否全部确认, 未确认/缺失的字段标签列表)。
    任一字段缺失，或用户对任一项回答否（且未重新输入），都会导致整体不通过。

    安全约束（B1 同步修正）
      * ``reveal`` **绝不会被本函数自动调用**。它只被“交给”调用方的 ``ask``，
        由 ``ask`` 在**用户主动操作**（例如点击“显示完整值”）时按需触发，
        因此不存在“后台静默展示完整敏感值”的路径。
      * ``ask`` 若声明为 ``ask(prompt, reveal)``，则本函数把 reveal 作为第二个
        位置参数传入；若只能是 ``ask(prompt)``（旧式 CLI 用法），则只传 prompt，
        行为与旧版完全一致（向后兼容）。
      * ``revise(field)`` —— “重新输入确认”。当用户对打码值回答“否”时调用，
        返回新的完整值则视为用户修正；随后会以新的打码值再次要求确认。
        返回 None/空串表示放弃修正，本次确认失败。
    """
    review = critical_fields_review(profile)
    missing = [it["label"] for it in review if not it["present"]]
    if missing:
        return False, missing
    wants_reveal = reveal_is_user_driven(ask)
    for it in review:
        prompt = f"请确认{it['label']} [{it['masked']}] 正确 (y/N): "
        # reveal 仅在 ask 内部、由用户主动操作时调用；本函数不自动调用它。
        confirmed = ask(prompt, reveal) if wants_reveal else ask(prompt)
        if confirmed:
            continue
        # 用户对当前打码值回答“否”：若提供 revise，允许重新输入后再确认。
        if revise is not None:
            new_value = revise(it["field"])
            if new_value:
                profile[it["field"]] = new_value
                if ask(f"已更新{it['label']} [{mask(new_value)}]，确认正确 (y/N): "):
                    continue
        return False, [it["label"]]
    return True, []
