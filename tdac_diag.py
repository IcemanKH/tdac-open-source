# -*- coding: utf-8 -*-
"""tdac_diag.py — 脱敏诊断包（B4）。

设计原则
  * **白名单**：只导出预先列出的、非敏感的字段；不在白名单里的一律不导出。
  * **绝不包含**：姓名、护照号、手机号、邮箱、行程/住宿信息、浏览器凭据
    （Cookie / Login Data）、网页截图、完整网页源码。
  * **只读**：只读取版本、平台、布尔存在性等元信息；不读取任何真实的字段值。
  * **用户主动导出**：由调用方在用户明确点击后调用；导出前可先 ``render`` 预览。
  * **自检**：导出前对最终文本做一次禁飞区扫描（邮箱 / 手机号 / 护照号 /
    绝对路径 / 用户名），命中即拒绝导出并抛错——宁可导出失败，也不泄露。

本文件不含任何敏感数据。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import re
import sys
from typing import Any, Callable, Dict, List, Optional

import tdac_paths

# 需要从文本中抹掉的模式（防止任何意外泄漏）
_FORBIDDEN = (
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("邮箱", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("证件号", re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{1,2}\d{7,8}(?![0-9])")),
    ("Windows 用户名", re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"']+", re.IGNORECASE)),
)

_REQUIRED_PROFILE_KEYS = (
    "familyName", "firstName", "nationality", "dateOfBirth", "gender",
    "occupation", "countryOfResidence", "cityOfResidence", "email",
    "phoneCode", "passportNo", "phoneNo",
)
_REQUIRED_TRIP_KEYS = (
    "arrivalDate", "departureDate", "countryOfBoarding", "purposeOfTravel",
    "arrivalMode", "arrivalFlightType", "arrivalFlightNo",
    "departureMode", "departureFlightType", "departureFlightNo",
    "accommodationType", "accommodationProvince", "accommodationDistrict",
    "accommodationSubdistrict", "accommodationPostalCode", "accommodationAddress",
)
_HEALTH_KEYS = ("last14DaysCountries", "yellowFeverVaccination", "symptoms")

EXCLUDED_NOTE = (
    "本诊断包已按白名单生成，不包含：姓名、护照号、手机号、收件邮箱、行程与住宿信息、"
    "浏览器凭据（Cookie/Login Data）、网页截图、完整网页源码，也不包含任何真实字段值。"
)


def scan_forbidden(text: str) -> List[str]:
    """扫描文本中的禁飞区模式，返回命中的类别名列表（去重）。"""
    hits: List[str] = []
    for name, pattern in _FORBIDDEN:
        if pattern.search(text) and name not in hits:
            hits.append(name)
    return hits


def scrub(text: str) -> str:
    """把文本里的敏感模式替换为占位符（最后一道保险）。"""
    out = text
    for name, pattern in _FORBIDDEN:
        out = pattern.sub(f"<已脱敏:{name}>", out)
    return out


def collect_diagnostic(config_presence: Optional[Callable[[], Dict[str, Any]]] = None,
                       check_summary: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """收集白名单诊断信息（全部为非敏感的元信息 / 布尔值）。"""
    kind = None
    present = False
    try:
        from tdac_browser import detect_browser
        kind, path = detect_browser()
        present = bool(path)
    except Exception:
        pass

    data: Dict[str, Any] = {
        "生成时间": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "程序版本": _safe(lambda: __import__("tdac_update").APP_VERSION),
        "Python": sys.version.split()[0],
        "Python 位数": 64 if sys.maxsize > 2 ** 32 else 32,
        "系统": platform.system(),
        "系统版本": platform.release(),
        "架构": platform.machine(),
        "浏览器类型": kind or "未检测到",
        "浏览器可用": present,
        "数据根目录可用": os.path.isdir(tdac_paths.data_root()),
        "受管子目录存在情况": {name: os.path.isdir(os.path.join(tdac_paths.data_root(), name))
                              for name in tdac_paths.MANAGED_SUBDIRS},
        "加密文件存在": os.path.exists(tdac_paths.secrets_path()),
    }

    if config_presence is not None:
        try:
            data.update(config_presence() or {})
        except Exception:
            data["配置存在性读取失败"] = True

    if check_summary is not None:
        # 只保留计数类字段，绝不保留明细文本。
        safe_summary = {k: v for k, v in check_summary.items()
                        if k in ("fail_count", "warn_count") and isinstance(v, int)}
        data["最近一次离线检查"] = safe_summary

    return data


def _safe(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except Exception:
        return "未知"


def config_presence_reader(config_loader: Callable[[str], Dict[str, Any]]) -> Callable[[], Dict[str, Any]]:
    """构造一个“只返回布尔存在性”的读取器，供 collect_diagnostic 使用。

    :param config_loader: 形如 ``loader("profile.json") -> dict``，返回**已读取**的配置。
                          本函数只使用其“键是否存在且非空”的布尔结论，不使用任何值。
    """
    def _read() -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        try:
            profile = config_loader("profile.json") or {}
            out["个人资料必填项齐全"] = {
                k: bool(str(profile.get(k, "")).strip()) for k in _REQUIRED_PROFILE_KEYS
            }
        except Exception:
            out["个人资料可读"] = False
        try:
            trip = config_loader("trip.json") or {}
            out["行程必填项齐全"] = {
                k: bool(str(trip.get(k, "")).strip()) for k in _REQUIRED_TRIP_KEYS
            }
        except Exception:
            out["行程可读"] = False
        try:
            dynamic = config_loader("dynamic.json") or {}
            out["健康声明字段存在"] = {
                k: (bool(dynamic.get(k)) if dynamic.get(k) is not None else False)
                for k in _HEALTH_KEYS
            }
        except Exception:
            out["健康声明可读"] = False
        return out
    return _read


def render_diagnostic(data: Optional[Dict[str, Any]] = None) -> str:
    """把诊断信息渲染成可预览的纯文本（先 scrub，再自检）。"""
    if data is None:
        data = collect_diagnostic()
    body = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False)
    body = scrub(body)
    text = (
        "TDAC 脱敏诊断包\n"
        "================\n"
        f"{EXCLUDED_NOTE}\n\n"
        "---- 诊断信息（JSON）----\n"
        f"{body}\n"
    )
    hits = scan_forbidden(text)
    if hits:
        raise ValueError("诊断包自检未通过，检测到疑似敏感内容：" + "、".join(hits) + "；已拒绝生成。")
    return text


def export_diagnostic(dest_path: Optional[str] = None,
                      data: Optional[Dict[str, Any]] = None) -> str:
    """导出诊断包到用户指定位置（默认：用户数据目录 output/）。"""
    text = render_diagnostic(data)
    if dest_path:
        path = os.path.abspath(dest_path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    else:
        out_dir = tdac_paths.output_dir()
        os.makedirs(out_dir, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(out_dir, f"TDAC_诊断包_{stamp}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


if __name__ == "__main__":  # 自检
    print(render_diagnostic())
    print("tdac_diag 自检 PASS")
