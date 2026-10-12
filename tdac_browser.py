# -*- coding: utf-8 -*-
"""tdac_browser.py — 浏览器检测（B6）与会话/调试端口说明与残留检测（B7）。

设计原则
  * **不自动安装**浏览器、**不修改**系统默认浏览器，只检测 + 给出官方入口。
  * 浏览器启动参数保持不变：仅使用 ``--remote-debugging-port=<随机端口>``
    与独立的 ``--user-data-dir``。Chrome/Edge 在未显式指定
    ``--remote-debugging-address`` 时**只监听回环地址 127.0.0.1**，不会暴露到局域网。
  * 绝不新增任何会**扩大**远程调试访问范围的参数（例如
    ``--remote-allow-origins=*``、``--remote-debugging-address=0.0.0.0``）。
  * 残留检测一律是**只读**的：只查询进程命令行与残留文件，不杀进程、不删文件。

本文件不含任何敏感数据。
"""
from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, List, Optional, Tuple

# 只使用系统**已安装**的浏览器；不随包分发、不自动下载。
# 顺序即优先级：Chrome 优先，其次 Edge。
CANDIDATES: Tuple[Tuple[str, str], ...] = (
    ("Chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    ("Chrome", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ("Chrome", os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")),
    ("Edge", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ("Edge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
)

# 官方下载入口（仅提供链接，不自动跳转、不自动下载）
DOWNLOAD_PAGES: Dict[str, str] = {
    "Chrome": "https://www.google.cn/chrome/",
    "Edge": "https://www.microsoft.com/edge/download",
}

# B7：绝不允许出现在启动参数里的开关（它们会扩大远程调试的访问范围）
FORBIDDEN_SWITCHES: Tuple[str, ...] = (
    "--remote-allow-origins",
    "--remote-debugging-address",
    "--disable-web-security",
)


def unsafe_switches(args) -> List[str]:
    """返回启动参数里命中的“危险开关”列表（应为空）。"""
    hits: List[str] = []
    for a in args:
        low = str(a).strip().lower()
        for bad in FORBIDDEN_SWITCHES:
            if low.startswith(bad):
                hits.append(str(a))
    return hits


def assert_safe_switches(args) -> None:
    """启动浏览器前的硬校验：命中危险开关即拒绝启动。"""
    bad = unsafe_switches(args)
    if bad:
        raise ValueError("检测到会扩大远程调试访问范围的启动参数，已拒绝启动：" + ", ".join(bad))


def detect_browser() -> Tuple[Optional[str], Optional[str]]:
    """返回 (浏览器类型, 可执行文件路径)；未检测到返回 (None, None)。"""
    for kind, path in CANDIDATES:
        if os.path.exists(path):
            return kind, path
    return None, None


def find_browser() -> Optional[str]:
    """返回浏览器可执行文件路径，未找到返回 None。"""
    return detect_browser()[1]


def missing_browser_help() -> str:
    """浏览器缺失时的中文说明（含官方下载入口）。"""
    lines = [
        "未在本机检测到 Google Chrome 或 Microsoft Edge。",
        "",
        "本工具**不捆绑**浏览器，只使用你系统里已安装的 Chrome / Edge，",
        "因此需要你先自行安装其中之一（推荐 Chrome 或 Edge 稳定版，64 位）：",
        f"  · Chrome：{DOWNLOAD_PAGES['Chrome']}",
        f"  · Edge  ：{DOWNLOAD_PAGES['Edge']}",
        "",
        "安装完成后回到本程序重新检测即可。本程序不会自动安装浏览器，",
        "也不会修改你的系统默认浏览器设置。",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# B7：会话与调试端口
# --------------------------------------------------------------------------- #
SESSION_NOTES = (
    "浏览器会话说明\n"
    "  1. 独立用户目录：本工具用 --user-data-dir 指向专用目录，与你日常使用的 Chrome\n"
    "     完全隔离；不会读取或修改你的日常浏览记录、登录状态与书签。\n"
    "  2. 调试端口：仅使用 --remote-debugging-port=<随机端口>，且 Chrome/Edge 在未指定\n"
    "     --remote-debugging-address 时只监听回环地址 127.0.0.1；局域网内其它设备无法访问。\n"
    "     端口每次随机分配，用完即止，不会长期占用固定端口。\n"
    "  3. 人工接管：自动流程在失败或未完成时会**保留**浏览器页面，方便你手动继续。\n"
    "  4. 主动结束：只有在你明确选择“结束会话”时，程序才会关闭它自己启动的那个实例。\n"
    "  5. 残留检测：若上次异常退出（例如强制关机），可能残留占用专用用户目录的进程，\n"
    "     程序在启动前会做一次**只读**检测并提示，不会擅自结束你的日常浏览器。\n"
    "  6. 安全边界：绝不使用会扩大远程调试访问范围的参数\n"
    "     （例如 --remote-allow-origins=* 或 --remote-debugging-address=0.0.0.0）。"
)

_LEFT_OVER_MARKERS = ("SingletonLock", "SingletonCookie", "DevToolsActivePort")


def leftover_markers(profile_dir: str) -> List[str]:
    """只读列举专用用户目录里的会话残留标记文件（存在即列出）。"""
    found: List[str] = []
    for name in _LEFT_OVER_MARKERS:
        if os.path.exists(os.path.join(profile_dir, name)):
            found.append(name)
    return found


def list_stale_processes(profile_dir: str) -> List[int]:
    """只读查询：命令行里包含本工具专用用户目录的 chrome/msedge 进程 PID。

    只按 profile 目录精确筛选，绝不匹配用户日常浏览器；不结束任何进程。
    """
    if not profile_dir:
        return []
    ps = (
        "$d = $args[0]; "
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' OR Name='msedge.exe'\" "
        "-ErrorAction SilentlyContinue | "
        "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($d) } | "
        "ForEach-Object { $_.ProcessId }"
    )
    try:
        # 不用 text=True：PowerShell 在中文控制台的输出可能是 OEM 代码页（cp936），
        # 强制 UTF-8 解码会抛 UnicodeDecodeError。这里只关心数字 PID，容错解码即可。
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps, profile_dir],
            capture_output=True, timeout=25,
        )
        out = (r.stdout or b"").decode("utf-8", errors="ignore").splitlines()
        return [int(x) for x in out if x.strip().isdigit()]
    except Exception:
        return []


def detect_leftovers(profile_dir: str) -> Dict[str, Any]:
    """汇总残留情况（纯只读，供 GUI / CLI 提示用）。"""
    return {
        "profile_dir": profile_dir,
        "markers": leftover_markers(profile_dir),
        "pids": list_stale_processes(profile_dir),
    }


def describe_leftovers(info: Dict[str, Any]) -> str:
    """把 detect_leftovers 的结果转成中文说明。"""
    markers = info.get("markers") or []
    pids = info.get("pids") or []
    if not markers and not pids:
        return "未发现浏览器会话残留。"
    parts = []
    if pids:
        parts.append(f"仍有 {len(pids)} 个进程占用专用用户目录")
    if markers:
        parts.append("存在残留标记文件：" + "、".join(markers))
    tail = ("建议：关闭由本工具打开的浏览器窗口后重试；程序在启动前会再次只读检查，"
            "不会影响你日常使用的浏览器。")
    return "；".join(parts) + "。" + tail


if __name__ == "__main__":  # 自检
    kind, path = detect_browser()
    print("检测到浏览器：", kind, path)
    if kind is None:
        print(missing_browser_help())
