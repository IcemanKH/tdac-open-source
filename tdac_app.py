# -*- coding: utf-8 -*-
"""tdac_app.py — 便携版单一入口（B2）。

* 双击（无参数）→ 启动简体中文图形界面；
* 带参数     → 交给 ``tdac_fill`` 的命令行模式（--check / --dry-run / --session-info …）。

控制台窗口只在命令行模式下保留；进入图形界面时会自动隐藏，避免打扰普通用户。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _hide_console() -> None:
    """隐藏 Windows 控制台窗口（图形界面模式下用；失败不影响运行）。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


def _setup_console_encoding() -> None:
    """让 cmd.exe 下的中文输出不乱码（命令行模式用）。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> int:
    argv = sys.argv[1:]
    # 无论图形界面还是命令行模式，先统一控制台编码，
    # 避免中文提示在 cmd.exe / 重定向输出时乱码（图形界面下控制台会被隐藏，无副作用）。
    _setup_console_encoding()
    if not argv:
        from tdac_gui import main as gui_main
        _hide_console()
        return int(gui_main([]) or 0)

    if argv[0] == "--gui":
        from tdac_gui import main as gui_main
        _hide_console()
        return int(gui_main(argv[1:]) or 0)

    import tdac_fill
    return int(tdac_fill.main() or 0)


if __name__ == "__main__":
    sys.exit(0 if main() == 0 else 1)
