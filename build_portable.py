# -*- coding: utf-8 -*-
"""build_portable.py — 生成免安装便携版（B2，PyInstaller onedir）。

只在**独立构建环境**内运行，不污染系统：

  ::  # 1) 一次性准备（已获授权；联网下载 playwright 与 pyinstaller）
      python -m venv .build\\venv
      .build\\venv\\Scripts\\python.exe -m pip install -r requirements.lock.txt
  ::  # 2) 构建
      .build\\venv\\Scripts\\python.exe build_portable.py

产物：``.build\\dist\\TDAC填写助手\\``（整个目录可直接拷走，免安装、免管理员）。
个人资料不在该目录内：默认写入 ``%LOCALAPPDATA%\\TDAC\\``，因此升级/替换程序不会覆盖。

G1 最终修复（P1-05）——构建来源与发行产物的对应关系：
  * **版本信息**：生成 PyInstaller 版本资源文件（``.build\\spec\\version_info.txt``），
    exe 属性中可见产品名 / 版本 / 版权；
  * **构建元数据**：输出 ``.build\\dist\\BUILD_INFO.json``，记录应用版本、Python 版本、
    构建期依赖版本、构建时间（UTC）、源码清单摘要与主程序 SHA-256；
  * **发行文件哈希**：输出 ``.build\\dist\\SHA256SUMS.txt``，覆盖产物目录内每个文件
    （外层发行 ZIP 及其 ``.sha256`` 校验文件除外，见下）；
  * **发行 ZIP**：``.build\\dist\\TDAC填写助手-<版本>-windows-x64.zip`` 由
    ``tools/make_release_zip.py`` 从**上面这一份**产物打包生成，本地与公开构建工作流
    复用同一步，避免“开发机旧 ZIP”混入；
  * **Python 版本一致性**：本脚本与 requirements*.txt、公开构建工作流统一要求
    **CPython 3.12（Windows x64，自带 tkinter）**，消除 3.12 / 3.13 配置不一致。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import platform
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(ROOT, ".build")
DIST = os.path.join(BUILD, "dist")
WORK = os.path.join(BUILD, "work")
SPECDIR = os.path.join(BUILD, "spec")
APP_NAME = "TDAC填写助手"

# 与 requirements.lock.txt / .github/workflows/build.yml 一致。
REQUIRED_PYTHON = "3.12"
SUPPORTED_PYTHON = ("3.12", "3.13")

HIDDEN_IMPORTS = [
    "tdac_app", "tdac_fill", "tdac_gui", "tdac_paths", "tdac_privacy",
    "tdac_sec", "tdac_synth", "tdac_group", "tdac_validate", "tdac_browser",
    "tdac_update", "tdac_diag", "tdac_draft", "tdac_gate",
]

# 进入发行包的运行时模块（用于源码清单摘要）
SOURCE_MODULES = [
    "tdac_app.py", "tdac_fill.py", "tdac_gui.py", "tdac_paths.py", "tdac_privacy.py",
    "tdac_sec.py", "tdac_synth.py", "tdac_group.py", "tdac_validate.py",
    "tdac_browser.py", "tdac_update.py", "tdac_diag.py", "tdac_draft.py", "tdac_gate.py",
    "set_secrets.py", "build_portable.py",
]

PORTABLE_README = """TDAC 填写助手 —— 免安装便携版
=====================================

【怎么用】
1. 直接双击本目录下的「TDAC填写助手.exe」即可打开中文界面，无需安装 Python。
2. 首次启动请按界面提示填写个人资料、行程与住宿、健康声明。
3. 需要时点击“离线演练（不触网）”先熟悉流程，再点“开始正式预填（打开官网）”。

【重要说明】
· 本程序【不会】替你提交、也不会替你勾选法律条款；
  “预填完成”不等于“正式申报成功”——是否成功以官网回执为准。
· 使用你系统里已安装的 Chrome 或 Edge，不捆绑浏览器、不自动安装、不改默认浏览器。
· 不需要管理员权限；支持放在中文路径下。
· 你的资料保存在 %LOCALAPPDATA%\\TDAC\\（与程序目录分离），
  因此替换/升级程序目录不会覆盖你的资料。
· 护照号与手机号用 Windows DPAPI 加密，仅当前 Windows 账户可解密。
· 草稿暂存同样使用 DPAPI 加密（secrets\\draft-secrets.dat），且与已确认的正式资料
  分开保存；「暂存」不等于「可以申报」。
· 这是 Beta 测试版，真实 TDAC 官网完整流程尚未验收；请自行核对后再在官网操作。
· 程序不自动提交、不自动同意条款、不绕过验证码；预填完成不代表申报成功。
· Windows 未签名程序可能显示安全提示；请只从项目正式发布页下载并核对 SHA-256。

【命令行（可选）】
  TDAC填写助手.exe --check             环境与配置检查
  TDAC填写助手.exe --dry-run           离线检查（合成资料、不联网）
  TDAC填写助手.exe --session-info      查看会话与调试端口说明
  TDAC填写助手.exe --export-diagnostic 导出脱敏诊断包
  TDAC填写助手.exe --delete-data       删除本程序管理的用户资料

本项目为非官方、免费的辅助工具，与泰国政府及移民局无任何隶属关系。
"""


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _app_version() -> str:
    with open(os.path.join(ROOT, "tdac_update.py"), encoding="utf-8") as fh:
        m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', fh.read())
    if not m:
        raise RuntimeError("未能在 tdac_update.py 中找到 APP_VERSION")
    return m.group(1)


def _py_ver() -> str:
    return "%d.%d.%d" % sys.version_info[:3]


def _check_python() -> int:
    """校验构建用解释器版本（与 requirements / CI 工作流保持一致）。"""
    cur = "%d.%d" % sys.version_info[:2]
    if cur == REQUIRED_PYTHON:
        print(f"Python 版本检查：{_py_ver()}（与要求 {REQUIRED_PYTHON} 一致）")
        return 0
    if cur in SUPPORTED_PYTHON:
        print(f"[警告] 当前 Python {_py_ver()}，而发行构建要求 {REQUIRED_PYTHON}；"
              f"产物版本信息会如实记录实际版本。")
        return 0
    print(f"[错误] 不支持的 Python 版本：{_py_ver()}；"
          f"请使用 CPython {REQUIRED_PYTHON}（Windows x64，自带 tkinter）。")
    return 4


def _write_version_file(version: str) -> str:
    """生成 PyInstaller 用的 Windows 版本资源文件。"""
    parts = [int(x) for x in re.findall(r"\d+", version)[:4]]
    while len(parts) < 4:
        parts.append(0)
    tup = "(%d, %d, %d, %d)" % tuple(parts[:4])
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={tup},
    prodvers={tup},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
    ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u'080404B0',
        [StringStruct(u'CompanyName', u'TDAC 填写助手（非官方免费工具）'),
        StringStruct(u'FileDescription', u'泰国电子入境卡（TDAC）本地半自动填写助手'),
        StringStruct(u'FileVersion', u'{version}'),
        StringStruct(u'InternalName', u'TDAC'),
        StringStruct(u'LegalCopyright', u'MIT License'),
        StringStruct(u'OriginalFilename', u'TDAC填写助手.exe'),
        StringStruct(u'ProductName', u'TDAC 填写助手'),
        StringStruct(u'ProductVersion', u'{version}')])
      ]),
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
  ]
)
"""
    os.makedirs(SPECDIR, exist_ok=True)
    path = os.path.join(SPECDIR, "version_info.txt")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    return path


def _installed(name: str) -> str:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return ""


def _build_info(version: str, out_dir: str, exe: str) -> dict:
    src_digest = hashlib.sha256()
    src_files = []
    for rel in SOURCE_MODULES:
        path = os.path.join(ROOT, rel)
        if os.path.exists(path):
            src_files.append({"path": rel, "sha256": _sha256(path)})
            src_digest.update(rel.encode())
            with open(path, "rb") as source_fh:
                src_digest.update(source_fh.read())
    source_sha = os.environ.get("TDAC_BUILD_SOURCE_SHA", "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{40}", source_sha):
        vcs = {"available": True, "commit": source_sha,
               "note": "由公开 CI 的 GitHub SHA 注入"}
    else:
        vcs = {"available": False,
               "note": "本地构建未提供有效 Git 提交 SHA；不得将其声称为公开可追溯构建"}
    return {
        "app": APP_NAME,
        "appVersion": version,
        "buildTimeUtc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "buildHost": {
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "pythonVersion": _py_ver(),
        "pythonRequired": REQUIRED_PYTHON,
        "pyinstallerVersion": _installed("pyinstaller"),
        "playwrightVersion": _installed("playwright"),
        "dependencyLock": "requirements.lock.txt",
        "sourceManifest": {"sha256": src_digest.hexdigest(), "files": src_files},
        "artifact": {
            "exe": os.path.relpath(exe, DIST).replace("\\", "/"),
            "exeSha256": _sha256(exe),
        },
        "vcs": vcs,
    }


def _write_sums(root: str, dest: str, skip: str = "") -> str:
    """对 ``root`` 下每个文件计算 SHA-256，写入 ``dest``。

    路径记录为**相对 ``root``**，因此清单可以放在 ``root`` 里、并在 ``root``
    处直接 ``sha256sum -c`` 校验（``skip`` 用于跳过清单自身，避免自我引用）。
    根目录下遗留的发行 ZIP 是清单的外层容器，不能纳入，否则下一次压缩覆盖该 ZIP 后
    会使清单指向旧哈希或不存在的文件；同理，由 ``tools/make_release_zip.py`` 生成的
    外层 ``*.zip.sha256``（它记录 ZIP 自身的哈希，而 ZIP 内部又含有本清单）也必须跳过，
    否则会形成 SHA256SUMS → .zip.sha256 → ZIP → SHA256SUMS 的循环引用。
    """
    skip_abs = os.path.abspath(skip) if skip else ""
    entries = []
    for dp, _dn, fn in os.walk(root):
        for name in fn:
            p = os.path.join(dp, name)
            if skip_abs and os.path.abspath(p) == skip_abs:
                continue
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            if "/" not in rel and (rel.lower().endswith(".zip")
                                   or rel.lower().endswith(".zip.sha256")):
                continue
            entries.append((rel, _sha256(p)))
    entries.sort(key=lambda e: e[0])
    with open(dest, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(f"{h}  {rel}" for rel, h in entries) + "\n")
    return dest


def main() -> int:
    rc = _check_python()
    if rc:
        return rc

    py = os.path.join(BUILD, "venv", "Scripts", "python.exe")
    if not os.path.exists(py):
        print("错误：未找到独立构建环境：", py)
        print("请先运行：python -m venv .build\\venv  并安装 requirements.lock.txt。")
        return 2

    version = _app_version()
    version_file = _write_version_file(version)

    sep = os.pathsep  # Windows 上为 ;
    add_data = [
        (os.path.join(ROOT, "templates"), "templates"),
        (os.path.join(ROOT, "config", "group.example.json"), "config"),
        (os.path.join(ROOT, "LICENSE"), "."),
        (os.path.join(ROOT, "使用说明.md"), "."),
    ]

    args = [
        py, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir",
        "--name", APP_NAME,
        "--distpath", DIST,
        "--workpath", WORK,
        "--specpath", SPECDIR,
        "--version-file", version_file,
        # playwright 自带 node 驱动，必须整包收集，否则运行时无法驱动浏览器
        "--collect-all", "playwright",
    ]
    for mod in HIDDEN_IMPORTS:
        args += ["--hidden-import", mod]
    for src, dest in add_data:
        if os.path.exists(src):
            args += ["--add-data", f"{src}{sep}{dest}"]
    args.append(os.path.join(ROOT, "tdac_app.py"))

    print("运行：", " ".join(args))
    r = subprocess.run(args, cwd=ROOT)
    if r.returncode != 0:
        print("构建失败，返回码：", r.returncode)
        return r.returncode

    out_dir = os.path.join(DIST, APP_NAME)
    exe = os.path.join(out_dir, f"{APP_NAME}.exe")
    if not os.path.exists(exe):
        print("未找到产物：", exe)
        return 3
    with open(os.path.join(out_dir, "便携版说明.txt"), "w", encoding="utf-8") as fh:
        fh.write(PORTABLE_README)

    info = _build_info(version, out_dir, exe)
    info_path = os.path.join(DIST, "BUILD_INFO.json")
    with open(info_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(info, fh, ensure_ascii=False, indent=2)

    # 发行文件哈希：覆盖 onedir + BUILD_INFO.json；根目录的外层 ZIP 另行计算，
    # 避免外层容器自引用。清单可在 dist/ 根部直接 `sha256sum -c SHA256SUMS.txt` 校验。
    sums_path = os.path.join(DIST, "SHA256SUMS.txt")
    _write_sums(DIST, sums_path, skip=sums_path)

    size = sum(os.path.getsize(os.path.join(dp, f))
               for dp, _dn, fn in os.walk(out_dir) for f in fn)
    print()
    print("构建完成：", out_dir)
    print("版本：", version, " Python：", _py_ver())
    print("总体积：%.1f MB" % (size / 1024 / 1024))
    print("主程序 SHA-256：", info["artifact"]["exeSha256"])
    print("构建元数据：", info_path)
    print("发行文件哈希：", sums_path)
    print("主程序：", exe)
    print("下一步：python tools/make_release_zip.py   "
          "# 由上面这份产物生成发行 ZIP 与 ZIP 的 SHA-256")
    return 0


if __name__ == "__main__":
    sys.exit(main())
