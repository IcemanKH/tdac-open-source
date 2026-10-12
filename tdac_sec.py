# -*- coding: utf-8 -*-
"""
tdac_sec.py — 敏感信息本地安全存储（Windows DPAPI，用户级加密）

设计要点：
  * 使用 Windows 自带 DPAPI（CryptProtectData / CryptUnprotectData），
    密钥由当前 Windows 用户 + 本机绑定，其它用户/其它机器无法解密。
  * 不引入任何第三方依赖（仅 ctypes + 标准库）。
  * 密文写入 secrets/secrets.dat（二进制），明文永不落盘、不进日志。
  * **解密失败绝不覆盖**：密文存在但无法解密（换账户 / 换机器 / 文件损坏）时
    直接抛错并保留原文件，绝不把旧资料当作空对象再写回。
  * **原子写入**：统一走 ``tdac_paths.atomic_write_bytes``（临时文件 → fsync →
    ``os.replace``），中断不会留下半截密文。
  * **ACL 加固结果可查**：加固失败不再静默吞掉，调用方可用
    :func:`acl_warning` 取出最近一次失败原因并向用户提示。

本文件不含任何敏感数据。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
from typing import Any, Dict, Optional, Tuple

import tdac_paths

__all__ = ["protect_bytes", "unprotect_bytes", "save_secrets", "load_secrets", "secrets_path", "group_secrets_path",
           "SECRETS_PATH", "GROUP_SECRETS_PATH", "SECRET_KEYS",
           "draft_secrets_path", "save_draft_secrets", "load_draft_secrets", "clear_draft_secrets",
           "load_draft_secrets_raw", "acl_warning", "harden_acl_path"]


def secrets_path() -> str:
    """正式密文路径（**动态解析**，跟随 ``TDAC_DATA_DIR``）。

    历史上这里是模块级常量，导入时即冻结：一旦数据目录被重定向（测试沙箱 /
    便携场景），写入仍会落到旧路径，属于“写入路径不受保护”的隐患。
    现改为函数 + 模块级 ``__getattr__`` 兼容别名。
    """
    return tdac_paths.secrets_path()


def group_secrets_path() -> str:
    """团体密文路径（动态解析，理由同 :func:`secrets_path`）。"""
    return tdac_paths.group_secrets_path()


def __getattr__(name: str) -> Any:  # PEP 562：兼容旧代码里的 SECRETS_PATH 常量用法
    if name == "SECRETS_PATH":
        return secrets_path()
    if name == "GROUP_SECRETS_PATH":
        return group_secrets_path()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# 草稿密文：与正式密文**分开存放**，这样“暂存草稿”不会覆盖已二次确认的正式敏感资料，
# 也保证草稿里的护照号 / 手机号同样只以 DPAPI 密文落盘，不产生明文副本。
DRAFT_SECRETS_NAME = "draft-secrets.dat"

# 允许存放在加密文件中的字段白名单
SECRET_KEYS = ("passportNo", "phoneNo")

# DPAPI 熵值，进一步降低被其它进程误用的可能
_ENTROPY = b"tdac-local-fill-v1"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_crypt32.CryptProtectData.argtypes = [
    ctypes.POINTER(_DATA_BLOB), wt.LPCWSTR, ctypes.POINTER(_DATA_BLOB),
    ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(_DATA_BLOB),
]
_crypt32.CryptProtectData.restype = wt.BOOL

_crypt32.CryptUnprotectData.argtypes = [
    ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(wt.LPWSTR), ctypes.POINTER(_DATA_BLOB),
    ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(_DATA_BLOB),
]
_crypt32.CryptUnprotectData.restype = wt.BOOL

_kernel32.LocalFree.argtypes = [wt.HLOCAL]
_kernel32.LocalFree.restype = wt.HLOCAL

CRYPTPROTECT_UI_FORBIDDEN = 0x1


def _blob_from(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _blob_bytes(blob: _DATA_BLOB) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def protect_bytes(plain: bytes) -> bytes:
    """用当前 Windows 用户凭据加密。"""
    in_blob = _blob_from(plain)
    ent = _blob_from(_ENTROPY)
    out_blob = _DATA_BLOB()
    ok = _crypt32.CryptProtectData(
        ctypes.byref(in_blob), "TDAC", ctypes.byref(ent),
        None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError(f"DPAPI 加密失败，错误码 {ctypes.get_last_error()}")
    try:
        return _blob_bytes(out_blob)
    finally:
        _kernel32.LocalFree(out_blob.pbData)


def unprotect_bytes(cipher: bytes) -> bytes:
    """解密（仅当前 Windows 用户可解）。"""
    in_blob = _blob_from(cipher)
    ent = _blob_from(_ENTROPY)
    out_blob = _DATA_BLOB()
    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, ctypes.byref(ent),
        None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError(
            f"DPAPI 解密失败，错误码 {ctypes.get_last_error()}。"
            "常见原因：换了 Windows 账户、换了机器、或 secrets.dat 被其它账户创建。"
        )
    try:
        return _blob_bytes(out_blob)
    finally:
        _kernel32.LocalFree(out_blob.pbData)


def save_secrets(data: Dict[str, Any]) -> str:
    """仅保存白名单字段，缺省字段保留原值。

    **解密失败即中止**：若已存在密文却无法解密（换账户 / 换机器 / 损坏），
    直接抛错并保留原文件，绝不把旧资料当作空对象再写回（否则会静默丢失
    另一个字段）。
    """
    cur: Dict[str, Any] = {}
    if os.path.exists(secrets_path()):
        # 不吞异常：旧密文读不出来时，宁可让本次保存失败，也不能覆盖旧密文。
        cur = load_secrets()
    for k in SECRET_KEYS:
        if k in data and str(data[k]).strip() != "":
            cur[k] = str(data[k]).strip()
    raw = json.dumps(cur, ensure_ascii=False).encode("utf-8")
    tdac_paths.atomic_write_bytes(secrets_path(), protect_bytes(raw))
    _harden_acl()
    return secrets_path()


def load_secrets() -> Dict[str, Any]:
    if not os.path.exists(secrets_path()):
        raise FileNotFoundError(
            f"未找到敏感信息文件：{secrets_path()}\n请先运行：.build\\venv\\Scripts\\python.exe set_secrets.py"
        )
    with open(secrets_path(), "rb") as f:
        return json.loads(unprotect_bytes(f.read()).decode("utf-8"))


# --------------------------------------------------------------------------- #
# 草稿密文（G1 验收新增：本地草稿暂存）
# 与正式密文完全同源：同一套 DPAPI、同一套字段白名单、同样收紧 ACL。
#
# G1 最终修复：草稿密文额外携带 ``generation``（代际标记），与 draft.json 中的
# ``generation`` 配对，用于检测“JSON 与密文来自不同版本”的错配（见 tdac_draft）。
# ``generation`` 不是敏感值（随机 hex），放在密文内部，不产生额外明文文件。
# --------------------------------------------------------------------------- #
def draft_secrets_path() -> str:
    """草稿密文的绝对路径（每次都按当前数据目录解析，便于测试重定向）。"""
    return os.path.join(tdac_paths.secrets_dir(), DRAFT_SECRETS_NAME)


def save_draft_secrets(data: Dict[str, Any], generation: str = "",
                       path: Optional[str] = None) -> str:
    """保存草稿中的敏感字段（仅白名单）。

    * 只写护照号 / 手机号，其它字段一律丢弃；
    * 两个字段都为空时**删除**草稿密文，避免残留旧密文；
    * ``path`` 可指定写入位置（事务机制用于先写暂存文件）；
    * 返回写入的路径；未写入时返回空串。
    """
    clean = {k: str(data.get(k, "")).strip() for k in SECRET_KEYS
             if str(data.get(k, "") or "").strip()}
    if not clean:
        clear_draft_secrets(path=path)
        return ""
    target = path or draft_secrets_path()
    payload: Dict[str, Any] = dict(clean)
    if generation:
        payload["generation"] = str(generation)
    tdac_paths.atomic_write_bytes(
        target, protect_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8")))
    harden_acl_path(target)
    return target


def load_draft_secrets_raw(path: Optional[str] = None) -> Dict[str, Any]:
    """读取草稿密文的完整内容（含 ``generation``）；不存在时返回空字典。

    解密失败会向上抛出（由调用方决定如何提示），绝不静默返回空字典。
    """
    target = path or draft_secrets_path()
    if not os.path.exists(target):
        return {}
    with open(target, "rb") as f:
        data = json.loads(unprotect_bytes(f.read()).decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("草稿密文内容不是一个对象")
    return data


def load_draft_secrets(path: Optional[str] = None) -> Dict[str, Any]:
    """读取草稿密文中的敏感字段；不存在时返回空字典（草稿允许只有非敏感字段）。"""
    data = load_draft_secrets_raw(path=path)
    return {k: str(data[k]) for k in SECRET_KEYS if k in data}


def clear_draft_secrets(path: Optional[str] = None) -> bool:
    """删除草稿密文；返回是否真的删除过（不存在时返回 False，不报错）。"""
    target = path or draft_secrets_path()
    if not os.path.exists(target):
        return False
    os.remove(target)
    return True


def validate_group_secret_records(records: Dict[str, Dict[str, Any]]) -> None:
    """拒绝把任何非护照/手机号字段写进团体密文。"""
    if not isinstance(records, dict):
        raise ValueError("团体敏感资料必须是按旅客 ID 组织的对象")
    for traveler_id, values in records.items():
        if not str(traveler_id).strip() or not isinstance(values, dict):
            raise ValueError("团体敏感资料包含无效旅客 ID")
        extra = set(values) - set(SECRET_KEYS)
        if extra:
            raise ValueError("团体敏感资料只允许 passportNo、phoneNo")


def save_group_secrets(records: Dict[str, Dict[str, Any]]) -> str:
    validate_group_secret_records(records)
    clean = {str(k): {field: str(v.get(field, "")).strip() for field in SECRET_KEYS}
             for k, v in records.items()}
    tdac_paths.atomic_write_bytes(
        group_secrets_path(), protect_bytes(json.dumps(clean, ensure_ascii=False).encode("utf-8")))
    harden_acl_path(group_secrets_path())
    return group_secrets_path()


def load_group_secrets() -> Dict[str, Dict[str, str]]:
    if not os.path.exists(group_secrets_path()):
        raise FileNotFoundError(f"未找到团体敏感资料：{group_secrets_path()}")
    with open(group_secrets_path(), "rb") as f:
        data = json.loads(unprotect_bytes(f.read()).decode("utf-8"))
    validate_group_secret_records(data)
    return {str(k): {field: str(v.get(field, "")) for field in SECRET_KEYS} for k, v in data.items()}


def _harden_acl() -> None:
    """尽力收紧正式密文的 ACL（失败只记录，不阻断保存）。"""
    harden_acl_path(secrets_path())


# 最近一次 ACL 加固的结果：(是否成功, 说明)。失败时说明为可读的中文原因。
_LAST_ACL: Tuple[bool, str] = (True, "")


def acl_warning() -> str:
    """返回最近一次 ACL 加固失败的说明；成功时返回空串。

    供 GUI / CLI 在保存后提示用户“文件权限未能收紧”，避免静默忽略
    （非管理员环境或文件被占用时可能失败）。
    """
    ok, detail = _LAST_ACL
    return "" if ok else detail


def harden_acl_path(path: str) -> bool:
    """收紧文件 ACL：仅当前用户可读写。返回是否成功。

    失败**不抛异常**（保存本身已经成功，权限收紧是加固项），但会记录到
    ``_LAST_ACL`` 并由 :func:`acl_warning` 暴露给调用方提示用户。
    """
    global _LAST_ACL
    user = os.environ.get("USERNAME")
    if not user:
        _LAST_ACL = (False, "未能确定当前 Windows 用户名（环境变量 USERNAME 为空），ACL 未收紧。")
        return False
    try:
        proc = subprocess.run(
            ["icacls", path, "/inheritance:r", "/grant:r", f"{user}:(R,W)"],
            capture_output=True, timeout=10, check=False,
        )
    except Exception as e:
        _LAST_ACL = (False, f"调用 icacls 收紧 ACL 失败：{type(e).__name__}: {e}。"
                            f"该文件仍仅受 Windows 用户账户保护，但目录权限未额外收紧。")
        return False
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip().splitlines()
        reason = tail[-1] if tail else f"返回码 {proc.returncode}"
        _LAST_ACL = (False, f"icacls 收紧 ACL 未成功（{reason}）；"
                            f"文件仍受 Windows 账户保护，但未额外限制为仅当前用户。")
        return False
    _LAST_ACL = (True, "")
    return True


def mask(value: str, keep_head: int = 2, keep_tail: int = 2) -> str:
    """打码显示，用于日志与回读核对输出。"""
    s = "" if value is None else str(value)
    if len(s) <= keep_head + keep_tail:
        return "*" * len(s)
    return f"{s[:keep_head]}{'*' * (len(s) - keep_head - keep_tail)}{s[-keep_tail:]}"


if __name__ == "__main__":
    # 自检：加密 -> 解密 -> 比对
    probe = b"selftest-" + os.urandom(8).hex().encode()
    assert unprotect_bytes(protect_bytes(probe)) == probe
    print("DPAPI 自检 PASS")
    print("secrets.dat 路径：", secrets_path())
    if os.path.exists(secrets_path()):
        d = load_secrets()
        print("已存在字段：", ", ".join(f"{k}={mask(v)}" for k, v in d.items()) or "(空)")
    else:
        print("尚未创建 secrets.dat")
