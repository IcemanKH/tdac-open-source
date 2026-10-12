# -*- coding: utf-8 -*-
"""tdac_paths.py — 程序与用户数据目录分离（决策 30）。

设计要点
  * 程序文件（源码、模板）位于代码目录，只读、可随发行包整体替换。
  * 用户数据（配置、DPAPI 密文、浏览器用户目录、日志、输出）默认位于
    当前 Windows 用户的专属数据目录：%LOCALAPPDATA%\\TDAC\\
  * 可用环境变量 TDAC_DATA_DIR 覆盖数据根目录（测试 / 便携场景）。
  * 删除受管数据只删除本模块管理、且真实位于数据根目录之下的路径；
    绝不接受数据根目录之外（尤其代码目录）的删除请求。

删除安全（本文件的核心约束）
  1. 必须显式确认（confirmed=True）才可能删除。
  2. 数据根目录不能是磁盘根目录、当前用户主目录，且与程序目录不得互相包含。
  3. 只删除固定的受管子目录（config / secrets / browser_profile / logs / output）。
  4. 所有包含关系判断都基于 **真实路径**（realpath），解析符号链接与目录联接（junction）。
  5. 目标若是符号链接 / 目录联接则拒绝删除，避免删除指向别处的链接时越界。
  6. 删除失败必须上报（抛出异常），绝不静默忽略；失败时返回已删/未删明细。

本文件不含任何敏感数据。
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
from typing import List, Tuple

APP_NAME = "TDAC"
DATA_DIR_ENV = "TDAC_DATA_DIR"


def _program_dir() -> str:
    """程序（资源）目录。

    * 源码运行时 = 本文件所在目录；
    * PyInstaller 冻结（便携版）后 = 解包/资源目录（onedir 下即 ``_internal``），
      随包资源（templates 等）都在这里。
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


PROGRAM_DIR = _program_dir()
TEMPLATE_CONFIG_DIR = os.path.join(PROGRAM_DIR, "templates", "config")

# 首次运行据此生成用户配置（不含 group.json，避免误触发团体流程）
SEED_FILES = ("profile.example.json", "trip.example.json", "dynamic.example.json")

# 允许删除的受管子目录（白名单：只认这几个名字）
MANAGED_SUBDIRS = ("config", "secrets", "browser_profile", "logs", "output")

_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def data_root() -> str:
    """用户数据根目录；可用 TDAC_DATA_DIR 覆盖。"""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return os.path.abspath(override)
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, APP_NAME)


def _sub(name: str) -> str:
    # 所有公开数据路径（包括读取路径）都先验证根目录；不能只在删除时保护，
    # 否则 TDAC_DATA_DIR 指向源码目录仍可能在初始化/读取/写入时造成污染。
    return os.path.join(assert_safe_data_root(), name)


def config_dir() -> str:
    return _sub("config")


def secrets_dir() -> str:
    return _sub("secrets")


def secrets_path() -> str:
    return os.path.join(secrets_dir(), "secrets.dat")


def group_secrets_path() -> str:
    return os.path.join(secrets_dir(), "group-secrets.dat")


def profile_dir() -> str:
    return _sub("browser_profile")


def log_dir() -> str:
    return _sub("logs")


def output_dir() -> str:
    return _sub("output")


def managed_root() -> str:
    return data_root()


def assert_safe_data_root() -> str:
    """返回当前数据根目录，若根目录不安全则拒绝任何受管访问。"""
    root = data_root()
    ok, reason = validate_data_root(root)
    if not ok:
        raise PermissionError(reason + "；已阻止对该数据根目录的读取、写入和初始化。")
    return root


def ensure_data_dirs() -> None:
    assert_safe_data_root()
    for d in (config_dir(), secrets_dir(), profile_dir(), log_dir(), output_dir()):
        os.makedirs(d, exist_ok=True)


def ensure_user_config() -> List[str]:
    """首次运行时从程序内模板生成用户配置；不覆盖任何已存在的文件。"""
    ensure_data_dirs()
    created: List[str] = []
    if not os.path.isdir(TEMPLATE_CONFIG_DIR):
        return created
    for name in SEED_FILES:
        src = os.path.join(TEMPLATE_CONFIG_DIR, name)
        if not os.path.exists(src):
            continue
        target = os.path.join(config_dir(), name[: -len(".example.json")] + ".json")
        if os.path.exists(target):
            continue
        shutil.copyfile(src, target)
        created.append(target)
    return created


# --------------------------------------------------------------------------- #
# 路径安全判定
# --------------------------------------------------------------------------- #
def realpath(path: str) -> str:
    """返回真实路径：解析符号链接 / 目录联接后的规范绝对路径。"""
    return os.path.realpath(os.path.abspath(path))


def is_reparse_point(path: str) -> bool:
    """判断路径是否为符号链接或 Windows 目录联接（junction）。

    目录联接本身是 reparse point，删除它只会移除链接、不会删除目标，
    但仍存在“链接指向别处、用户误以为在删受管数据”的风险，故一律拒绝删除。
    """
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & _REPARSE_FLAG)


def _is_inside(path: str, parent: str) -> bool:
    """真实路径包含判断（解析联接/符号链接后比较）。"""
    p = realpath(path)
    q = realpath(parent)
    return p == q or p.startswith(q + os.sep)


def _is_filesystem_root(path: str) -> bool:
    """是否为磁盘根目录（例如 C:\\ 或 /）。"""
    p = os.path.abspath(path)
    drive, tail = os.path.splitdrive(p)
    return tail in ("", os.sep, "/", "\\")


def validate_data_root(root: str = "") -> Tuple[bool, str]:
    """校验数据根目录是否允许作为受管数据目录。返回 (是否通过, 原因)。"""
    root = os.path.abspath(root or managed_root())
    real = realpath(root)
    prog = realpath(PROGRAM_DIR)
    if _is_filesystem_root(root):
        return False, f"拒绝：数据根目录不能是磁盘根目录：{root}"
    if _is_inside(real, prog) or _is_inside(prog, real):
        return False, (f"拒绝：数据根目录 {root} 与程序目录 {prog} 存在包含关系"
                       "（含通过符号链接/目录联接形成的包含）")
    # 若数据根目录里存在本程序的源码文件，说明这是某个代码检出目录（可能是原项目
    # 或其他克隆），绝不能把它当成受管数据目录来删除。
    for marker in ("tdac_fill.py", "tdac_paths.py"):
        if os.path.exists(os.path.join(real, marker)):
            return False, f"拒绝：数据根目录 {root} 看起来是程序源代码目录（含 {marker}）"
    home = realpath(os.path.expanduser("~"))
    if real == home:
        return False, f"拒绝：数据根目录不能是当前用户主目录：{root}"
    return True, "ok"


def _delete_target_guard(target: str, real_root: str) -> Tuple[bool, str]:
    """判断单个受管子目录是否可以安全删除。返回 (是否可删, 原因)。"""
    abs_target = os.path.abspath(target)
    if os.path.basename(abs_target) not in MANAGED_SUBDIRS:
        return False, f"名称不在受管白名单内：{abs_target}"
    if not _is_inside(abs_target, real_root):
        return False, f"真实路径位于数据根目录之外：{abs_target}"
    # 必须是数据根目录的直接子目录（真实路径的父目录 == 真实数据根）
    if os.path.dirname(realpath(abs_target)) != real_root:
        return False, f"不是数据根目录的直接子目录（可能被联接/链接）：{abs_target}"
    if is_reparse_point(abs_target):
        return False, f"是符号链接或目录联接，拒绝删除：{abs_target}"
    return True, "ok"


def _rmtree_strict(path: str) -> None:
    """递归删除，但任何子项删除失败都会抛出异常，绝不静默忽略。"""
    def _raise_onexc(func, p, exc):  # Python >= 3.12
        raise exc

    def _raise_onerror(func, p, exc_info):  # Python < 3.12
        raise exc_info[1]

    try:
        shutil.rmtree(path, onexc=_raise_onexc)
    except TypeError:
        # 旧版本 Python 不认 onexc 关键字，回退到 onerror
        shutil.rmtree(path, onerror=_raise_onerror)


def managed_paths() -> List[str]:
    """返回当前实际存在的受管数据目录。"""
    return [p for p in (config_dir(), secrets_dir(), profile_dir(), log_dir(), output_dir())
            if os.path.exists(p)]


# --------------------------------------------------------------------------- #
# 统一的受管路径写保护 + 原子写入（P2：写入与删除都走同一套路径判定）
#
# 删除已有 ``delete_managed_data``（只删受管子目录）；这里补齐**写入**侧：
# 任何要落进受管数据目录的文件，都必须先通过 ``assert_managed_write``，
# 避免路径拼接错误或符号链接把用户资料写到源码目录 / 系统目录去。
# 写入本身一律先写同目录临时文件、``flush`` + ``fsync`` 后再 ``os.replace``，
# 保证进程崩溃 / 断电时不会留下半截文件。
# --------------------------------------------------------------------------- #
def assert_managed_write(path: str) -> str:
    """校验“准备写入的路径”确实位于受管数据根目录之内。

    返回规范化后的绝对路径；越界（真实路径在数据根目录之外）时抛
    ``PermissionError``。判定基于 ``realpath``，因此符号链接 / 目录联接
    绕不过去；尚未创建的文件按“真实父目录”判定。
    """
    root = realpath(assert_safe_data_root())
    target = realpath(path)
    if target != root and not target.startswith(root + os.sep):
        raise PermissionError(
            f"拒绝写入受管数据目录之外的文件：{path}（受管根目录：{root}）")
    return os.path.abspath(path)


def atomic_write_bytes(path: str, data: bytes, *, fsync: bool = True) -> str:
    """原子写入字节：同目录临时文件 → flush/fsync → ``os.replace``。

    ``os.replace`` 在同一卷上是原子的，因此读方要么看到旧的完整内容，
    要么看到新的完整内容，不会读到半截文件。
    """
    target = assert_managed_write(path)
    directory = os.path.dirname(target)
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, "." + os.path.basename(target) + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        if fsync:
            os.fsync(fh.fileno())
    os.replace(tmp, target)
    return target


def atomic_write_text(path: str, text: str, *, encoding: str = "utf-8",
                      fsync: bool = True) -> str:
    """原子写入文本（UTF-8，不写 BOM）。"""
    return atomic_write_bytes(path, text.encode(encoding), fsync=fsync)


def atomic_write_json(path: str, data, *, fsync: bool = True) -> str:
    """原子写入 JSON（``ensure_ascii=False`` + 缩进 2，便于人工核对）。"""
    import json

    payload = json.dumps(data, ensure_ascii=False, indent=2)
    return atomic_write_text(path, payload, fsync=fsync)


def _guard_root() -> None:
    assert_safe_data_root()


def delete_managed_data(confirmed: bool = False) -> List[str]:
    """删除受管数据目录。

    * 必须 confirmed=True。
    * 只删除数据根目录下、名称在受管白名单内、且非符号链接/目录联接的直接子目录。
    * 任何目标删除失败都会抛 RuntimeError 并列出明细，绝不静默忽略。
    """
    if not confirmed:
        raise PermissionError("删除受管数据需要显式确认（confirmed=True）")
    _guard_root()
    root = os.path.abspath(managed_root())
    real_root = realpath(root)

    removed: List[str] = []
    errors: List[str] = []
    for name in MANAGED_SUBDIRS:
        target = os.path.join(root, name)
        if not os.path.exists(target):
            continue
        ok, reason = _delete_target_guard(target, real_root)
        if not ok:
            errors.append("跳过：" + reason)
            continue
        try:
            _rmtree_strict(target)
        except OSError as e:
            errors.append(f"删除失败 {target}：{type(e).__name__}: {e}")
        else:
            removed.append(target)

    if errors:
        raise RuntimeError("部分受管数据未能删除：\n" + "\n".join(errors))
    return removed
