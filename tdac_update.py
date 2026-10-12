# -*- coding: utf-8 -*-
"""tdac_update.py — 版本检查（B3）。

安全边界
  * **只有在用户明确同意后**才会发起网络请求；本模块自身不主动联网。
  * 版本来源**可配置**，默认留空。仓库尚未建立，因此**不预置任何虚构地址**；
    未配置时直接返回“未配置版本来源”，不做任何请求。
  * 只使用 **https**。
  * 发现新版本时**只显示版本号与官方下载页链接**，
    **不自动下载、不自动安装、不远程执行任何代码**。
  * 版本信息只接受极简 JSON：``{"version": "...", "downloadPage": "..."}``；
    其它字段一律忽略，多余的字段不会被使用。

本文件不含任何敏感数据。
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, Optional, Tuple

import tdac_paths

APP_VERSION = "0.1.0"

UPDATE_CONFIG_NAME = "update.json"
ENV_UPDATE_SOURCE = "TDAC_UPDATE_SOURCE"

# 允许展示的官方入口（仅链接，不自动打开、不自动下载）
OFFICIAL_SITE = "https://tdac.immigration.go.th/arrival-card/"

_TIMEOUT_SECONDS = 6


def update_config_path() -> str:
    return os.path.join(tdac_paths.config_dir(), UPDATE_CONFIG_NAME)


def load_update_config() -> Dict[str, str]:
    """读取版本来源配置。

    优先级：环境变量 TDAC_UPDATE_SOURCE > 数据目录 config/update.json > 空。
    未配置时返回空字符串，调用方据此提示“未配置版本来源”。
    """
    cfg: Dict[str, str] = {"versionSource": "", "downloadPage": ""}
    path = update_config_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                cfg["versionSource"] = str(data.get("versionSource") or "").strip()
                cfg["downloadPage"] = str(data.get("downloadPage") or "").strip()
        except Exception:
            pass
    env = os.environ.get(ENV_UPDATE_SOURCE, "").strip()
    if env:
        cfg["versionSource"] = env
    return cfg


def parse_version(text: str) -> Tuple[int, ...]:
    """把 "1.2.3" / "v1.2.3" / "1.2.3-beta.1" 解析为可比较的数字元组。

    预发布 / 构建后缀（``-beta``、``+build``）会被忽略，因此
    ``1.2.3-beta.1`` 与 ``1.2.3`` 视为同一版本（不会误判为更新）。
    """
    s = str(text).strip().lstrip("vV")
    for sep in ("-", "+", " "):
        if sep in s:
            s = s.split(sep, 1)[0]
    parts = []
    for chunk in s.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts) if parts else (0,)


def is_newer(remote: str, current: str = APP_VERSION) -> bool:
    a, b = parse_version(remote), parse_version(current)
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return a > b


def _default_fetch(url: str) -> str:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "tdac-local-fill"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:  # noqa: S310 (https enforced)
        return resp.read().decode("utf-8", errors="replace")


def check_for_update(current: str = APP_VERSION,
                     source: Optional[str] = None,
                     fetch: Optional[Callable[[str], str]] = None,
                     consent: bool = False) -> Dict[str, Any]:
    """检查是否有新版本。

    参数
      * ``consent`` —— 必须显式传 True（表示“用户已同意联网检查”），否则直接返回
        未检查状态，**不会发起任何网络请求**。
      * ``source``  —— 版本来源 URL；缺省时读取配置。
      * ``fetch``   —— 可注入的取数函数，便于离线单元测试。

    返回 dict：
      ``{"checked": bool, "reason": str, "current": str,
         "latest": str|None, "update_available": bool, "downloadPage": str|None}``
    """
    result: Dict[str, Any] = {
        "checked": False,
        "reason": "",
        "current": current,
        "latest": None,
        "update_available": False,
        "downloadPage": None,
    }

    cfg = load_update_config()
    url = (source if source is not None else cfg["versionSource"]).strip()
    page = cfg["downloadPage"].strip()

    if not consent:
        result["reason"] = "用户未同意联网检查版本，已跳过。"
        return result
    if not url:
        result["reason"] = ("未配置版本来源（可在 config/update.json 或环境变量 "
                            "TDAC_UPDATE_SOURCE 中设置）。当前仓库尚未建立，因此不预置地址。")
        return result
    if not url.lower().startswith("https://"):
        result["reason"] = "版本来源必须是 https:// 地址，已拒绝。"
        return result

    fetcher = fetch or _default_fetch
    try:
        raw = fetcher(url)
        data = json.loads(raw)
    except Exception as e:
        result["reason"] = f"获取版本信息失败：{type(e).__name__}"
        return result

    if not isinstance(data, dict):
        result["reason"] = "版本信息格式不正确（应为 JSON 对象）。"
        return result

    latest = str(data.get("version") or "").strip()
    if not latest:
        result["reason"] = "版本信息缺少 version 字段。"
        return result

    remote_page = str(data.get("downloadPage") or "").strip()
    if remote_page and not remote_page.lower().startswith("https://"):
        remote_page = ""

    result.update({
        "checked": True,
        "reason": "已获取版本信息。",
        "latest": latest,
        "update_available": is_newer(latest, current),
        "downloadPage": remote_page or page or None,
    })
    return result


def describe(result: Dict[str, Any]) -> str:
    """把检查结果转成中文说明（只含版本号与链接，不含任何个人信息）。"""
    if not result.get("checked"):
        return result.get("reason") or "未检查版本。"
    cur = result["current"]
    latest = result.get("latest")
    if not result.get("update_available"):
        return f"当前已是最新版本（{cur}）。"
    lines = [f"发现新版本：{latest}（当前 {cur}）。"]
    page = result.get("downloadPage")
    if page:
        lines.append(f"官方下载页：{page}")
    else:
        lines.append(f"官方入口：{OFFICIAL_SITE}")
    lines.append("本程序不会自动下载或安装；请自行前往上述页面获取。")
    return "\n".join(lines)


if __name__ == "__main__":  # 自检（不联网）
    print("当前版本：", APP_VERSION)
    print(describe(check_for_update(consent=False)))
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert is_newer("0.2.0", "0.1.0")
    assert not is_newer("0.1.0", "0.1.0")
    print("tdac_update 自检 PASS")
