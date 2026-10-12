# -*- coding: utf-8 -*-
"""prepare_release.py — 生成**干净的发布暂存目录**（G1 最终修复 / P1-04）。

背景
  工作副本里混有内部资料（项目状态文档、docs/superpowers 设计与实施计划）、
  构建元数据（``.build/``）、浏览器用户目录、用户资料与密文、日志、PDF、
  验收截图等。它们都不应该进入公开发行物。

做法（白名单，而不是黑名单）
  只复制**明确允许**的文件/目录到 ``release/<版本>/``；任何不在白名单里的
  东西一律不进去。复制完成后对暂存目录做一次脱敏扫描，命中即失败。

  白名单：源码、合成测试与夹具、模板、许可证与用户说明、构建文件、公开
  构建工作流、发布工具。
  显式排除：内部文档、个人路径、``.build``、浏览器配置、用户资料、密文、
  日志、PDF、截图、缓存。

用法
  ::  # 生成暂存目录 + 清单 + 扫描报告
      .build\\venv\\Scripts\\python.exe tools\\prepare_release.py
  ::  # 只做检查（校验已有暂存目录是否干净），不写任何文件
      .build\\venv\\Scripts\\python.exe tools\\prepare_release.py --check release\\1.0.0

  现有内部文档不会被删除，只是不进入暂存目录。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from typing import Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE_DIR_NAME = "release"

# --- 允许进入发行物的**文件**（相对仓库根目录） ---------------------------- #
WHITELIST_FILES: Tuple[str, ...] = (
    # 程序源码
    "tdac_app.py", "tdac_fill.py", "tdac_gui.py", "tdac_paths.py", "tdac_privacy.py",
    "tdac_sec.py", "tdac_validate.py", "tdac_draft.py", "tdac_gate.py", "tdac_group.py",
    "tdac_synth.py", "tdac_browser.py", "tdac_update.py", "tdac_diag.py",
    # 辅助脚本与构建文件
    "set_secrets.py", "build_portable.py",
    "requirements.txt", "requirements-build.txt", "requirements.lock.txt",
    # 许可证 / 说明 / 安全声明
    "LICENSE", "README.md", "SECURITY.md", "THIRD_PARTY_LICENSES.md", "使用说明.md",
    # 启动脚本（源码运行方式，公开说明里已引用）
    "一键启动TDAC.bat", "启动泰国入境卡填写.cmd", ".gitignore", ".gitattributes",
    # 团队模板（合成，不含真实资料）
    "config/group.example.json",
)

# --- 允许进入发行物的**目录**（递归复制，仍受排除规则约束） ----------------- #
WHITELIST_DIRS: Tuple[str, ...] = (
    "templates",      # 配置模板 / 同行人必填信息表模板
    "tests",          # 合成测试（含 fixtures/ 下的本地模拟页）
    "tools",          # 发布与构建工具
    ".github",        # 公开构建工作流
)

# --- 永不进入发行物的目录名 / 文件名（即使落在白名单目录里） ---------------- #
FORBIDDEN_DIR_NAMES = {
    "__pycache__", ".build", ".venv", "venv", "env", ".git", "node_modules",
    "browser_profile", "secrets", "logs", "output", "输出", "docs", ".idea", ".vscode",
}
FORBIDDEN_FILE_PATTERNS = (
    re.compile(r"^draft\.json$"),
    re.compile(r".*\.dat$"),
    re.compile(r".*\.log$"),
    re.compile(r".*\.pdf$"),
    re.compile(r".*\.png$", re.I),
    re.compile(r".*\.jpg$", re.I),
    re.compile(r".*\.jpeg$", re.I),
    re.compile(r".*\.webp$", re.I),
    re.compile(r".*\.spec$"),
    re.compile(r".*\.pyc$"),
    re.compile(r"^profile\.json$"),
    re.compile(r"^trip\.json$"),
    re.compile(r"^dynamic\.json$"),
    re.compile(r"^group\.json$"),
    re.compile(r"^secrets\.dat$"),
    re.compile(r"^group-secrets\.dat$"),
    re.compile(r"^draft-secrets\.dat$"),
    re.compile(r".*本地.*\.json$"),
)

# --- 内部资料（明确不进入发行物，但保留在工作副本中，不删除） ---------------- #
INTERNAL_KEEP_IN_WORKTREE: Tuple[str, ...] = (
    "项目全貌与当前状态.md",
    "docs/superpowers/",
    "验收截图/",
)

# --- 脱敏扫描：暂存目录里**绝不允许**出现的内容 ---------------------------- #
_PRIVATE_PATH = "D:" + chr(92) + "入境卡填写"        # 私有项目本机绝对路径
_USER_HOME_RE = re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"']+")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_CN_MOBILE_RE = re.compile(r"\b1[3-9]\d{9}\b")
_PASSPORT_RE = re.compile(r"\b[Ee]\d{8}\b")
_CN_ID_RE = re.compile(r"\b\d{17}[\dXx]\b")

# 允许出现的示例值（合成 / 占位），避免误报阻断发布
_ALLOWED_SAMPLE_TOKENS = (
    "tester@example.invalid", "name@example.com", "example.invalid", "example.com",
    "E12345678", "E00000000", "13000000000", "13800138000", "1380013800",
    "MU0000", "MU0001", "E11111111", "E99999999", "E22222222", "E33333333",
    "E44444444", "E55555555", "00000",
)

# 合成测试专用：域名 / 用户名段（明确不是真实数据）
_SYNTHETIC_EMAIL_DOMAINS = ("example.com", "example.invalid", "example.org",
                            "b.com", "test.local", "localhost", "example")
_SYNTHETIC_USER_SEGMENTS = ("someone", "somebody", "user", "username", "name",
                            "test", "tester", "example", "redacted", "已隐藏", "xxx", "x")
_SYNTHETIC_PHONE_TOKENS = ("13000000000", "13800138000", "13800138001",
                           "13900139000", "15000000000", "18600000000",
                           "1300000000", "1380013800")


def _is_synthetic_digits(digits: str) -> bool:
    """数字串是否为明显的合成值：全同数字，或严格递增 / 递减序列。"""
    if len(set(digits)) == 1:
        return True
    asc = all(int(digits[i + 1]) - int(digits[i]) == 1 for i in range(len(digits) - 1))
    desc = all(int(digits[i + 1]) - int(digits[i]) == -1 for i in range(len(digits) - 1))
    return asc or desc


def _synthetic_email(addr: str) -> bool:
    domain = addr.rsplit("@", 1)[-1].lower()
    return any(domain == d or domain.endswith("." + d) for d in _SYNTHETIC_EMAIL_DOMAINS)


def _synthetic_home(path_match: str) -> bool:
    seg = path_match.replace("\\", "/").rstrip("/").split("/")[-1].lower()
    return seg in _SYNTHETIC_USER_SEGMENTS


def _synthetic_passport(token: str) -> bool:
    digits = token[1:]
    if token in _ALLOWED_SAMPLE_TOKENS:
        return True
    return _is_synthetic_digits(digits)


def _synthetic_phone(token: str) -> bool:
    if token in _SYNTHETIC_PHONE_TOKENS or token in _ALLOWED_SAMPLE_TOKENS:
        return True
    return _is_synthetic_digits(token)

_SCAN_EXTENSIONS = {
    ".py", ".md", ".txt", ".json", ".csv", ".yml", ".yaml", ".bat", ".cmd",
    ".html", ".htm", ".toml", ".cfg", ".ini", "", ".gitignore",
}


def app_version() -> str:
    """从 tdac_update.py 读取版本号（正则解析，避免导入副作用）。"""
    with open(os.path.join(ROOT, "tdac_update.py"), encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    if not m:
        raise RuntimeError("未能在 tdac_update.py 中找到 APP_VERSION")
    return m.group(1)


def release_root(version: str = "") -> str:
    return os.path.join(ROOT, RELEASE_DIR_NAME, version or app_version())


def _is_forbidden(rel_path: str) -> bool:
    parts = rel_path.replace("\\", "/").split("/")
    if any(p in FORBIDDEN_DIR_NAMES for p in parts[:-1]):
        return True
    name = parts[-1]
    if name in FORBIDDEN_DIR_NAMES:
        return True
    return any(p.match(name) for p in FORBIDDEN_FILE_PATTERNS)


def collect_whitelist() -> List[str]:
    """返回白名单展开后的相对路径列表（仅包含实际存在的文件）。"""
    out: List[str] = []
    for rel in WHITELIST_FILES:
        if os.path.isfile(os.path.join(ROOT, rel)) and not _is_forbidden(rel):
            out.append(rel.replace("\\", "/"))
    for dirname in WHITELIST_DIRS:
        base = os.path.join(ROOT, dirname)
        if not os.path.isdir(base):
            continue
        for dp, dns, fns in os.walk(base):
            dns[:] = [d for d in dns if d not in FORBIDDEN_DIR_NAMES]
            for fn in fns:
                abs_p = os.path.join(dp, fn)
                rel = os.path.relpath(abs_p, ROOT).replace("\\", "/")
                if _is_forbidden(rel):
                    continue
                out.append(rel)
    return sorted(set(out))


def scan_forbidden(staging_root: str) -> List[Tuple[str, str]]:
    """对暂存目录做脱敏扫描，返回 ``[(相对路径, 命中说明), ...]``。"""
    hits: List[Tuple[str, str]] = []
    for dp, dns, fns in os.walk(staging_root):
        dns[:] = [d for d in dns if d not in FORBIDDEN_DIR_NAMES]
        for fn in fns:
            abs_p = os.path.join(dp, fn)
            rel = os.path.relpath(abs_p, staging_root).replace("\\", "/")
            if _is_forbidden(rel):
                hits.append((rel, "命中排除规则（不应进入发行物的文件类型/名称）"))
                continue
            for why in dict.fromkeys(_scan_text_rel(rel, abs_p)):
                hits.append((rel, why))
    return hits


def _scan_text_rel(rel: str, abs_p: str) -> List[str]:
    """同 :func:`_scan_text`，但直接针对暂存目录中的文件。"""
    hits: List[str] = []
    ext = os.path.splitext(rel)[1].lower()
    if ext not in _SCAN_EXTENSIONS and os.path.basename(rel) != ".gitignore":
        return hits
    try:
        with open(abs_p, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return hits
    if _PRIVATE_PATH in text:
        hits.append("私有项目绝对路径")
    for m in _USER_HOME_RE.finditer(text):
        if not _synthetic_home(m.group(0)):
            hits.append("本机用户目录绝对路径")
            break
    for m in _EMAIL_RE.finditer(text):
        if not _synthetic_email(m.group(0)):
            hits.append("疑似真实邮箱")
            break
    for m in _CN_MOBILE_RE.finditer(text):
        if not _synthetic_phone(m.group(0)):
            hits.append("疑似真实手机号")
            break
    for m in _PASSPORT_RE.finditer(text):
        if not _synthetic_passport(m.group(0)):
            hits.append("疑似真实护照号")
            break
    if _CN_ID_RE.search(text):
        hits.append("疑似身份证号")
    return hits


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_staging(dest: str = "") -> Dict[str, object]:
    """生成干净的发布暂存目录，返回摘要信息。"""
    version = app_version()
    dest = dest or release_root(version)
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest, exist_ok=True)
    files = collect_whitelist()
    for rel in files:
        src = os.path.join(ROOT, rel)
        dst = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    hits = scan_forbidden(dest)
    manifest = write_manifest(dest, files)
    report = write_scan_report(dest, hits)
    return {"dest": dest, "version": version, "files": files,
            "hits": hits, "manifest": manifest, "report": report}


def write_manifest(dest: str, files: List[str]) -> str:
    lines = []
    for rel in sorted(files):
        lines.append(f"{_sha256(os.path.join(dest, rel))}  {rel}")
    path = os.path.join(dest, "MANIFEST.sha256")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def write_scan_report(dest: str, hits: List[Tuple[str, str]]) -> str:
    version = app_version()
    lines = [
        f"# 发布暂存目录脱敏扫描报告（版本 {version}）",
        "",
        "本报告由 tools/prepare_release.py 生成。白名单机制：只有明确列出的源码、",
        "合成测试与夹具、模板、许可证与用户说明、构建文件、公开构建工作流、发布工具",
        "才会进入本目录；内部文档、个人路径、.build、浏览器配置、用户资料、密文、",
        "日志、PDF、截图一律不进入。",
        "",
        f"扫描结果：{'PASS' if not hits else 'FAIL'}（命中 {len(hits)} 处）",
        "",
    ]
    if hits:
        lines.append("| 文件 | 命中说明 |")
        lines.append("|---|---|")
        for rel, why in hits:
            lines.append(f"| {rel} | {why} |")
    else:
        lines.append("未发现私有绝对路径、本机用户目录、真实邮箱 / 手机号 / 护照号 / 身份证号。")
    lines.append("")
    lines.append("说明：扫描只针对文本类文件；二进制文件不进入本目录（见排除规则）。")
    path = os.path.join(dest, "RELEASE_SCAN_REPORT.md")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def check_only(target: str) -> int:
    """校验已有暂存目录：文件集合必须等于白名单，且扫描无命中。"""
    hits = scan_forbidden(target)
    present = set()
    for dp, dns, fns in os.walk(target):
        dns[:] = [d for d in dns if d not in FORBIDDEN_DIR_NAMES]
        for fn in fns:
            rel = os.path.relpath(os.path.join(dp, fn), target).replace("\\", "/")
            if rel in ("MANIFEST.sha256", "RELEASE_SCAN_REPORT.md"):
                continue
            present.add(rel)
    expected = set(collect_whitelist())
    extra = sorted(present - expected)
    missing = sorted(expected - present)
    print("暂存目录：", target)
    print("文件数：", len(present), "（白名单：", len(expected), "）")
    if extra:
        print("[FAIL] 出现白名单之外的文件：")
        for rel in extra:
            print("  -", rel)
    if missing:
        print("[FAIL] 缺少白名单中的文件：")
        for rel in missing:
            print("  -", rel)
    if hits:
        print("[FAIL] 脱敏扫描命中：")
        for rel, why in hits:
            print(f"  - {rel}: {why}")
    ok = not extra and not missing and not hits
    print("结论：", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: List[str] = None) -> int:
    ap = argparse.ArgumentParser(description="生成干净的发布暂存目录并做脱敏扫描")
    ap.add_argument("--check", metavar="DIR", default=None,
                    help="只检查已有暂存目录，不写任何文件")
    ap.add_argument("--dest", default="", help="自定义暂存目录（默认 release/<版本>）")
    args = ap.parse_args(argv)

    if args.check:
        return check_only(args.check)

    info = build_staging(args.dest)
    print("发布暂存目录：", info["dest"])
    print("版本：", info["version"])
    print("纳入文件数：", len(info["files"]))
    print("清单：", info["manifest"])
    print("扫描报告：", info["report"])
    hits = info["hits"]
    if hits:
        print()
        print("[FAIL] 脱敏扫描命中，发布暂存目录不可用于发行：")
        for rel, why in hits:
            print(f"  - {rel}: {why}")
        return 1
    print()
    print("脱敏扫描：PASS（无私有路径 / 真实个人资料）")
    print("内部文档（", "、".join(INTERNAL_KEEP_IN_WORKTREE),
          "）仍保留在工作副本中，未进入暂存目录、也未删除。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
