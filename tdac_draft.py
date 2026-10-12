# -*- coding: utf-8 -*-
"""tdac_draft.py — 本地草稿暂存与继续填写（G1 验收新增）。

目标
  允许普通用户**分多次**准备入境信息：资料还没填完也能先暂存，下次打开程序
  自动恢复草稿并继续编辑；等资料确认完整后再启动官网预填。

设计要点（与首批 A1–A9 / 后续 B1–B7 的既有约定保持一致）
  * **只保留一份草稿**：本机当前用户唯一的草稿。不做云同步、不做多草稿、
    不做账号体系、不引入任何数据库——草稿就是两个文件。
  * **敏感字段仍然走 DPAPI**：护照号 / 手机号写入 ``secrets\\draft-secrets.dat``
    （复用 ``tdac_sec`` 的 protect / unprotect 与 ACL 收紧逻辑），
    ``config\\draft.json`` 里**绝不出现**这两个字段的明文，只记录“是否已暂存”。
  * **草稿与正式资料分离**：暂存草稿**不会**覆盖 ``profile.json`` /
    ``trip.json`` / ``dynamic.json`` / ``secrets.dat``；反之，正式保存也不会
    改动草稿。草稿只是“未完成的编辑现场”。
  * **以“最近一次明确保存”为准**：重启时若发现正式配置比草稿更新（用户后来
    又重新保存过正式资料），则不自动恢复草稿（``draft_is_stale``），只提示、
    不删除；反之恢复草稿。
  * **损坏不静默**：草稿 JSON 损坏（``draft_status`` 返回 ``corrupt``）或
    密文缺失 / 损坏（``load_draft`` 抛 ``DraftSecretsUnavailable``）时，
    一律明确报错并保留原文件，绝不用不完整内容覆盖用户资料。
  * **草稿保存不做任何校验**：缺必填项、格式不对、日期为空都允许暂存。
    真正的完整性 / 格式 / 二次确认 / 日期窗口校验只在“开始官网预填”时执行
    （见 ``tdac_validate.prefill_blockers``）。
  * **草稿里的健康声明一律视为“未确认”**：写入时强制
    ``last14DaysConfirmed = healthDeclarationConfirmed = False``，
    因此正式填写前必须由本人重新确认，不存在“草稿把健康答案带过去”的路径。
  * **草稿文件位于受管数据目录内**（``config`` / ``secrets``），因此
    ``--delete-data`` / 界面“删除受管用户资料”会连同草稿一起清理。
  * **两文件一致提交（G1 最终修复）**：``draft.json`` 与
    ``secrets\\draft-secrets.dat`` 是一个整体，必须同版本。写入采用
    “暂存文件 → 事务标记 → 旧版转 prev → 安装新版 → 清理”的最小可恢复
    事务，并在两份文件里写入同一个 ``generation`` 代际标记。任何时刻
    中断 / 替换失败 / 进程崩溃，都不会把不同版本的资料组合在一起：
    ``recover_draft_transaction`` 在下次启动时回滚到一致状态，而
    ``load_draft`` 还会做二次代际核对（不一致直接报错，绝不拼接）。
  * **解密失败即停止保存（G1 最终修复）**：草稿密文存在但无法解密时，
    ``save_draft`` 直接抛错，既不覆盖也不删除旧密文，避免把旧资料当作
    空对象写回。
  * **时间戳异常可恢复（G1 最终修复）**：``savedAt`` / ``savedAtEpoch``
    缺失或明显异常（早于 2000 年、晚于当前时间一天以上）时，
    ``draft_time_status`` 不再把草稿误判为“过期”，而是按“未知时间”处理：
    **优先恢复草稿**（避免丢用户的填写成果）并明确提示，由本人核对。

本文件不含任何敏感数据。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import tdac_paths
import tdac_sec

__all__ = [
    "SCHEMA_VERSION", "DRAFT_FILE_NAME", "draft_path", "draft_exists", "read_draft_file",
    "save_draft", "load_draft", "describe_draft", "delete_draft",
    "DraftError", "DraftSecretsUnavailable", "DraftGenerationMismatch",
    "STATUS_NONE", "STATUS_OK", "STATUS_CORRUPT", "draft_status",
    "official_config_mtime", "draft_saved_epoch", "draft_is_stale",
    "draft_time_status", "TIME_OK", "TIME_UNKNOWN", "TIME_ABNORMAL",
    "recover_draft_transaction", "RECOVER_NONE", "RECOVER_FINALIZED", "RECOVER_ROLLED_BACK",
    "commit_marker_path", "staged_json_path", "staged_secrets_path",
    "prev_json_path", "prev_secrets_path",
]

SCHEMA_VERSION = 1
DRAFT_FILE_NAME = "draft.json"

# 正式（已确认）配置文件的文件名：用于判断“草稿 vs 正式资料”谁更新。
OFFICIAL_CONFIG_FILES = ("profile.json", "trip.json", "dynamic.json")

# 草稿 savedAt 只有秒级精度，正式文件 mtime 有亚秒精度；给一点容差，
# 避免“先保存正式资料、紧接着暂存草稿”被误判为旧草稿。
STALE_TOLERANCE_SECONDS = 2.0

STATUS_NONE = "none"
STATUS_OK = "ok"
STATUS_CORRUPT = "corrupt"

# 草稿时间戳状态
TIME_OK = "ok"                 # 时间戳可信，可用于“谁更新”的比较
TIME_UNKNOWN = "unknown"       # 没有时间戳信息（回落到文件 mtime）
TIME_ABNORMAL = "abnormal"     # 时间戳明显异常（早于 2000 年 / 明显在未来）

# 时间戳合理下界：2000-01-01 00:00:00 UTC。早于此一律视为异常。
MIN_SANE_EPOCH = 946684800.0

# 事务恢复结果
RECOVER_NONE = "none"                  # 没有进行中的事务
RECOVER_FINALIZED = "finalized"        # 提交其实已完成，补做收尾
RECOVER_ROLLED_BACK = "rolled_back"    # 提交中途中断，已回滚到上一版

# 事务用的临时文件名（一律以 “.” 开头，在 config 目录里不与 draft.json 混同）
_STAGE_JSON = ".draft.stage.json"
_PREV_JSON = ".draft.prev.json"
_TXN_MARKER = ".draft.txn"
_STAGE_SECRETS = ".draft.stage.dat"
_PREV_SECRETS = ".draft.prev.dat"


class DraftError(Exception):
    """草稿相关错误的基类（GUI 据此给出明确提示，绝不静默降级）。"""


class DraftSecretsUnavailable(DraftError):
    """草稿标记“已暂存护照号 / 手机号”，但密文缺失、损坏或账户不匹配。"""


class DraftGenerationMismatch(DraftError):
    """``draft.json`` 与草稿密文来自不同版本（代际标记不一致）。

    这说明存在被中断的写入或外部手工改动；为避免把不同版本的资料组合在
    一起，调用方必须放弃本次读取而不是拼接。
    """

# 健康声明：答案可以随草稿暂存，但“已确认”标记不允许随草稿恢复。
HEALTH_ANSWER_KEYS = ("last14DaysCountries", "yellowFeverVaccination", "symptoms")
CONFIRMED_FLAGS = ("last14DaysConfirmed", "healthDeclarationConfirmed")

# 这些字段属于“正式资料”语义，不写入草稿：
#   * 护照号 / 手机号 → 只进 DPAPI 密文（白名单以 tdac_sec.SECRET_KEYS 为准）；
#   * 二次确认标记 → 由正式保存流程写入 profile.json，草稿不携带。
NON_DRAFT_PROFILE_KEYS = tuple(tdac_sec.SECRET_KEYS) + ("criticalConfirmedAt", "criticalFingerprint")


def draft_path() -> str:
    """草稿的 JSON 路径（按当前数据目录动态解析，便于测试重定向）。"""
    return os.path.join(tdac_paths.config_dir(), DRAFT_FILE_NAME)


# --- 事务用辅助路径（均位于受管数据目录内，且以 “.” 开头） ----------------- #
def _config_file(name: str) -> str:
    return os.path.join(tdac_paths.config_dir(), name)


def _secrets_file(name: str) -> str:
    return os.path.join(tdac_paths.secrets_dir(), name)


def _in_managed(path: str) -> bool:
    """路径（真实路径）是否位于受管数据根目录之内。"""
    root = tdac_paths.realpath(tdac_paths.managed_root())
    real = tdac_paths.realpath(path)
    return real == root or real.startswith(root + os.sep)


def commit_marker_path() -> str:
    """事务标记文件路径：存在即表示“有一次未完成的提交”。"""
    return _config_file(_TXN_MARKER)


def staged_json_path() -> str:
    return _config_file(_STAGE_JSON)


def prev_json_path() -> str:
    return _config_file(_PREV_JSON)


def staged_secrets_path() -> str:
    return _secrets_file(_STAGE_SECRETS)


def prev_secrets_path() -> str:
    return _secrets_file(_PREV_SECRETS)


def draft_exists() -> bool:
    """本机是否存在草稿（草稿文件本身即可作为判据）。"""
    return os.path.exists(draft_path())


def draft_status() -> str:
    """草稿文件状态：``none`` / ``ok`` / ``corrupt``。

    ``read_draft_file`` 为了兼容“没有草稿”而把损坏文件也当作空字典，
    因此**不能**用它区分“无草稿”和“草稿损坏”。本函数专门用于在界面上
    明确区分这两种情况，避免损坏被静默忽略。
    """
    path = draft_path()
    if not os.path.exists(path):
        return STATUS_NONE
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return STATUS_CORRUPT
    return STATUS_OK if isinstance(data, dict) else STATUS_CORRUPT


def official_config_mtime() -> float:
    """正式配置（profile/trip/dynamic）中最新的修改时间；都不存在时返回 0。"""
    latest = 0.0
    for name in OFFICIAL_CONFIG_FILES:
        try:
            latest = max(latest, os.path.getmtime(os.path.join(tdac_paths.config_dir(), name)))
        except OSError:
            continue
    return latest


def draft_saved_epoch() -> float:
    """草稿的暂存时刻（epoch 秒）；无法判断时返回 0。

    优先取 ``savedAtEpoch``（精确到亚秒）；旧草稿回退到解析 ``savedAt``；
    再不行回退到草稿文件自身的 mtime。
    """
    value = _declared_epoch()
    if value is not None:
        return value
    try:
        return os.path.getmtime(draft_path())
    except OSError:
        return 0.0


def _declared_epoch() -> Optional[float]:
    """草稿**自述**的暂存时刻；没有或无法解析时返回 None。"""
    info = read_draft_file()
    value = info.get("savedAtEpoch")
    if isinstance(value, (int, float)):
        return float(value)
    stamp = str(info.get("savedAt", "") or "")
    if not stamp:
        return None
    try:
        return dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        return None


def draft_time_status(now: Optional[float] = None) -> str:
    """草稿时间戳是否可信：``ok`` / ``unknown`` / ``abnormal``。

    * ``ok``：有可解析的时间戳，且在合理范围内（≥ 2000-01-01，且不晚于
      当前时间一天以上）——可以据此判断“草稿 vs 正式资料”谁更新；
    * ``unknown``：完全没有时间戳信息（旧草稿或字段缺失），只能回退到
      文件 mtime；
    * ``abnormal``：时间戳明显异常（早于 2000 年、未来时间、或格式坏掉）。
      此时**不做“过期”判定**，按“优先恢复草稿 + 提示本人核对”处理。
    """
    now = time.time() if now is None else now
    info = read_draft_file()
    if not info:
        return TIME_UNKNOWN
    value = info.get("savedAtEpoch")
    candidate: Optional[float] = None
    if isinstance(value, (int, float)):
        candidate = float(value)
    else:
        stamp = str(info.get("savedAt", "") or "")
        if not stamp:
            return TIME_UNKNOWN
        try:
            candidate = dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").timestamp()
        except Exception:
            return TIME_ABNORMAL
    if candidate < MIN_SANE_EPOCH or candidate > now + 86400:
        return TIME_ABNORMAL
    return TIME_OK


def draft_is_stale(tolerance: float = STALE_TOLERANCE_SECONDS) -> bool:
    """草稿是否**比正式配置更旧**。

    草稿与正式资料是两套独立文件：如果用户在暂存草稿之后又重新保存了正式
    资料，那么草稿就是“过期现场”，不能再覆盖更新的正式资料。返回 True
    表示“重启时应保留正式资料、不自动恢复草稿”。

    **时间戳异常时不判过期**：``draft_time_status`` 不是 ``ok`` 时一律返回
    False——宁可多恢复一次草稿（用户可自行核对/放弃），也不因为一个坏时间戳
    就静默丢弃用户尚未确认的填写成果。
    """
    if not draft_exists():
        return False
    if draft_status() != STATUS_OK:
        return False
    if draft_time_status() != TIME_OK:
        return False
    return official_config_mtime() > draft_saved_epoch() + float(tolerance)


def _clean_profile(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """去掉不谈草稿携带的字段（尤其是敏感明文，绝不能落到 draft.json）。"""
    out: Dict[str, Any] = {}
    for key, value in (profile or {}).items():
        if str(key) in NON_DRAFT_PROFILE_KEYS:
            continue
        if str(key).startswith("_"):
            continue
        out[str(key)] = value
    return out


def _clean_dynamic(dynamic: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """草稿中的健康声明一律标记为未确认（正式填写前须本人重新确认）。"""
    out: Dict[str, Any] = {}
    for key, value in (dynamic or {}).items():
        if str(key) in CONFIRMED_FLAGS:
            continue
        if str(key).startswith("_"):
            continue
        out[str(key)] = value
    for flag in CONFIRMED_FLAGS:
        out[flag] = False
    return out


def _new_generation() -> str:
    """生成一次性代际标记（随机 hex，非敏感值）。"""
    return os.urandom(8).hex()


def _remove_file(path: str) -> bool:
    """删除文件；不存在时返回 False（不报错）。只允许受管目录内。"""
    if not os.path.exists(path):
        return False
    if not _in_managed(path):
        raise PermissionError(f"拒绝删除受管数据目录之外的文件：{path}")
    os.remove(path)
    return True


def _move_if_exists(src: str, dst: str) -> bool:
    """把 src 移到 dst（同卷 os.replace）；src 不存在时返回 False。"""
    if not os.path.exists(src):
        return False
    tdac_paths.assert_managed_write(dst)
    os.replace(src, dst)
    return True


def _read_marker() -> Dict[str, Any]:
    path = commit_marker_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        # 标记损坏：当作“有未完成事务”处理（最保守：触发回滚）。
        return {"generation": "", "hadJson": True, "hadSecrets": True, "corrupt": True}
    return data if isinstance(data, dict) else {"generation": "", "hadJson": True, "hadSecrets": True}


def _peek_json_generation() -> Optional[str]:
    info = read_draft_file()
    value = info.get("generation")
    return str(value) if value else None


def _peek_cipher_generation() -> Optional[str]:
    try:
        raw = tdac_sec.load_draft_secrets_raw()
    except Exception:
        return None
    value = raw.get("generation")
    return str(value) if value else None


def _finish_commit() -> None:
    """提交收尾：先清 prev（新版已就位），再删事务标记（顺序不可颠倒）。"""
    _remove_file(prev_json_path())
    _remove_file(prev_secrets_path())
    _remove_file(commit_marker_path())


def recover_draft_transaction() -> str:
    """修复上一次可能被中断的草稿提交，返回 ``none`` / ``finalized`` / ``rolled_back``。

    判定逻辑：

    * 没有事务标记 → 顺手清理任何遗留暂存文件，返回 ``none``；
    * 有标记且两份文件都已是本次代际 → 提交其实已完成，补做收尾（``finalized``）；
    * 否则 → 按标记里记录的“提交前是否存在旧版”回滚到上一版，返回
      ``rolled_back``。回滚后的两份文件必然同属一个版本。
    """
    marker = _read_marker()
    if not marker:
        _remove_file(staged_json_path())
        _remove_file(staged_secrets_path())
        _remove_file(prev_json_path())
        _remove_file(prev_secrets_path())
        return RECOVER_NONE

    gen = str(marker.get("generation", "") or "")
    if gen and _peek_json_generation() == gen and _peek_cipher_generation() == gen:
        _finish_commit()
        return RECOVER_FINALIZED

    had_json = bool(marker.get("hadJson", True))
    had_secrets = bool(marker.get("hadSecrets", True))
    _restore_prev(prev_json_path(), draft_path(), had_json)
    _restore_prev(prev_secrets_path(), tdac_sec.draft_secrets_path(), had_secrets)
    _remove_file(staged_json_path())
    _remove_file(staged_secrets_path())
    _remove_file(prev_json_path())
    _remove_file(prev_secrets_path())
    _remove_file(commit_marker_path())
    return RECOVER_ROLLED_BACK


def _restore_prev(prev: str, target: str, had: bool) -> None:
    """回滚单个文件：提交前存在旧版则恢复旧版，否则删除新装的半成品。"""
    if had:
        if os.path.exists(prev):
            tdac_paths.assert_managed_write(target)
            os.replace(prev, target)
        # had=True 但 prev 缺失（异常情况）：不动 target，交由代际核对兜底。
    else:
        _remove_file(target)


def _assert_existing_draft_cipher_readable() -> None:
    """P1-02：旧草稿密文存在但读不出来 → 直接抛错，停止本次保存。

    这样既不会用新内容覆盖掉用户可能在他处能解开的旧密文，也不会把旧
    资料当作空对象写回。
    """
    path = tdac_sec.draft_secrets_path()
    if not os.path.exists(path):
        return
    try:
        tdac_sec.load_draft_secrets_raw()
    except Exception as e:
        raise DraftSecretsUnavailable(
            "已有草稿密文无法解密，已停止本次暂存以免覆盖旧资料（旧密文保持原样）。\n"
            "常见原因：更换了 Windows 账户或电脑、密文损坏。\n"
            f"（{type(e).__name__}: {e}）") from e


def save_draft(profile: Optional[Dict[str, Any]] = None,
               trip: Optional[Dict[str, Any]] = None,
               dynamic: Optional[Dict[str, Any]] = None,
               secrets: Optional[Dict[str, Any]] = None,
               now: Optional[str] = None,
               now_epoch: Optional[float] = None) -> str:
    """把当前编辑现场暂存为**唯一**草稿（覆盖上一份）。

    * 不做任何必填 / 格式 / 日期校验：资料不完整也允许暂存；
    * 返回草稿 JSON 的路径。

    敏感字段只在 ``secrets\\draft-secrets.dat`` 中以 DPAPI 密文保存；
    ``draft.json`` 只记录 ``secretsPresent``（布尔存在性），不含任何明文。

    写入顺序（最小可恢复事务，绝不只是两个独立的 ``os.replace``）：

    0. 先修复上次被中断的事务（保证起始状态一致）；
    1. 旧草稿密文若无法解密 → 立即停止，不覆盖、不删除；
    2. 两份新内容都先写成同目录**暂存**文件（``.draft.stage.*``）；
    3. 写**事务标记**（记录本次代际 + 提交前两份文件是否存在）；
    4. 把旧版转存为 ``.draft.prev.*``，再安装新版；
    5. 收尾：清理 prev 与事务标记。

    任何一步中断，下次启动时 ``recover_draft_transaction`` 都能把两份文件
    恢复到同一个版本。
    """
    recover_draft_transaction()

    clean_secrets = {k: str((secrets or {}).get(k, "") or "").strip() for k in tdac_sec.SECRET_KEYS}
    _assert_existing_draft_cipher_readable()

    stamp = now or dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if now_epoch is None:
        now_epoch = time.time()
        if now:
            try:
                now_epoch = dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").timestamp()
            except Exception:
                pass

    generation = _new_generation()
    has_secrets = any(clean_secrets.get(k) for k in tdac_sec.SECRET_KEYS)
    payload: Dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "generation": generation,
        "savedAt": stamp,
        "savedAtEpoch": float(now_epoch),
        "profile": _clean_profile(profile),
        "trip": {str(k): v for k, v in (trip or {}).items() if not str(k).startswith("_")},
        "dynamic": _clean_dynamic(dynamic),
        "secretsPresent": {k: bool(clean_secrets.get(k)) for k in tdac_sec.SECRET_KEYS},
        "secretFile": tdac_sec.DRAFT_SECRETS_NAME if has_secrets else "",
        "healthConfirmed": False,   # 草稿中的健康声明为“未确认”
    }

    # 1) 暂存两份新内容（同目录，保证后续 os.replace 同卷原子）。
    if has_secrets:
        tdac_sec.save_draft_secrets(clean_secrets, generation=generation,
                                    path=staged_secrets_path())
    else:
        _remove_file(staged_secrets_path())
    tdac_paths.atomic_write_json(staged_json_path(), payload)

    # 2) 事务标记：此后任何中断都可被检测并回滚。
    had_json = os.path.exists(draft_path())
    had_secrets_file = os.path.exists(tdac_sec.draft_secrets_path())
    tdac_paths.atomic_write_json(commit_marker_path(), {
        "generation": generation,
        "hadJson": had_json,
        "hadSecrets": had_secrets_file,
        "savedAt": stamp,
    })

    # 3) 旧版转 prev，然后安装新版。
    if had_json:
        _move_if_exists(draft_path(), prev_json_path())
    if had_secrets_file:
        _move_if_exists(tdac_sec.draft_secrets_path(), prev_secrets_path())
    if has_secrets:
        tdac_paths.assert_managed_write(tdac_sec.draft_secrets_path())
        os.replace(staged_secrets_path(), tdac_sec.draft_secrets_path())
    os.replace(staged_json_path(), draft_path())

    # 4) 收尾。
    _finish_commit()
    return draft_path()


def read_draft_file() -> Dict[str, Any]:
    """只读草稿 JSON（不触碰密文）；不存在或损坏时返回空字典。"""
    path = draft_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_draft() -> Dict[str, Any]:
    """读取草稿（含解密后的敏感字段）。

    返回 ``{"savedAt", "profile", "trip", "dynamic", "secrets", "secretsPresent", ...}``；
    不存在草稿时返回空字典。密文损坏会向上抛出（由调用方决定如何提示），
    绝不静默返回“看起来正常”的空资料。

    额外一致性检查（两层）：

    1. **代际一致**：``draft.json`` 的 ``generation`` 与草稿密文内部的
       ``generation`` 必须一致；不一致说明两份文件来自不同版本，抛
       :class:`DraftGenerationMismatch`，绝不把两个版本拼接起来；
    2. **存在性一致**：若 ``secretsPresent`` 标记某个敏感字段“已暂存”，但解密后
       取不到该字段（密文被删除 / 损坏 / 属于其它 Windows 账户），抛出
       :class:`DraftSecretsUnavailable`，而不是静默把护照号 / 手机号变成空值。
    """
    raw = read_draft_file()
    if not raw:
        return {}
    present = raw.get("secretsPresent") or {}
    json_gen = str(raw.get("generation", "") or "")
    if not json_gen:
        raise DraftGenerationMismatch(
            "草稿缺少代际标记，无法确认 JSON 与加密资料是否来自同一次保存。"
            "为避免覆盖或拼接不同版本的资料，本次不自动恢复草稿；草稿文件保持不动。")
    cipher = {}
    if raw.get("secretFile"):
        cipher = tdac_sec.load_draft_secrets_raw()   # 解密失败直接抛出
        cipher_gen = str(cipher.get("generation", "") or "")
        if not cipher_gen or json_gen != cipher_gen:
            raise DraftGenerationMismatch(
                "草稿的 JSON 与加密资料不是同一次保存的版本（代际标记不一致），"
                "可能上次保存被中断或被外部改动。为避免把不同版本的资料组合在一起，"
                "本次不恢复草稿；两份文件保持原样，可在界面选择「放弃草稿」后重新暂存。")
    loaded = {k: str(cipher.get(k, "") or "") for k in tdac_sec.SECRET_KEYS}
    missing = [label for key, label in (("passportNo", "护照号"), ("phoneNo", "手机号"))
               if present.get(key) and not loaded.get(key).strip()]
    if missing:
        raise DraftSecretsUnavailable(
            "草稿标记已暂存：" + "、".join(missing) +
            "，但草稿密文缺失或无法解密（文件可能被删除、损坏，或属于其它 Windows 账户）。"
            "为避免用不完整内容覆盖你的资料，本次不自动恢复草稿；草稿文件保持不动。")
    out = dict(raw)
    out["secrets"] = {k: loaded[k] for k in tdac_sec.SECRET_KEYS if present.get(k)}
    return out


def describe_draft(draft: Optional[Dict[str, Any]] = None, action: str = "已暂存") -> str:
    """一句话描述草稿状态，用于界面与日志（不含任何敏感值）。"""
    info = read_draft_file() if draft is None else draft
    if not info:
        return "草稿：无"
    saved_at = str(info.get("savedAt", "") or "时间未知")
    present = info.get("secretsPresent") or {}
    marks = [name for key, name in (("passportNo", "护照号"), ("phoneNo", "手机号"))
             if present.get(key)]
    detail = ("；敏感字段已加密暂存：" + "、".join(marks)) if marks else "；敏感字段尚未填写"
    return f"草稿：{action}（{saved_at}）{detail}"


def delete_draft() -> List[str]:
    """删除本机草稿（JSON + 密文 + 任何事务遗留文件）。

    返回被删除的**草稿主文件**路径列表（``draft.json`` 与
    ``draft-secrets.dat``）；事务遗留的暂存 / 回滚文件会一并清理，
    但不计入返回值。

    * 只删除本模块管理、且真实位于受管数据目录内的文件；
    * 文件不存在时不算错误，直接跳过。
    """
    removed: List[str] = []
    for path in (draft_path(), tdac_sec.draft_secrets_path()):
        if _remove_file(path):
            removed.append(path)
    # 事务遗留（暂存 / prev / 标记）：一律清掉，避免污染下一次保存。
    for leftover in (staged_json_path(), staged_secrets_path(),
                     prev_json_path(), prev_secrets_path(), commit_marker_path()):
        _remove_file(leftover)
    return removed
