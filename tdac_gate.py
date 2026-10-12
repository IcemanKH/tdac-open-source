# -*- coding: utf-8 -*-
"""tdac_gate.py — 正式（官网）预填的统一门禁（G1 最终修复）。

背景
  界面（``tdac_gui``）、命令行（``tdac_fill``）与便携版（``tdac_app`` →
  ``tdac_fill``）过去各自判断“能不能开始官网预填”，命令行还提供过
  ``--skip-confirm`` 这样的**公开开关**来跳过关键字段二次确认。公开参数、
  布尔标记都可能被误用或伪造，属于真实的安全缺陷。

本模块把门禁收敛为**唯一实现**，三处入口一律调用它：
  * ``profile.json`` 中的不可逆指纹只用于提示关键字段是否变化，绝不能授权预填；
  * 每次正式预填必须由当前 GUI 对话框或交互式 CLI 颁发一次性、仅内存存在的授权，
    并绑定当前护照号 / 手机号 / 邮箱及健康声明内容；
  * 授权消费后立即失效，非交互 CLI、公开参数和可编辑 JSON 都无法伪造它。

正式预填必须同时满足：
  1. 资料完整（必填字段齐全）；
  2. 格式正确（护照号 / 手机号 / 收件邮箱 / 日期）；
  3. 关键字段已在本次操作中由本人确认；
  4. 处于官网申报窗口内；
  5. 健康声明在**本次操作**中已由本人确认，且内容齐备。

本文件不含任何敏感数据。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import tdac_paths
import tdac_privacy
import tdac_validate

__all__ = [
    "CONFIRM_AT_KEY", "CONFIRM_FP_KEY",
    "load_confirm_record", "confirmation_valid", "record_critical_confirmation",
    "health_confirmed", "authorize_current_prefill", "has_current_prefill_authorization",
    "consume_current_prefill_authorization", "data_blocker_groups",
    "prefill_blocker_groups", "prefill_blockers",
]

# 记录“这组关键字段已由本人确认”的两个键（写入 profile.json）。
CONFIRM_AT_KEY = "criticalConfirmedAt"
CONFIRM_FP_KEY = "criticalFingerprint"

# 只存在于当前 Python 进程内的一次性授权；绝不从命令行、配置 JSON 或草稿读取。
# 值绑定关键字段、行程住宿和健康声明的当前内容，消费后立即清除，避免复用。
_current_prefill_authorization: Optional[Tuple[str, str, str]] = None


def _health_fingerprint(dynamic: Dict[str, Any]) -> str:
    payload = json.dumps(dynamic or {}, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _trip_fingerprint(trip: Dict[str, Any]) -> str:
    """行程与住宿的确定性摘要；授权后任一字段变动都必须重新确认。"""
    payload = json.dumps(trip or {}, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def authorize_current_prefill(profile: Dict[str, Any], dynamic: Dict[str, Any],
                              trip: Dict[str, Any]) -> bool:
    """由本次人工确认流程颁发一次性内存授权；不落盘、不接受外部布尔值。"""
    global _current_prefill_authorization
    critical = tdac_privacy.critical_fingerprint(profile or {})
    if not critical or not health_confirmed(dynamic or {}):
        _current_prefill_authorization = None
        return False
    _current_prefill_authorization = (critical, _health_fingerprint(dynamic or {}),
                                      _trip_fingerprint(trip or {}))
    return True


def has_current_prefill_authorization(profile: Dict[str, Any], dynamic: Dict[str, Any],
                                      trip: Dict[str, Any]) -> bool:
    """检查当前进程是否持有匹配授权，但不消费它（供 GUI 交接给工作线程）。"""
    return bool(_current_prefill_authorization and
                _current_prefill_authorization == (
                    tdac_privacy.critical_fingerprint(profile or {}),
                    _health_fingerprint(dynamic or {}), _trip_fingerprint(trip or {})))


def consume_current_prefill_authorization(profile: Dict[str, Any], dynamic: Dict[str, Any],
                                          trip: Dict[str, Any]) -> bool:
    """验证并一次性消费当前操作授权，任何变更或重放都会失败。"""
    global _current_prefill_authorization
    ok = has_current_prefill_authorization(profile, dynamic, trip)
    _current_prefill_authorization = None
    return ok


def _profile_json_path(config_dir: Optional[str] = None) -> str:
    return os.path.join(config_dir or tdac_paths.config_dir(), "profile.json")


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_confirm_record(config_dir: Optional[str] = None) -> Dict[str, str]:
    """读取确认证据（时间 + 指纹）；不存在时返回空值。

    只读取两个**元信息**键，不读取任何个人资料字段。
    """
    data = _read_json(_profile_json_path(config_dir))
    return {
        "confirmedAt": str(data.get(CONFIRM_AT_KEY, "") or ""),
        "fingerprint": str(data.get(CONFIRM_FP_KEY, "") or ""),
    }


def confirmation_valid(profile: Dict[str, Any],
                       record: Optional[Dict[str, str]] = None,
                       config_dir: Optional[str] = None) -> bool:
    """关键字段是否“已由本人确认，且此后未被改动”。

    判据：存在确认时间与指纹，且指纹等于**当前**三项关键字段（护照号 /
    手机号 / 收件邮箱）的 SHA-256。任一字段被改动 → 立即失效。
    """
    rec = record if record is not None else load_confirm_record(config_dir)
    current = tdac_privacy.critical_fingerprint(profile or {})
    return bool(rec.get("confirmedAt") and rec.get("fingerprint")
                and current and current == rec["fingerprint"])


def record_critical_confirmation(profile: Dict[str, Any],
                                 config_dir: Optional[str] = None,
                                 when: Optional[str] = None) -> str:
    """把“本人已确认这组关键字段”写入 ``profile.json``（合并，不覆盖其它键）。

    只写两个不可逆 / 非敏感的值：确认时间与指纹摘要。
    """
    path = _profile_json_path(config_dir)
    data = _read_json(path)
    data[CONFIRM_AT_KEY] = when or dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data[CONFIRM_FP_KEY] = tdac_privacy.critical_fingerprint(profile or {})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tdac_paths.atomic_write_json(path, data)
    return path


def health_confirmed(dynamic: Dict[str, Any]) -> bool:
    """健康声明是否已由本人在本次使用中确认（看 ``dynamic.json`` 的两个标记）。"""
    dyn = dynamic or {}
    return bool(dyn.get("last14DaysConfirmed") and dyn.get("healthDeclarationConfirmed"))


def data_blocker_groups(profile: Dict[str, Any], trip: Dict[str, Any],
                        dynamic: Dict[str, Any]) -> List[Tuple[str, List[str]]]:
    """只校验资料内容、格式和日期窗口，供当前操作确认前的 GUI/CLI 使用。"""
    return tdac_validate.prefill_blocker_groups(
        profile, trip, dynamic, critical_confirmed=True, health_reconfirmed=True)


def prefill_blocker_groups(profile: Dict[str, Any], trip: Dict[str, Any],
                           dynamic: Dict[str, Any], **_ignored: Any
                           ) -> List[Tuple[str, List[str]]]:
    """统一门禁：返回按「个人资料 / 行程住宿 / 健康声明」分组的阻断原因。

    仅接受由当前人工确认流程写入内存的一次性授权。任何落盘确认记录仅用于
    变更提示，不能直接放行；历史参数即使传入也会被忽略，防止外部布尔值绕过。
    """
    authorized = consume_current_prefill_authorization(profile or {}, dynamic or {}, trip or {})
    return tdac_validate.prefill_blocker_groups(
        profile, trip, dynamic,
        critical_confirmed=authorized,
        health_reconfirmed=authorized)


def prefill_blockers(profile: Dict[str, Any], trip: Dict[str, Any],
                     dynamic: Dict[str, Any], **kwargs: Any) -> List[str]:
    """扁平化的阻断原因列表（供命令行与既有调用方使用）。"""
    return [item for _name, items in prefill_blocker_groups(
        profile, trip, dynamic, **kwargs) for item in items]
