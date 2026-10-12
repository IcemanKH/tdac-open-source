# -*- coding: utf-8 -*-
"""make_release_zip.py — 由**同一次构建**的便携版产物生成发行 ZIP 与其 SHA-256。

定位（G2 最小发布工程补丁）
  ``build_portable.py`` 只负责把源码构建成 onedir 目录，并输出
  ``.build/dist/{BUILD_INFO.json,SHA256SUMS.txt}``。本脚本**只做打包与哈希**，
  不重新开发压缩系统：沿用本地已验证的「把 onedir 与构建元数据打成一个 ZIP，
  再对 ZIP 计算 SHA-256」的做法，把它固化成**本地与 CI 可复用、可复核**的一步。

产物（默认写入 ``.build/dist/``）
  * ``TDAC填写助手-<版本>-windows-x64.zip``
      ZIP 根目录结构与本地已验证的 ZIP 一致（与 README 中的命名/校验示例一致）::

        TDAC填写助手/          ← PyInstaller onedir（含 EXE 与 _internal）
        BUILD_INFO.json
        SHA256SUMS.txt

  * ``TDAC填写助手-<版本>-windows-x64.zip.sha256``
      ``<sha256>  <ZIP 文件名>``（LF 换行，兼容 ``sha256sum -c``）。
      注意：外层 ZIP 与其校验文件**不进入** ``SHA256SUMS.txt``（``build_portable.py``
      会按根目录 ``*.zip`` / ``*.zip.sha256`` 规则跳过），避免自引用与循环引用。

一致性保证（任一条不满足即非零退出，且绝不留下半成品 ZIP）
  1. ``BUILD_INFO.json`` 的应用版本 == ``tdac_update.py`` 的 ``APP_VERSION``；
  2. ``BUILD_INFO.json`` 记录的 EXE 路径与 ``exeSha256`` == 磁盘上实际的 EXE
     （用于识别“元数据与产物混装”）；
  3. ``SHA256SUMS.txt`` 的覆盖集合 == onedir 全部文件 + ``BUILD_INFO.json``
     （不含清单自身；旧 ZIP / 旧校验文件若留在目录里会被判为“多出”），
     且逐条哈希与磁盘一致（旧产物混在目录里无法蒙混过关）；
  4. ZIP **先写到临时文件**，回读自检通过后**才**落盘：文件条目集合与磁盘一致、
     逐条目内容哈希与磁盘一致、整体 CRC 无损。落盘用「备份旧 ZIP → 替换 ZIP →
     替换 ``.sha256`` → 失败则回滚旧 ZIP」的**两步提交 + 补偿回滚**——注意这**不是**
     跨文件原子事务（见 :func:`promote_zip`）；任一步失败都保留原有有效 ZIP 与校验
     文件，绝不留下半成品 ZIP 或指向旧 ZIP 的新校验文件；
  5. 若设置了环境变量 ``TDAC_BUILD_SOURCE_SHA``（公开 CI 注入的提交 SHA）或传入
     ``--require-vcs-sha``，则要求 ``BUILD_INFO.json`` 的 ``vcs.commit`` 与之相同 ——
     用于证明 ZIP 来自**本次** CI 构建，而不是开发机上的旧包。

可复现性
  ZIP 内全部条目的时间戳统一取自 ``BUILD_INFO.json`` 的 ``buildTimeUtc``（即**构建时刻**，
  而不是打包时刻），条目顺序按名称排序。因此对**同一份** onedir 重复打包，ZIP 的 SHA-256
  完全一致，便于独立复核。

用法
  ::  # 打包 + 自检 + 写校验文件
      .build\\venv\\Scripts\\python.exe tools\\make_release_zip.py
  ::  # 只校验（不写任何文件）：核对已有 ZIP 与同次构建产物是否一致
      .build\\venv\\Scripts\\python.exe tools\\make_release_zip.py --verify
      .build\\venv\\Scripts\\python.exe tools\\make_release_zip.py --verify <ZIP 路径>
  ::  # 输出到其它目录（本地验证用，避免覆盖正式产物）
      .build\\venv\\Scripts\\python.exe tools\\make_release_zip.py --out <目录>

退出码：0 成功 / 一致；1 一致性校验失败或写出发行物失败；2 输入缺失或用法错误。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, ".build", "dist")
APP_NAME = "TDAC填写助手"
BUILD_INFO_NAME = "BUILD_INFO.json"
SUMS_NAME = "SHA256SUMS.txt"
ZIP_SUFFIX = "-windows-x64.zip"
CHUNK = 1024 * 1024

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_USAGE = 2


class InputError(Exception):
    """输入缺失 / 用法错误（退出码 2）。"""


class ConsistencyError(Exception):
    """一致性校验失败（退出码 1）。"""


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def app_version() -> str:
    """从 tdac_update.py 读取版本号（正则解析，避免导入副作用）。"""
    with open(os.path.join(ROOT, "tdac_update.py"), encoding="utf-8") as fh:
        m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', fh.read())
    if not m:
        raise InputError("未能在 tdac_update.py 中找到 APP_VERSION")
    return m.group(1)


def zip_basename(version: str) -> str:
    """与 README 约定一致的发行 ZIP 文件名。"""
    return f"{APP_NAME}-{version}{ZIP_SUFFIX}"


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _remove_if_exists(path: str) -> None:
    """清理临时/备份文件；**绝不**让清理动作掩盖原始异常。"""
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _dos_time(build_info: dict) -> Tuple[int, int, int, int, int, int]:
    """把 BUILD_INFO.json 的 buildTimeUtc 解析成 ZIP 的 DOS 时间元组。"""
    raw = str(build_info.get("buildTimeUtc", "")).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})", raw)
    if not m:
        raise ConsistencyError(f"{BUILD_INFO_NAME} 的 buildTimeUtc 无法解析：{raw!r}")
    year, month, day, hour, minute, second = (int(x) for x in m.groups())
    if year < 1980:
        raise ConsistencyError("buildTimeUtc 早于 ZIP 可表示的年份下限（1980）")
    return (year, month, day, hour, minute, second)


# --------------------------------------------------------------------------- #
# 读取并核对同次构建产物
# --------------------------------------------------------------------------- #
def dist_files(dist: str) -> Dict[str, str]:
    """ZIP 内应有的**文件**条目：``arcname -> 磁盘绝对路径``。"""
    app_dir = os.path.join(dist, APP_NAME)
    for p in (app_dir,
              os.path.join(dist, BUILD_INFO_NAME),
              os.path.join(dist, SUMS_NAME)):
        if not os.path.exists(p):
            raise InputError(f"缺少同次构建产物：{p}（请先运行 build_portable.py）")
    out: Dict[str, str] = {BUILD_INFO_NAME: os.path.join(dist, BUILD_INFO_NAME),
                           SUMS_NAME: os.path.join(dist, SUMS_NAME)}
    for dp, dns, fns in os.walk(app_dir):
        dns.sort()
        for fn in sorted(fns):
            p = os.path.join(dp, fn)
            out[os.path.relpath(p, dist).replace(os.sep, "/")] = p
    return out


def dist_dirs(dist: str) -> List[str]:
    """ZIP 内需要显式写入的**目录**条目（POSIX 风格、以 ``/`` 结尾）。"""
    app_dir = os.path.join(dist, APP_NAME)
    out = {APP_NAME + "/"}
    for dp, dns, _fns in os.walk(app_dir):
        for d in dns:
            rel = os.path.relpath(os.path.join(dp, d), dist).replace(os.sep, "/")
            out.add(rel + "/")
    return sorted(out)


def check_build_info(dist: str, version: str) -> dict:
    """版本与产物对应关系：BUILD_INFO.json 必须描述**磁盘上这一份**产物。"""
    info_path = os.path.join(dist, BUILD_INFO_NAME)
    try:
        info = _read_json(info_path)
    except (OSError, ValueError) as exc:
        raise ConsistencyError(f"{BUILD_INFO_NAME} 无法解析：{exc}") from exc
    if not isinstance(info, dict):
        raise ConsistencyError(f"{BUILD_INFO_NAME} 顶层必须是对象")
    got = str(info.get("appVersion", ""))
    if got != version:
        raise ConsistencyError(
            f"{BUILD_INFO_NAME} 的 appVersion={got!r} 与 tdac_update.py 的 {version!r} 不一致")
    artifact = info.get("artifact")
    if not isinstance(artifact, dict):
        raise ConsistencyError(f"{BUILD_INFO_NAME} 缺少 artifact 段")
    exe_rel = str(artifact.get("exe", "")).replace("\\", "/")
    if not exe_rel:
        raise ConsistencyError(f"{BUILD_INFO_NAME} 缺少 artifact.exe")
    if not exe_rel.startswith(APP_NAME + "/"):
        raise ConsistencyError(f"{BUILD_INFO_NAME} 的 artifact.exe 路径不在 onedir 内：{exe_rel}")
    exe_abs = os.path.join(dist, *exe_rel.split("/"))
    if not os.path.isfile(exe_abs):
        raise ConsistencyError(f"{BUILD_INFO_NAME} 记录的 EXE 在磁盘上不存在：{exe_rel}")
    recorded = str(artifact.get("exeSha256", "")).lower()
    actual = _sha256(exe_abs)
    if recorded != actual:
        raise ConsistencyError(
            "BUILD_INFO.json 的 exeSha256 与磁盘上的 EXE 不一致"
            f"（记录 {recorded[:16]}… / 实际 {actual[:16]}…），疑似元数据与产物混装")
    return info


def check_sums(dist: str, files: Dict[str, str]) -> int:
    """SHA256SUMS.txt 必须恰好覆盖 onedir 全部文件 + BUILD_INFO.json，且逐条一致。"""
    sums_path = os.path.join(dist, SUMS_NAME)
    listed: Dict[str, str] = {}
    with open(sums_path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("  ", 1)
            if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
                raise ConsistencyError(f"{SUMS_NAME} 第 {lineno} 行格式不正确：{line!r}")
            listed[parts[1]] = parts[0]
    # 清单不列出它自己（build_portable.py 用 skip= 跳过自身），因此期望集合要排除它。
    expected = {rel for rel in files if rel != SUMS_NAME}
    missing = sorted(expected - set(listed))
    extra = sorted(set(listed) - expected)
    if missing or extra:
        raise ConsistencyError(
            f"{SUMS_NAME} 与同次构建产物不一致：缺少 {missing[:5]}；多出 {extra[:5]}")
    for rel, recorded in listed.items():
        actual = _sha256(files[rel])
        if actual != recorded:
            raise ConsistencyError(
                f"{SUMS_NAME} 中 {rel} 的哈希与磁盘不一致（产物已被改动）")
    return len(listed)


def check_vcs(info: dict, expected_sha: str) -> str:
    """可选：要求 BUILD_INFO.json 的 vcs 提交与本次构建提交一致。"""
    vcs = info.get("vcs")
    if not isinstance(vcs, dict):
        vcs = {}
    commit = str(vcs.get("commit", "")).lower()
    if expected_sha:
        if commit != expected_sha:
            raise ConsistencyError(
                f"{BUILD_INFO_NAME} 的 vcs.commit={commit or '(空)'!r} 与本次构建提交 "
                f"{expected_sha!r} 不一致：该 ZIP 不是本次 CI 构建产出的")
        return commit
    return commit if vcs.get("available") else ""


# --------------------------------------------------------------------------- #
# 打包与回读自检
# --------------------------------------------------------------------------- #
def build_zip(dist: str, out_dir: str, name: str, stamp: Tuple[int, int, int, int, int, int],
              files: Dict[str, str], dirs: List[str]) -> str:
    """按固定顺序与固定时间戳把 ZIP 写到 ``<name>.part``，返回该临时路径。

    本函数**不**替换正式 ZIP：正式文件由 :func:`promote_zip` 在回读自检通过后统一落盘，
    从而保证校验失败时不覆盖原有有效 ZIP。
    """
    os.makedirs(out_dir, exist_ok=True)
    tmp = os.path.join(out_dir, name) + ".part"
    if os.path.exists(tmp):
        os.remove(tmp)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel in dirs:
                info = zipfile.ZipInfo(rel, date_time=stamp)
                info.compress_type = zipfile.ZIP_STORED
                info.external_attr = 0x10          # FILE_ATTRIBUTE_DIRECTORY
                zf.writestr(info, b"")
            for arc in sorted(files):
                info = zipfile.ZipInfo(arc, date_time=stamp)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0x20          # FILE_ATTRIBUTE_ARCHIVE
                with open(files[arc], "rb") as src, zf.open(info, "w") as dst:
                    shutil.copyfileobj(src, dst, CHUNK)
    except BaseException:
        _remove_if_exists(tmp)                     # 写出中途失败不留半成品
        raise
    return tmp


def verify_zip(zip_path: str, dist: str) -> Dict[str, int]:
    """回读 ZIP：整体无损 + 文件条目集合与磁盘一致 + 逐条目内容哈希与磁盘一致。"""
    if not os.path.isfile(zip_path):
        raise InputError(f"找不到待校验的 ZIP：{zip_path}")
    files = dist_files(dist)
    with zipfile.ZipFile(zip_path) as zf:
        broken = zf.testzip()
        if broken is not None:
            raise ConsistencyError(f"ZIP 整体 CRC 校验失败，首个损坏条目：{broken}")
        infos = zf.infolist()
        file_names = [i.filename for i in infos if not i.filename.endswith("/")]
        if len(file_names) != len(set(file_names)):
            raise ConsistencyError("ZIP 内存在重复的文件条目名")
        got = set(file_names)
        missing = sorted(set(files) - got)
        extra = sorted(got - set(files))
        if missing or extra:
            raise ConsistencyError(
                f"ZIP 文件条目与同次构建产物不一致：缺少 {missing[:5]}；多出 {extra[:5]}")
        for name in file_names:
            h = hashlib.sha256()
            size = 0
            with zf.open(name) as fh:
                for chunk in iter(lambda: fh.read(CHUNK), b""):
                    h.update(chunk)
                    size += len(chunk)
            src = files[name]
            if size != os.path.getsize(src):
                raise ConsistencyError(f"ZIP 内 {name} 的大小与磁盘不一致")
            if h.hexdigest() != _sha256(src):
                raise ConsistencyError(f"ZIP 内 {name} 的内容与磁盘不一致")
        return {"files": len(file_names), "dirs": len(infos) - len(file_names)}


def write_sidecar(zip_path: str) -> Tuple[str, str]:
    """写出 ``<zip>.sha256``（``<sha256>  <文件名>``，LF 换行）。"""
    digest = _sha256(zip_path)
    side = zip_path + ".sha256"
    with open(side, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{digest}  {os.path.basename(zip_path)}\n")
    return digest, side


def promote_zip(tmp_zip: str, dest_zip: str) -> Tuple[str, str]:
    """把**已通过回读自检**的临时 ZIP 与配套 ``.sha256`` 落盘（两步提交 + 失败回滚）。

    调用前 ``tmp_zip`` 必须已经 :func:`verify_zip` 通过。校验文件内容指向的是正式 ZIP
    文件名（而不是 ``.part``）。

    **这不是原子事务**：ZIP 与 ``.sha256`` 是两个文件，操作系统没有提供跨文件原子改名，
    两次 ``os.replace()`` 之间始终存在一个窗口。本函数用补偿式流程把该窗口的后果收敛为
    「要么都是新的，要么保持旧的」：

      ① 先把新校验文件写到 ``<zip>.sha256.part``（失败则完全没碰过正式文件）；
      ② 备份当前正式 ZIP 到 ``<zip>.bak``（若存在）；
      ③ ``os.replace()`` 替换正式 ZIP；
      ④ ``os.replace()`` 替换正式 ``.sha256``；
      ⑤ ③/④ 任一步失败 → 把正式 ZIP 回滚到备份（原本不存在旧 ZIP 则删除新 ZIP）、
         清理全部临时与备份文件后抛出。若**回滚本身也失败**，抛 :class:`ConsistencyError`
         显式报告「产物可能不一致」，而不是假装成功。
    """
    side = dest_zip + ".sha256"
    side_tmp = side + ".part"
    zip_bak = dest_zip + ".bak"
    digest = _sha256(tmp_zip)

    try:
        with open(side_tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f"{digest}  {os.path.basename(dest_zip)}\n")
    except OSError:
        _remove_if_exists(side_tmp)
        raise

    had_old_zip = os.path.exists(dest_zip)
    if had_old_zip:
        try:
            shutil.copy2(dest_zip, zip_bak)
        except OSError:
            _remove_if_exists(side_tmp)
            raise

    zip_replaced = False
    try:
        os.replace(tmp_zip, dest_zip)                  # ③ 替换正式 ZIP
        zip_replaced = True
        os.replace(side_tmp, side)                     # ④ 替换正式校验文件
    except OSError as exc:
        rollback_failed = None
        if zip_replaced:                               # ⑤ 补偿：退回旧 ZIP，避免“新 ZIP + 旧校验”
            try:
                if had_old_zip:
                    os.replace(zip_bak, dest_zip)
                else:
                    _remove_if_exists(dest_zip)        # 原本没有旧 ZIP：不留孤儿 ZIP
            except OSError as rb:
                rollback_failed = rb
        _remove_if_exists(tmp_zip)
        _remove_if_exists(side_tmp)
        _remove_if_exists(zip_bak)
        if rollback_failed is not None:
            raise ConsistencyError(
                "替换校验文件失败后回滚正式 ZIP 也失败，磁盘上的 ZIP 与 .sha256 可能"
                f"不一致（{dest_zip}）：{rollback_failed}") from exc
        raise
    _remove_if_exists(zip_bak)
    return digest, side


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="由同一次构建产物生成便携发行 ZIP 及其 SHA-256 校验文件")
    ap.add_argument("--dist", default=DIST, help="构建产物目录（默认 .build/dist）")
    ap.add_argument("--out", default="", help="ZIP 输出目录（默认与 --dist 相同）")
    ap.add_argument("--verify", nargs="?", const="", default=None, metavar="ZIP",
                    help="只校验已有 ZIP 与构建产物是否一致，不写任何文件")
    ap.add_argument("--require-vcs-sha", default="", metavar="SHA",
                    help="要求 BUILD_INFO.json 的 vcs.commit 等于该提交 SHA")
    args = ap.parse_args(argv)

    dist = os.path.abspath(args.dist)
    out_dir = os.path.abspath(args.out) if args.out else dist

    try:
        version = app_version()
        name = zip_basename(version)
        info = check_build_info(dist, version)
        files = dist_files(dist)
        n_sums = check_sums(dist, files)

        expected_sha = (args.require_vcs_sha
                        or os.environ.get("TDAC_BUILD_SOURCE_SHA", "")).strip().lower()
        if expected_sha and not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
            raise InputError(f"构建提交 SHA 格式不正确：{expected_sha!r}")
        commit = check_vcs(info, expected_sha)

        if args.verify is not None:
            target = args.verify or os.path.join(out_dir, name)
            stats = verify_zip(target, dist)
            print("校验对象：", target)
            print("结论：PASS（ZIP 内容与同次构建产物逐文件一致）")
            print(f"文件条目 {stats['files']} 个，目录条目 {stats['dirs']} 个；"
                  f"{SUMS_NAME} 覆盖 {n_sums} 项。")
            if expected_sha:
                print("构建提交：", commit, "（与本次 CI 提交一致）")
            return EXIT_OK

        dest_zip = os.path.join(out_dir, name)
        tmp_zip = build_zip(dist, out_dir, name, _dos_time(info), files, dist_dirs(dist))
        try:
            stats = verify_zip(tmp_zip, dist)        # ① 先对**临时** ZIP 完整回读自检
            digest, side = promote_zip(tmp_zip, dest_zip)   # ② 通过后才替换正式产物
        except BaseException:
            _remove_if_exists(tmp_zip)               # 校验失败不留半成品
            raise
        zip_path = dest_zip
        size = os.path.getsize(zip_path)
        print()
        print("便携发行 ZIP：", zip_path)
        print("版本：", version)
        print("ZIP 大小：%d 字节（%.1f MiB）" % (size, size / 1024 / 1024))
        print("ZIP SHA-256：", digest)
        print("校验文件：", side)
        print(f"ZIP 自检：PASS（{stats['files']} 个文件条目逐一对齐构建产物，"
              f"{stats['dirs']} 个目录条目）")
        print(f"{SUMS_NAME}：覆盖 {n_sums} 项，与磁盘一致")
        print("构建提交：", commit or "（本地构建，未标记提交 SHA）")
        print()
        print("说明：外层 ZIP 与其 .sha256 校验文件按设计**不进入** %s（避免自引用）。"
              % SUMS_NAME)
        return EXIT_OK
    except InputError as exc:
        print("[错误]", exc)
        return EXIT_USAGE
    except ConsistencyError as exc:
        print("[FAIL]", exc)
        return EXIT_MISMATCH
    except OSError as exc:
        print("[FAIL] 写出发行物时发生 I/O 错误（原有有效产物已尽力保留）：", exc)
        return EXIT_MISMATCH


if __name__ == "__main__":
    sys.exit(main())
