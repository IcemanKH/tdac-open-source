# -*- coding: utf-8 -*-
"""tdac_gui.py — 简体中文图形界面（B1）。

面向普通中国游客的 Windows 11 64 位单人版界面。设计原则：

  * **复用**已有逻辑：``tdac_paths``（数据/程序分离）、``tdac_sec``（DPAPI 加密）、
    ``tdac_privacy``（关键字段二次确认）、``tdac_validate``（格式校验）、
    ``tdac_fill``（自动填写主流程）、``tdac_browser`` / ``tdac_update`` /
    ``tdac_diag`` / ``tdac_draft``。**不重写一套独立业务流程**，不引入新框架
    （只用标准库 tkinter）。
  * **敏感字段默认遮挡**：护照号 / 手机号 / 收件邮箱在界面上以 ``*`` 显示，
    只有用户点击该行的「显示」按钮才会揭示完整内容。
  * **明确区分**「预填完成」与「正式申报成功」：本程序永远不会提交，
    因此界面上两者是两套完全独立的提示，不会混淆。
  * 运行中的「暂停 / 继续 / 停止并转人工」通过 ``tdac_fill.RunControl`` 的
    安全检查点协作实现；停止后沿用既有的「保留浏览器页面供人工接管」路径。
  * **草稿暂存（G1 验收新增）**：允许资料不完整时先「暂存草稿」，下次打开
    自动恢复并继续编辑。草稿与正式资料分离，敏感字段仍走 DPAPI；
    「暂存草稿」不做校验，「开始官网预填」才做完整校验（二者严格分离）。

界面上始终同时可见三种彼此独立的状态：
  ``草稿``（本地未完成的编辑现场）、``预填``（表单已自动填好、停在预览页）、
  ``正式申报``（只能由本人提交并取得回执后自行记录）。

本文件不含任何敏感数据；界面演示一律使用合成资料。
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any, Dict, List, Optional, Tuple

import tdac_browser
import tdac_diag
import tdac_draft
import tdac_fill
import tdac_gate
import tdac_paths
import tdac_privacy
import tdac_sec
import tdac_update
import tdac_validate

APP_TITLE = "泰国电子入境卡（TDAC）填写助手"
FONT = ("Microsoft YaHei UI", 10)
FONT_BOLD = ("Microsoft YaHei UI", 10, "bold")
FONT_TITLE = ("Microsoft YaHei UI", 14, "bold")
FONT_SMALL = ("Microsoft YaHei UI", 9)

# --------------------------------------------------------------------------- #
# 表单字段定义
# --------------------------------------------------------------------------- #
PROFILE_FIELDS: List[Tuple] = [
    ("familyName", "英文姓（与护照一致）", "text"),
    ("firstName", "英文名（与护照一致）", "text"),
    ("middleName", "中间名（没有则留空）", "text"),
    ("nationality", "国籍", "combo", ["CHINESE"]),
    ("dateOfBirth", "出生日期 (YYYY/MM/DD)", "text"),
    ("gender", "性别", "combo", ["MALE", "FEMALE"]),
    ("occupation", "职业", "combo",
     ["EMPLOYEE", "STUDENT", "BUSINESS", "GOVERNMENT OFFICER", "RETIRED", "OTHER"]),
    ("countryOfResidence", "居住国家/地区", "combo", ["CHINA"]),
    ("cityOfResidence", "居住城市", "text"),
    ("phoneCode", "电话区号（纯数字）", "text", ["86"]),
    ("visaNo", "签证号（免签留空）", "text"),
    ("email", "收件邮箱（接收申报回执）", "secret"),
]
# 关键敏感字段：单独用 DPAPI 加密保存，不写进 profile.json
SECRET_FIELDS = [("passportNo", "护照号码"), ("phoneNo", "手机号码（不含区号）")]

TRIP_FIELDS: List[Tuple] = [
    ("arrivalDate", "入境日期 (YYYY/MM/DD)", "text"),
    ("departureDate", "离境日期 (YYYY/MM/DD)", "text"),
    ("countryOfBoarding", "出发/登机国家地区", "combo", ["CHINA"]),
    ("purposeOfTravel", "旅行目的", "combo", ["HOLIDAY", "BUSINESS", "TRANSIT", "OTHER"]),
    ("arrivalMode", "入境交通方式", "combo", ["AIR", "LAND", "SEA"]),
    ("arrivalFlightType", "入境交通类型", "combo",
     ["COMMERCIAL FLIGHT", "PRIVATE FLIGHT", "CHARTER FLIGHT", "OTHER"]),
    ("arrivalFlightNo", "入境航班/车船号", "text"),
    ("departureMode", "离境交通方式", "combo", ["AIR", "LAND", "SEA"]),
    ("departureFlightType", "离境交通类型", "combo",
     ["COMMERCIAL FLIGHT", "PRIVATE FLIGHT", "CHARTER FLIGHT", "OTHER"]),
    ("departureFlightNo", "离境航班/车船号", "text"),
    ("accommodationType", "住宿类型", "combo",
     ["HOTEL", "FRIEND'S HOUSE", "GUESTHOUSE", "HOSTEL", "RESORT", "OTHER"]),
    ("accommodationProvince", "府/省", "text"),
    ("accommodationDistrict", "区/县", "text"),
    ("accommodationSubdistrict", "分区/街道", "text"),
    ("accommodationPostalCode", "邮编", "text"),
    ("accommodationAddress", "详细地址", "text"),
]

DYNAMIC_FIELDS: List[Tuple] = [
    ("last14DaysCountries", "过去 14 天停留国家/地区（用逗号分隔）", "text"),
    ("yellowFeverVaccination", "黄热病疫苗/风险行程", "combo", ["YES", "NO"]),
    ("symptoms", "症状申报", "combo", ["YES", "NO"]),
]


# --------------------------------------------------------------------------- #
# 基础小部件
# --------------------------------------------------------------------------- #
class SecretEntry(ttk.Frame):
    """默认遮挡的输入框；只有用户点击「显示」才揭示完整内容。"""

    def __init__(self, master, width: int = 34, **kw):
        super().__init__(master, **kw)
        self.var = tk.StringVar()
        self._hidden = True
        self.entry = ttk.Entry(self, textvariable=self.var, width=width, show="*")
        self.entry.grid(row=0, column=0, sticky="we")
        self.btn = ttk.Button(self, text="显示", width=6, command=self.toggle)
        self.btn.grid(row=0, column=1, padx=(4, 0))
        self.columnconfigure(0, weight=1)

    def toggle(self) -> None:
        self._hidden = not self._hidden
        self.entry.configure(show="*" if self._hidden else "")
        self.btn.configure(text="显示" if self._hidden else "隐藏")

    def get(self) -> str:
        return self.var.get().strip()

    def set(self, value: str) -> None:
        self.var.set(value or "")


class FormGrid(ttk.Frame):
    """按字段定义生成一个「标签 + 输入控件」表单，并负责取值 / 赋值。"""

    def __init__(self, master, fields: List[Tuple], on_secret_toggle=None):
        super().__init__(master)
        self.fields = fields
        self.widgets: Dict[str, Any] = {}
        self.columnconfigure(1, weight=1)
        for i, spec in enumerate(fields):
            key, label, kind = spec[0], spec[1], spec[2]
            options = spec[3] if len(spec) > 3 else None
            ttk.Label(self, text=label, font=FONT).grid(
                row=i, column=0, sticky="w", padx=(0, 10), pady=4)
            if kind == "secret":
                w = SecretEntry(self, width=34)
            elif kind == "combo":
                w = ttk.Combobox(self, values=options or [], width=32)
            else:
                w = ttk.Entry(self, width=34)
            w.grid(row=i, column=1, sticky="we", pady=4)
            self.widgets[key] = w

    def values(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, w in self.widgets.items():
            out[key] = w.get() if hasattr(w, "get") else ""
        return out

    def load(self, data: Dict[str, Any]) -> None:
        for key, w in self.widgets.items():
            raw = data.get(key, "")
            if isinstance(raw, list):
                raw = ", ".join(str(x) for x in raw)
            text = "" if raw is None else str(raw)
            if hasattr(w, "set"):
                w.set(text)
            else:
                w.delete(0, "end")
                w.insert(0, text)


# --------------------------------------------------------------------------- #
# 关键字段二次确认对话框
# --------------------------------------------------------------------------- #
class CriticalConfirmDialog(tk.Toplevel):
    """护照号 / 手机号 / 收件邮箱的二次确认。

    * 默认只显示打码值；**只有点击该行的「显示」按钮**才揭示完整内容。
    * 三行全部勾选后才能点「确认并保存」。
    * 与 ``tdac_privacy.confirm_critical_fields`` 的 ``ask`` / ``reveal`` 契约对接，
      真正复用了共享的确认逻辑（而非另写一套）。
    """

    def __init__(self, master, profile: Dict[str, Any]):
        super().__init__(master)
        self.title("请再次核对以下关键信息")
        self.resizable(False, False)
        self.profile = dict(profile)
        self.review = tdac_privacy.critical_fields_review(profile)
        self.accepted = False
        self._rows: Dict[str, Dict[str, Any]] = {}

        head = ttk.Label(
            self,
            text=("以下三项将用于正式申报，请与证件 / 邮箱逐一核对。\n"
                  "为保护隐私，默认只显示打码值；点击「显示」才会展示完整内容。"),
            font=FONT, justify="left")
        head.grid(row=0, column=0, columnspan=5, sticky="w", padx=14, pady=(14, 8))

        for i, it in enumerate(self.review, start=1):
            field, label, masked = it["field"], it["label"], it["masked"]
            ttk.Label(self, text=label, font=FONT_BOLD).grid(
                row=i, column=0, sticky="w", padx=(14, 8), pady=6)
            value_lbl = ttk.Label(self, text=masked or "（未填写）", font=FONT, width=22)
            value_lbl.grid(row=i, column=1, sticky="w", pady=6)
            show_btn = ttk.Button(self, text="显示", width=6,
                                  command=lambda f=field: self._reveal(f))
            show_btn.grid(row=i, column=2, padx=4, pady=6)
            edit_btn = ttk.Button(self, text="重新输入", width=9,
                                  command=lambda f=field: self._revise(f))
            edit_btn.grid(row=i, column=3, padx=4, pady=6)
            var = tk.BooleanVar(value=False)
            chk = ttk.Checkbutton(self, text="已核对", variable=var)
            chk.grid(row=i, column=4, padx=(8, 14), pady=6)
            self._rows[field] = {"label": label, "value_lbl": value_lbl,
                                 "var": var, "show_btn": show_btn}

        bar = ttk.Frame(self)
        bar.grid(row=len(self.review) + 1, column=0, columnspan=5, sticky="e",
                 padx=14, pady=(10, 14))
        ttk.Button(bar, text="取消", width=10, command=self._cancel).grid(row=0, column=0, padx=6)
        self.ok_btn = ttk.Button(bar, text="确认并保存", width=12, command=self._accept)
        self.ok_btn.grid(row=0, column=1)

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.transient(master)
        self.grab_set()

    # --- 交互：只有用户点击才揭示完整值 -------------------------------------
    def _reveal(self, field: str) -> None:
        value = str(self.profile.get(field, "") or "")
        row = self._rows.get(field)
        if row is None:
            return
        if row["value_lbl"].cget("text") == (value or "（未填写）"):
            row["value_lbl"].configure(text=tdac_privacy.mask(value) or "（未填写）")
            row["show_btn"].configure(text="显示")
        else:
            row["value_lbl"].configure(text=value or "（未填写）")
            row["show_btn"].configure(text="隐藏")

    def _revise(self, field: str) -> None:
        row = self._rows[field]
        new = simpledialog.askstring(
            "重新输入", f"请重新输入{row['label']}：", parent=self, show="*")
        if new is None:
            return
        new = new.strip()
        if not new:
            messagebox.showwarning("提示", "输入为空，已忽略。", parent=self)
            return
        self.profile[field] = new
        row["value_lbl"].configure(text=tdac_privacy.mask(new))
        row["show_btn"].configure(text="显示")
        row["var"].set(True)

    def _accept(self) -> None:
        if not all(r["var"].get() for r in self._rows.values()):
            messagebox.showwarning("请先核对", "请对三项关键信息都勾选「已核对」。", parent=self)
            return
        self.accepted = True
        self.destroy()

    def _cancel(self) -> None:
        self.accepted = False
        self.destroy()

    # --- 供 tdac_privacy.confirm_critical_fields 使用 -----------------------
    def reveal(self, field: str) -> Optional[str]:
        """只在用户点击「显示」时由本对话框内部调用；返回值仅用于展示。"""
        return str(self.profile.get(field, "") or "")

    def ask(self, prompt: str, reveal=None) -> bool:
        for field, row in self._rows.items():
            if row["label"] in prompt:
                return bool(self.accepted and row["var"].get())
        return False


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_TITLE}  v{tdac_update.APP_VERSION}")
        self.root.geometry("980x720")
        self.root.minsize(880, 640)

        self._queue: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._control: Optional[tdac_fill.RunControl] = None
        self._last_output = ""

        # G1 草稿 / 预填门禁状态
        self.draft_loaded = False            # 启动时是否自动恢复了本地草稿
        self.draft_stale = False             # 草稿比正式资料更旧，未自动恢复
        self.draft_corrupt = False           # 草稿文件存在但无法解析
        self.draft_time_abnormal = False     # 草稿时间戳异常（按“优先恢复 + 提示”处理）
        self._critical_confirmed = False     # 关键字段是否已由本人二次确认（且未被改动）
        self._critical_confirmed_at = ""
        self._critical_fingerprint_saved = ""
        self._health_reconfirmed = False     # 健康声明是否在本次使用中由本人重新确认
        self._snapshot = ""                  # 最近一次“已保存/已载入”的界面快照，用于未保存提醒

        os.makedirs(tdac_paths.config_dir(), exist_ok=True)
        tdac_paths.ensure_user_config()

        self.profile: Dict[str, Any] = self._load_json("profile.json")
        self.trip: Dict[str, Any] = self._load_json("trip.json")
        self.dynamic: Dict[str, Any] = self._load_json("dynamic.json")
        self.secrets: Dict[str, Any] = self._load_secrets()
        self._sync_email()

        self._build_ui()
        self._reload_forms()
        self._refresh_critical_confirmed()
        self.recover_draft_transaction_if_any()
        self._restore_draft_if_any()
        self._refresh_draft_state()
        self._mark_clean()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._poll_queue()

    # ------------------------------------------------------------------ 事务
    def recover_draft_transaction_if_any(self) -> None:
        """修复上次可能被中断的草稿提交（P1-01）。

        ``draft.json`` 与草稿密文必须同版本；若上次写入中途崩溃，这里会把它
        回滚到上一次**完整**保存，绝不让两个版本混在一起。
        """
        try:
            result = tdac_draft.recover_draft_transaction()
        except Exception as e:
            messagebox.showwarning(
                "草稿一致性检查失败",
                f"{type(e).__name__}: {e}\n将按现有文件继续，不做任何改动。")
            return
        if result == tdac_draft.RECOVER_ROLLED_BACK:
            messagebox.showinfo(
                "草稿已回滚到上一次完整版本",
                "检测到上次暂存草稿时被中断（两份文件版本不一致）。\n"
                "已把草稿回滚到上一次完整保存的版本，保证内容一致。")
        elif result == tdac_draft.RECOVER_FINALIZED:
            self.status.configure(text="草稿：上次暂存已完成，已补做收尾")

    # ------------------------------------------------------------------ 数据
    @staticmethod
    def _load_json(name: str) -> Dict[str, Any]:
        path = os.path.join(tdac_paths.config_dir(), name)
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            messagebox.showwarning("配置读取失败",
                                   f"{name} 无法解析：{type(e).__name__}。\n将按空配置继续，"
                                   "请重新填写后保存。")
            return {}

    def _load_secrets(self) -> Dict[str, Any]:
        try:
            return tdac_sec.load_secrets()
        except FileNotFoundError:
            return {}
        except Exception as e:
            messagebox.showwarning(
                "加密资料无法读取",
                "已存在的加密资料无法解密，常见原因是更换了 Windows 账户或电脑。\n"
                f"（{type(e).__name__}）\n请重新录入护照号与手机号。")
            return {}

    def _save_json(self, name: str, data: Dict[str, Any]) -> None:
        """写入正式配置：统一走受管路径保护 + 原子写入（P2）。

        * 目标必须位于受管数据目录内（``tdac_paths.assert_managed_write``），
          否则抛 ``PermissionError``，避免路径拼接错误写到源码目录；
        * 先写同目录临时文件、``fsync`` 后再 ``os.replace``，中断不会留下半截配置。
        """
        path = os.path.join(tdac_paths.config_dir(), name)
        payload = {k: v for k, v in data.items()
                   if not k.startswith("_") or k.startswith("_说明")}
        tdac_paths.atomic_write_json(path, payload)

    def _sync_email(self) -> None:
        """让 profile.email 与 dynamic.receiverEmail 保持一致（收件邮箱核对）。"""
        email = str(self.profile.get("email") or self.dynamic.get("receiverEmail") or "")
        self.profile["email"] = email
        self.dynamic["receiverEmail"] = email

    # ------------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except Exception:
            pass
        style.configure(".", font=FONT)

        header = ttk.Frame(self.root, padding=(14, 12, 14, 6))
        header.pack(fill="x")
        ttk.Label(header, text=APP_TITLE, font=FONT_TITLE).pack(side="left")
        ttk.Label(header, text=f"v{tdac_update.APP_VERSION}", font=FONT_SMALL).pack(
            side="left", padx=(8, 0), pady=(6, 0))

        self._draft_bar(self.root)

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=12, pady=6)
        self.nb = nb

        self._tab_profile(nb)
        self._tab_trip(nb)
        self._tab_health(nb)
        self._tab_run(nb)
        self._tab_tools(nb)
        self._tab_about(nb)

        self.status = tk.Label(self.root, text="就绪", anchor="w", font=FONT_SMALL,
                               bg="#eef2f7", padx=10, pady=4)
        self.status.pack(fill="x", side="bottom")

    # --- 草稿操作区（G1 验收新增）-----------------------------------------
    def _draft_bar(self, parent) -> None:
        """标题栏下方的草稿操作区：暂存 / 放弃 + 当前草稿状态。

        「暂存草稿」**不做**任何必填 / 格式校验，资料不完整也允许保存；
        真正的完整性校验只在「开始正式预填」时执行。
        """
        bar = ttk.Frame(parent, padding=(14, 0, 14, 4))
        bar.pack(fill="x")
        self.btn_draft = ttk.Button(bar, text="暂存草稿（资料可暂不完整）",
                                    command=self.save_draft_now)
        self.btn_draft.pack(side="left")
        self.btn_discard = ttk.Button(bar, text="放弃草稿", command=self.discard_draft)
        self.btn_discard.pack(side="left", padx=8)
        self.draft_state = tk.Label(bar, text="草稿：无", anchor="w",
                                    font=FONT_SMALL, fg="#555")
        self.draft_state.pack(side="left", padx=12)

    # --- 个人资料 ---------------------------------------------------------
    def _tab_profile(self, nb) -> None:
        f = ttk.Frame(nb, padding=14)
        nb.add(f, text=" 个人资料 ")
        ttk.Label(f, text="长期个人资料（与护照一致）", font=FONT_BOLD).pack(anchor="w")

        body = ttk.Frame(f)
        body.pack(fill="both", expand=True, pady=(8, 0))
        left = FormGrid(body, PROFILE_FIELDS)
        left.pack(side="left", fill="both", expand=True)
        self.profile_form = left

        sec = ttk.LabelFrame(body, text="敏感证件信息（加密保存，不写入 config）", padding=12)
        sec.pack(side="left", fill="y", padx=(16, 0))
        self.secret_widgets: Dict[str, SecretEntry] = {}
        for i, (key, label) in enumerate(SECRET_FIELDS):
            ttk.Label(sec, text=label, font=FONT).grid(row=i, column=0, sticky="w", pady=6)
            w = SecretEntry(sec, width=26)
            w.grid(row=i, column=1, sticky="we", pady=6)
            self.secret_widgets[key] = w
        ttk.Label(sec, text="保存时使用 Windows DPAPI 加密，仅当前账户可解密。",
                  font=FONT_SMALL, foreground="#555").grid(
            row=len(SECRET_FIELDS), column=0, columnspan=2, sticky="w", pady=(8, 0))

        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(12, 0))
        ttk.Button(bar, text="校验并保存个人资料", command=self.save_profile).pack(side="left")
        ttk.Button(bar, text="重新载入", command=self._reload_forms).pack(side="left", padx=8)

    # --- 行程与住宿 -------------------------------------------------------
    def _tab_trip(self, nb) -> None:
        f = ttk.Frame(nb, padding=14)
        nb.add(f, text=" 行程与住宿 ")
        ttk.Label(f, text="本次行程与在泰住宿", font=FONT_BOLD).pack(anchor="w")
        self.trip_form = FormGrid(f, TRIP_FIELDS)
        self.trip_form.pack(fill="both", expand=True, pady=(8, 0))
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(12, 0))
        ttk.Button(bar, text="保存行程与住宿", command=self.save_trip).pack(side="left")

    # --- 健康声明 ---------------------------------------------------------
    def _tab_health(self, nb) -> None:
        f = ttk.Frame(nb, padding=14)
        nb.add(f, text=" 健康声明 ")
        ttk.Label(f, text="当天需你本人确认的健康信息", font=FONT_BOLD).pack(anchor="w")
        ttk.Label(f, text="健康答案必须按实际情况填写；缺值时程序会停止，绝不会替你选择「否」。",
                  font=FONT_SMALL, foreground="#a15c00").pack(anchor="w", pady=(2, 8))
        self.dyn_form = FormGrid(f, DYNAMIC_FIELDS)
        self.dyn_form.pack(anchor="w")

        self.var_days_confirmed = tk.BooleanVar(value=False)
        self.var_health_confirmed = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="我已确认：以上「过去 14 天停留国家/地区」属实",
                        variable=self.var_days_confirmed).pack(anchor="w", pady=(10, 2))
        ttk.Checkbutton(f, text="我已确认：以上健康声明由我本人在当天作出",
                        variable=self.var_health_confirmed).pack(anchor="w")
        ttk.Button(f, text="保存健康声明", command=self.save_health).pack(anchor="w", pady=(12, 0))

    # --- 运行 -------------------------------------------------------------
    def _tab_run(self, nb) -> None:
        f = ttk.Frame(nb, padding=14)
        nb.add(f, text=" 开始填写 ")

        self.banner = tk.Label(f, text="尚未开始", font=("Microsoft YaHei UI", 12, "bold"),
                               bg="#e9edf2", fg="#22303f", padx=12, pady=10, anchor="w")
        self.banner.pack(fill="x")
        ttk.Label(f, text=("“预填完成”只代表表单已自动填好并停在预览页；"
                           "是否“正式申报成功”只能由你在官网点击 Submit 并拿到回执后确认。\n"
                           "开始正式预填前会先检查：资料完整、格式正确、关键字段已由你确认、"
                           "且在申报窗口内；不满足时会明确列出原因，不会打开官网。"),
                  font=FONT_SMALL, foreground="#555", wraplength=900,
                  justify="left").pack(anchor="w", pady=(6, 10))

        row = ttk.Frame(f)
        row.pack(fill="x")
        self.btn_dry = ttk.Button(row, text="离线演练（不触网）", command=self.run_dry)
        self.btn_dry.pack(side="left")
        self.btn_real = ttk.Button(row, text="开始正式预填（打开官网）", command=self.run_real)
        self.btn_real.pack(side="left", padx=8)
        self.btn_pause = ttk.Button(row, text="暂停自动填写", command=self.toggle_pause,
                                   state="disabled")
        self.btn_pause.pack(side="left", padx=8)
        self.btn_stop = ttk.Button(row, text="停止并转人工接管", command=self.stop_run,
                                  state="disabled")
        self.btn_stop.pack(side="left")

        ttk.Label(f, text="运行状态", font=FONT_BOLD).pack(anchor="w", pady=(12, 4))
        self.log = ScrolledText(f, height=16, font=("Consolas", 9), wrap="word")
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

        sub = ttk.Frame(f)
        sub.pack(fill="x", pady=(10, 0))
        ttk.Button(sub, text="我已自行在官网提交并取得回执（记录本机状态）",
                   command=self.mark_submitted).pack(side="left")
        self.submit_state = ttk.Label(sub, text="正式申报状态：未确认", font=FONT_SMALL)
        self.submit_state.pack(side="left", padx=12)
        self._refresh_submit_state()

    # --- 工具 -------------------------------------------------------------
    def _tab_tools(self, nb) -> None:
        f = ttk.Frame(nb, padding=14)
        nb.add(f, text=" 工具 ")
        ttk.Label(f, text="维护与诊断", font=FONT_BOLD).pack(anchor="w")

        box = ttk.LabelFrame(f, text="脱敏诊断包（B4）", padding=12)
        box.pack(fill="x", pady=10)
        ttk.Label(box, text=("只包含版本、平台、布尔存在性等非敏感信息；不含姓名、护照、手机号、"
                             "邮箱、行程、浏览器凭据、截图或网页源码。导出前会自检，"
                             "命中敏感模式即拒绝导出。"),
                  font=FONT_SMALL, wraplength=880, justify="left").pack(anchor="w")
        bb = ttk.Frame(box)
        bb.pack(fill="x", pady=(8, 0))
        ttk.Button(bb, text="预览诊断包", command=self.preview_diagnostic).pack(side="left")
        ttk.Button(bb, text="导出诊断包…", command=self.export_diagnostic).pack(side="left", padx=8)

        vbox = ttk.LabelFrame(f, text="版本检查（B3）", padding=12)
        vbox.pack(fill="x", pady=10)
        ttk.Label(vbox, text=("只有在点击下面按钮时才会联网，且只读取版本号与官方下载页；"
                              "不会自动下载或安装。版本来源可在 config/update.json 配置，"
                              "当前仓库尚未建立，因此未预置地址。"),
                  font=FONT_SMALL, wraplength=880, justify="left").pack(anchor="w")
        ttk.Button(vbox, text="检查新版本（需联网）", command=self.check_update).pack(
            anchor="w", pady=(8, 0))

        sbox = ttk.LabelFrame(f, text="浏览器会话与调试端口（B7）", padding=12)
        sbox.pack(fill="x", pady=10)
        ttk.Button(sbox, text="查看会话说明与残留检测", command=self.show_session).pack(anchor="w")

        dbox = ttk.LabelFrame(f, text="危险操作", padding=12)
        dbox.pack(fill="x", pady=10)
        ttk.Label(dbox, text=("删除本程序管理的用户资料（配置、密文（含草稿密文）、"
                              "浏览器资料、日志、输出）。\n"
                              "不会删除程序本体与模板；也不会触碰原项目目录。" +
                              ("\n当前数据目录：" + tdac_paths.data_root())),
                  font=FONT_SMALL, justify="left").pack(anchor="w")
        ttk.Button(dbox, text="删除受管用户资料…", command=self.delete_data).pack(
            anchor="w", pady=(8, 0))
        ttk.Button(f, text="打开用户数据目录", command=lambda: self._open_dir(
            tdac_paths.data_root())).pack(anchor="w", pady=(10, 0))

    # --- 关于 -------------------------------------------------------------
    def _tab_about(self, nb) -> None:
        f = ttk.Frame(nb, padding=14)
        nb.add(f, text=" 关于 ")
        text = ScrolledText(f, wrap="word", font=FONT)
        text.pack(fill="both", expand=True)
        text.insert("1.0", ABOUT_TEXT)
        text.configure(state="disabled")

    # ------------------------------------------------------------------ 表单
    def _reload_forms(self) -> None:
        self.profile_form.load(self.profile)
        self.trip_form.load(self.trip)
        dyn = dict(self.dynamic)
        countries = dyn.get("last14DaysCountries")
        if isinstance(countries, list):
            dyn["last14DaysCountries"] = ", ".join(str(x) for x in countries)
        self.dyn_form.load(dyn)
        for key, w in self.secret_widgets.items():
            w.set(str(self.secrets.get(key, "")))
        self.var_days_confirmed.set(bool(self.dynamic.get("last14DaysConfirmed")))
        self.var_health_confirmed.set(bool(self.dynamic.get("healthDeclarationConfirmed")))

    def _collect_profile(self) -> Dict[str, Any]:
        p = self.profile_form.values()
        for key, w in self.secret_widgets.items():
            p[key] = w.get()
        return p

    def _collect_secrets(self) -> Dict[str, Any]:
        return {key: w.get() for key, w in self.secret_widgets.items()}

    def _collect_dynamic(self) -> Dict[str, Any]:
        data = self.dyn_form.values()
        countries = [c.strip().upper() for c in str(data.get("last14DaysCountries", "")).split(",")
                     if c.strip()]
        dyn = dict(self.dynamic)
        dyn.update({
            "last14DaysCountries": countries,
            "yellowFeverVaccination": str(data.get("yellowFeverVaccination", "")).strip().upper(),
            "symptoms": str(data.get("symptoms", "")).strip().upper(),
            "last14DaysConfirmed": bool(self.var_days_confirmed.get()),
            "healthDeclarationConfirmed": bool(self.var_health_confirmed.get()),
            "receiverEmail": str(self.profile_form.widgets["email"].get() or ""),
        })
        return dyn

    def _collect_all(self) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        """收集界面上的全部内容（个人资料含敏感字段、行程与住宿、健康声明）。"""
        return self._collect_profile(), self.trip_form.values(), self._collect_dynamic()

    # ------------------------------------------------------------------ 草稿状态
    def _form_snapshot(self) -> str:
        profile, trip, dynamic = self._collect_all()
        return json.dumps({"profile": profile, "trip": trip, "dynamic": dynamic},
                          sort_keys=True, ensure_ascii=False)

    def _mark_clean(self) -> None:
        """记录“当前界面内容已落盘（保存或暂存）”的快照。"""
        self._snapshot = self._form_snapshot()

    def _is_dirty(self) -> bool:
        """是否存在尚未保存 / 尚未暂存的界面修改（用于关闭窗口提醒）。"""
        try:
            return self._form_snapshot() != self._snapshot
        except Exception:
            return False

    def _saved_config(self, name: str) -> Dict[str, Any]:
        """静默读取用户数据目录中的配置文件（不弹窗），失败返回空字典。"""
        path = os.path.join(tdac_paths.config_dir(), name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _refresh_critical_confirmed(self) -> None:
        """判断“关键字段是否已由本人二次确认，且此后未被改动”。

        判据来自 ``tdac_gate``：``profile.json`` 里由正式保存流程写入的确认时间 +
        指纹（``tdac_privacy.critical_fingerprint``，不可逆摘要），与当前护照号 /
        手机号 / 邮箱一致才算通过。界面与命令行共用同一套判据。
        """
        record = tdac_gate.load_confirm_record()
        current = {"passportNo": str(self.secrets.get("passportNo", "") or ""),
                   "phoneNo": str(self.secrets.get("phoneNo", "") or ""),
                   "email": str(self.profile.get("email", "") or "")}
        self._critical_confirmed_at = record.get("confirmedAt", "")
        self._critical_fingerprint_saved = record.get("fingerprint", "")
        self._critical_confirmed = bool(
            self._critical_confirmed_at and self._critical_fingerprint_saved
            and self._critical_fingerprint_saved
            == tdac_privacy.critical_fingerprint(current))

    def _refresh_draft_state(self) -> None:
        """刷新草稿状态标签（与“预填状态”“正式申报状态”互相独立）。"""
        if not tdac_draft.draft_exists():
            self.draft_state.configure(text="草稿：无（尚未暂存）", fg="#555")
            return
        if self.draft_corrupt:
            self.draft_state.configure(
                text="草稿：文件存在但无法解析（未自动恢复；可在草稿区放弃）", fg="#a15c00")
            return
        info = tdac_draft.read_draft_file()
        if not info:
            self.draft_state.configure(
                text="草稿：文件存在但无法解析（未自动恢复；可在草稿区放弃）", fg="#a15c00")
            return
        if self.draft_stale:
            self.draft_state.configure(
                text=tdac_draft.describe_draft(info, "比正式资料更旧，未自动恢复"),
                fg="#a15c00")
            return
        action = "已恢复，可继续编辑" if self.draft_loaded else "已暂存"
        self.draft_state.configure(text=tdac_draft.describe_draft(info, action), fg="#0b6b3a")

    # ------------------------------------------------------------------ 草稿操作
    def save_draft_now(self, announce: bool = True) -> bool:
        """暂存草稿：**不做任何必填 / 格式校验**，资料不完整也允许保存。

        敏感字段沿用现有 DPAPI 加密写入独立的草稿密文，不产生明文副本；
        暂存不会改动已确认的正式资料，也不会开始官网预填。
        """
        profile, trip, dynamic = self._collect_all()
        try:
            tdac_draft.save_draft(profile, trip, dynamic, secrets=self._collect_secrets())
        except tdac_draft.DraftError as e:
            # 明确报错：旧密文无法解密时**不会**覆盖旧资料，草稿原样保留。
            messagebox.showerror("暂存草稿已中止（未覆盖旧资料）", str(e))
            self.status.configure(text="草稿未保存：旧草稿密文无法解密，已保留原文件")
            return False
        except Exception as e:
            messagebox.showerror("暂存草稿失败", f"{type(e).__name__}: {e}")
            return False
        self.draft_loaded = False
        self.draft_stale = False
        self.draft_corrupt = False
        self.draft_time_abnormal = False
        self._mark_clean()
        self._refresh_draft_state()
        self._warn_acl_if_any()
        self.status.configure(text="草稿已暂存（未做必填校验；敏感字段已加密）")
        if announce:
            messagebox.showinfo(
                "草稿已暂存",
                "当前内容已暂存为本机草稿，下次打开会自动恢复，可继续修改。\n\n"
                "· 草稿允许资料不完整，不做必填 / 格式校验；\n"
                "· 护照号与手机号沿用 Windows DPAPI 加密，不产生明文副本；\n"
                "· 草稿不会改动已确认的正式资料，也不会开始官网预填；\n"
                "· 健康声明即使暂存，正式填写前仍需你本人重新确认。\n\n"
                f"草稿位置：{tdac_draft.draft_path()}")
        return True

    def _restore_draft_if_any(self) -> None:
        """启动时自动恢复本地草稿（首版只保留一份草稿）。

        三种“不自动恢复”的情况都会**明确提示**，绝不静默降级、也不删除文件：

        * 草稿文件无法解析（损坏）；
        * 草稿比正式资料更旧（用户后来重新保存过正式资料）；
        * 草稿密文缺失 / 损坏 / 属于其它 Windows 账户。
        """
        status = tdac_draft.draft_status()
        if status == tdac_draft.STATUS_NONE:
            return
        if status == tdac_draft.STATUS_CORRUPT:
            self.draft_corrupt = True
            messagebox.showwarning(
                "草稿文件无法解析",
                "本机存在草稿文件，但内容已损坏，无法自动恢复。\n\n"
                "为安全起见，草稿文件保持不动，程序将按正式资料继续。\n"
                "可在顶部草稿区选择「放弃草稿」清理该文件；"
                "若希望保留，请先备份后再处理。")
            return
        if tdac_draft.draft_is_stale():
            self.draft_stale = True
            messagebox.showwarning(
                "草稿比正式资料更旧",
                "检测到草稿的暂存时间早于最近一次正式保存的资料。\n\n"
                "为避免用较旧的内容覆盖你更新的正式资料，本次**不自动恢复草稿**，"
                "程序将按正式资料继续。\n\n"
                "草稿文件保持不动：确认不再需要时，可在顶部草稿区「放弃草稿」清理。")
            return
        # 时间戳异常（P2）：不判“过期”，而是按“优先恢复 + 明确提示”处理，
        # 避免因一个坏时间戳丢失用户尚未确认的填写成果。
        if tdac_draft.draft_time_status() == tdac_draft.TIME_ABNORMAL:
            self.draft_time_abnormal = True
            messagebox.showwarning(
                "草稿保存时间异常",
                "草稿里记录的保存时间不可信（可能被外部修改，或系统时间异常）。\n\n"
                "为避免丢失你尚未确认的填写成果，程序仍会恢复这份草稿，"
                "请核对内容后再使用；不需要时可在顶部草稿区「放弃草稿」清理。")
        try:
            draft = tdac_draft.load_draft()
        except Exception as e:
            self.draft_corrupt = True
            messagebox.showwarning(
                "草稿无法读取",
                f"{e}\n\n（{type(e).__name__}）\n"
                "将按正式资料继续，草稿文件保持不动。")
            return
        if not draft:
            return
        profile = dict(self.profile)
        profile.update(draft.get("profile") or {})
        trip = dict(self.trip)
        trip.update(draft.get("trip") or {})
        dynamic = dict(self.dynamic)
        dynamic.update(draft.get("dynamic") or {})
        # 健康声明：草稿中的“已确认”标记一律不继承，正式填写前须本人重新确认。
        dynamic["last14DaysConfirmed"] = False
        dynamic["healthDeclarationConfirmed"] = False
        present = draft.get("secretsPresent") or {}
        secrets = draft.get("secrets") or {}
        self.profile, self.trip, self.dynamic = profile, trip, dynamic
        self.secrets = {key: (str(secrets.get(key, "")) if present.get(key) else "")
                        for key in self.secret_widgets}
        self.draft_loaded = True
        self._health_reconfirmed = False
        self._reload_forms()
        # 恢复后的敏感字段可能和正式确认时的不同，重新判定关键字段确认状态，
        # 避免用到过期的确认缓存（门禁本身也会再次比对指纹）。
        self._refresh_critical_confirmed()
        self.status.configure(
            text="已自动恢复本地草稿：可继续修改；健康声明需在正式填写前重新确认")

    def discard_draft(self) -> None:
        """放弃草稿：删除本机唯一的草稿，并回到已保存的正式资料。

        **只**删除草稿自己的两个文件；不触碰 profile.json / trip.json /
        dynamic.json / secrets.dat 等正式资料。
        """
        if not tdac_draft.draft_exists():
            messagebox.showinfo("没有草稿", "当前没有本地草稿。")
            return
        dirty_hint = ("\n\n注意：界面里尚未保存 / 尚未暂存的修改也会一并丢弃。"
                      if self._is_dirty() else "")
        if not messagebox.askyesno(
                "放弃草稿（仅删除草稿文件）",
                "将删除本机唯一的草稿文件：\n"
                "  · config\\draft.json\n"
                "  · secrets\\draft-secrets.dat（护照号 / 手机号密文）\n\n"
                "**不会**删除已保存的正式资料"
                "（profile.json / trip.json / dynamic.json / secrets.dat）。\n"
                "删除后界面会重新载入正式资料。" + dirty_hint + "\n\n确定继续吗？"):
            return
        try:
            tdac_draft.delete_draft()
        except Exception as e:
            messagebox.showerror("删除草稿失败", f"{type(e).__name__}: {e}")
            return
        self.draft_loaded = False
        self.draft_stale = False
        self.draft_corrupt = False
        self.draft_time_abnormal = False
        self._reload_from_config()
        self._refresh_draft_state()
        self.status.configure(text="草稿已删除，已回到正式资料")

    def _reload_from_config(self) -> None:
        """重新从用户数据目录载入正式资料（草稿不参与）。"""
        self.profile = self._load_json("profile.json")
        self.trip = self._load_json("trip.json")
        self.dynamic = self._load_json("dynamic.json")
        self.secrets = self._load_secrets()
        self._sync_email()
        self._refresh_critical_confirmed()
        self._health_reconfirmed = False
        self._reload_forms()

    # ------------------------------------------------------------------ 预填门禁
    def _prefill_blocker_groups(self) -> List[Tuple[str, List[str]]]:
        """正式（官网）预填的门禁：按「个人资料 / 行程住宿 / 健康声明」分组的阻断原因。

        每次都按**界面当前内容**重新校验，因此改过护照 / 手机号 / 邮箱 / 行程 /
        住宿 / 健康资料后，都会重新判定（不存在“改完还沿用旧结论”的情况）。
        与「暂存草稿」完全分离——暂存不调用本方法。
        """
        profile, trip, dynamic = self._collect_all()
        # 这里仅预检资料内容；真正的关键字段/健康声明确认在点击“开始正式预填”
        # 后当次执行，并以同进程一次性授权交给 tdac_fill 的统一门禁消费。
        return tdac_gate.data_blocker_groups(profile, trip, dynamic)

    def _prefill_blockers(self) -> List[str]:
        """扁平化的阻断原因列表（保留给既有调用方与测试）。"""
        return [item for _group, items in self._prefill_blocker_groups() for item in items]

    @staticmethod
    def _format_blockers(groups: List[Tuple[str, List[str]]]) -> str:
        """把分组阻断原因排成易读的中文段落。"""
        return "\n\n".join(
            f"【{group}】\n" + "\n".join("· " + item for item in items)
            for group, items in groups)

    def _persist_all(self) -> bool:
        """把界面当前内容写入正式配置，供自动填写流程读取。

        只有通过预填门禁后才会调用，因此这里的 profile 必然是“完整 + 格式正确 +
        已由本人二次确认”的那一组；确认指纹原样沿用，不产生新的确认行为。
        """
        profile, trip, dynamic = self._collect_all()
        stored = {k: v for k, v in profile.items() if k not in tdac_sec.SECRET_KEYS}
        stored[tdac_gate.CONFIRM_AT_KEY] = self._critical_confirmed_at
        stored[tdac_gate.CONFIRM_FP_KEY] = tdac_privacy.critical_fingerprint(profile)
        try:
            self._save_json("profile.json", stored)
            tdac_sec.save_secrets({k: profile.get(k, "") for k in tdac_sec.SECRET_KEYS})
            self._save_json("trip.json", trip)
            self._save_json("dynamic.json", dynamic)
        except Exception as e:
            messagebox.showerror("保存失败", f"{type(e).__name__}: {e}")
            return False
        self.profile.update(stored)
        self.trip.update(trip)
        self.dynamic.update(dynamic)
        self.secrets = {k: profile.get(k, "") for k in tdac_sec.SECRET_KEYS}
        self._refresh_critical_confirmed()
        self._mark_clean()
        return True

    # ------------------------------------------------------------------ 保存
    def save_profile(self) -> None:
        profile = self._collect_profile()
        for key in ("passportNo", "phoneNo"):
            if not str(profile.get(key, "")).strip():
                messagebox.showwarning("缺少必填项",
                                       f"{dict(SECRET_FIELDS).get(key, key)} 不能为空。")
                return
        problems = tdac_validate.validate_profile(profile)
        if problems:
            messagebox.showerror("格式校验未通过",
                                 tdac_validate.format_problems(problems) + "\n\n请修正后再保存。")
            return
        dlg = CriticalConfirmDialog(self.root, profile)
        self.root.wait_window(dlg)
        if not dlg.accepted:
            self.status.configure(text="已取消保存个人资料")
            return
        ok, unconfirmed = tdac_privacy.confirm_critical_fields(
            dlg.profile, ask=dlg.ask, reveal=dlg.reveal)
        if not ok:
            messagebox.showerror("未通过二次确认", "以下字段未确认：" + "、".join(unconfirmed))
            return

        profile = dlg.profile
        # 收件邮箱同时写入 dynamic.receiverEmail，保证预览页与回执邮箱一致。
        stored = {k: v for k, v in profile.items() if k not in ("passportNo", "phoneNo")}
        # 记录“这组关键字段已由本人二次确认”的证据：时间 + 不可逆指纹。
        # 指纹由 tdac_privacy.critical_fingerprint 生成，此后任一关键字段被改动即失效，
        # 官网预填会被重新拦下，必须再次确认。
        stored["criticalConfirmedAt"] = _now_stamp()
        stored["criticalFingerprint"] = tdac_privacy.critical_fingerprint(profile)
        try:
            self._save_json("profile.json", stored)
            tdac_sec.save_secrets({"passportNo": profile.get("passportNo", ""),
                                   "phoneNo": profile.get("phoneNo", "")})
        except Exception as e:
            messagebox.showerror("保存失败", f"{type(e).__name__}: {e}")
            return
        self.profile.update(stored)
        self.secrets = {"passportNo": profile.get("passportNo", ""),
                        "phoneNo": profile.get("phoneNo", "")}
        self._sync_email()
        self._save_json("dynamic.json", self.dynamic)
        self._refresh_critical_confirmed()
        self._mark_clean()
        self.status.configure(text="个人资料已加密保存（关键字段已二次确认）")
        self._warn_acl_if_any()
        messagebox.showinfo("已保存", "个人资料已保存，敏感信息已用 Windows DPAPI 加密。")

    def save_trip(self) -> None:
        data = self.trip_form.values()
        for key in ("arrivalDate", "departureDate"):
            value = str(data.get(key, "")).strip()
            if value and not _is_date(value):
                messagebox.showerror("日期格式不正确",
                                     f"{key} 需为 YYYY/MM/DD（例如 2026/01/01）。")
                return
        self.trip.update(data)
        try:
            self._save_json("trip.json", self.trip)
        except Exception as e:
            messagebox.showerror("保存失败", f"{type(e).__name__}: {e}")
            return
        self._mark_clean()
        self.status.configure(text="行程与住宿已保存")

    def save_health(self) -> None:
        data = self.dyn_form.values()
        countries = [c.strip().upper() for c in str(data.get("last14DaysCountries", "")).split(",")
                     if c.strip()]
        dynamic = dict(self.dynamic)
        dynamic.update({
            "last14DaysCountries": countries,
            "yellowFeverVaccination": str(data.get("yellowFeverVaccination", "")).strip().upper(),
            "symptoms": str(data.get("symptoms", "")).strip().upper(),
            "last14DaysConfirmed": bool(self.var_days_confirmed.get()),
            "healthDeclarationConfirmed": bool(self.var_health_confirmed.get()),
            "receiverEmail": self.profile.get("email", ""),
        })
        if not countries:
            messagebox.showwarning("缺少必填项", "请填写过去 14 天停留的国家/地区。")
            return
        if not dynamic["yellowFeverVaccination"] or not dynamic["symptoms"]:
            if not messagebox.askyesno(
                    "健康答案未填写",
                    "黄热病疫苗与症状申报必须按实际情况选择 YES 或 NO。\n"
                    "留空会在自动填写时直接停止（程序不会替你选）。\n\n仍要保存吗？"):
                return
        self.dynamic = dynamic
        try:
            self._save_json("dynamic.json", self.dynamic)
        except Exception as e:
            messagebox.showerror("保存失败", f"{type(e).__name__}: {e}")
            return
        # 健康声明必须在**本次使用**中由本人重新确认；草稿恢复的确认标记不作数。
        self._health_reconfirmed = bool(
            dynamic["last14DaysConfirmed"] and dynamic["healthDeclarationConfirmed"])
        self._mark_clean()
        self.status.configure(
            text=("健康声明已保存（已本人确认）" if self._health_reconfirmed
                  else "健康声明已保存（尚未勾选本人确认，正式填写前需确认）"))

    # ------------------------------------------------------------------ 运行
    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self.btn_dry.configure(state=state)
        self.btn_real.configure(state=state)
        self.btn_pause.configure(state="normal" if running else "disabled")
        self.btn_stop.configure(state="normal" if running else "disabled")

    def _start_run(self, argv: List[str], banner_text: str, banner_bg: str) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("正在运行", "当前已有一次填写流程在运行。")
            return
        self._last_output = ""
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.banner.configure(text=banner_text, bg=banner_bg)
        self._set_running(True)
        control = tdac_fill.RunControl()
        control.on_status = lambda where: self._queue.put(("stage", where))
        self._control = control
        tdac_fill.set_run_control(control)
        self._worker = threading.Thread(target=self._run_flow, args=(argv,), daemon=True)
        self._worker.start()

    def _run_flow(self, argv: List[str]) -> None:
        class _Std:
            def __init__(self, q, tag):
                self.q, self.tag = q, tag

            def write(self, s):
                if s:
                    self.q.put(("out", s))
                return len(s)

            def flush(self):
                pass

            def isatty(self):
                return False

        old = (sys.argv, sys.stdout, sys.stderr, sys.stdin)
        writer = _Std(self._queue, "out")
        sys.argv = ["tdac_fill.py"] + argv
        sys.stdout = writer
        sys.stderr = writer
        sys.stdin = _Std(self._queue, "in")
        rc = 1
        try:
            rc = int(tdac_fill.main() or 0)
        except Exception as e:
            self._queue.put(("out", f"\n[界面] 运行异常：{type(e).__name__}: {e}\n"))
            rc = 1
        finally:
            sys.argv, sys.stdout, sys.stderr, sys.stdin = old
            tdac_fill.set_run_control(None)
            self._queue.put(("done", rc))

    def run_dry(self) -> None:
        self._start_run(["--dry-run"],
                        "正在离线演练（不打开浏览器、不联网）…", "#e8f1ff")

    def run_real(self) -> None:
        groups = self._prefill_blocker_groups()
        if groups:
            messagebox.showerror(
                "暂时不能开始官网预填",
                "正式预填要求：资料完整、格式正确、关键字段经你本人确认，"
                "且处于官网申报窗口内。\n\n当前未满足以下条件：\n\n"
                + self._format_blockers(groups)
                + "\n\n提示：可以先用顶部「暂存草稿」保存不完整的内容，稍后继续填写。")
            self.status.configure(text="已阻止官网预填：资料尚未满足正式申报要求")
            return
        profile, trip, dynamic = self._collect_all()
        dlg = CriticalConfirmDialog(self.root, profile)
        self.root.wait_window(dlg)
        if not dlg.accepted:
            self.status.configure(text="已取消本次关键字段确认")
            return
        ok, unconfirmed = tdac_privacy.confirm_critical_fields(
            dlg.profile, ask=dlg.ask, reveal=dlg.reveal)
        if not ok:
            messagebox.showerror("未通过二次确认", "以下字段未确认：" + "、".join(unconfirmed))
            return
        # 对话框允许更正关键字段；将更正同步回当前表单后再落盘和授权。
        self.secret_widgets["passportNo"].set(dlg.profile.get("passportNo", ""))
        self.secret_widgets["phoneNo"].set(dlg.profile.get("phoneNo", ""))
        self.profile_form.widgets["email"].set(dlg.profile.get("email", ""))
        profile, trip, dynamic = self._collect_all()
        if not tdac_gate.health_confirmed(dynamic):
            messagebox.showerror("健康声明未确认",
                                 "请先在「健康声明」页勾选并保存两项确认，再开始正式预填。")
            return
        if not messagebox.askyesno(
                "确认本次健康声明",
                "请确认：健康声明答案为本次申报填写的真实情况。\n\n确定继续吗？"):
            self.status.configure(text="已取消本次健康声明确认")
            return
        if not messagebox.askyesno(
                "确认开始正式预填",
                "即将打开泰国官方 TDAC 网站并自动预填。\n"
                "程序不会替你提交、不会替你勾选条款。\n\n确定继续吗？"):
            return
        if not tdac_gate.authorize_current_prefill(profile, dynamic, trip):
            messagebox.showerror("确认状态无效", "本次确认无法建立安全授权，请重新核对后再试。")
            return
        self._critical_confirmed_at = _now_stamp()
        self._critical_fingerprint_saved = tdac_privacy.critical_fingerprint(profile)
        self._critical_confirmed = True
        self._health_reconfirmed = True
        # 先把界面内容落到正式配置（自动填写流程读取磁盘配置）。
        # G1 最终修复：不再向命令行传 ``--skip-confirm`` 这类跳过确认的开关。
        # 关键字段的二次确认已经落盘为“确认时间 + 不可逆指纹”，命令行侧会用
        # 同一套证据（tdac_gate）重新校验；不满足时命令行同样会阻断。
        if not self._persist_all():
            return
        self._warn_acl_if_any()
        self._start_run([], "正在自动预填…（尚未提交）", "#fff4d6")

    @staticmethod
    def _warn_acl_if_any() -> None:
        """ACL 加固失败时明确提示，不静默忽略（P2）。"""
        detail = tdac_sec.acl_warning()
        if detail:
            messagebox.showwarning(
                "文件权限未能完全收紧",
                "资料已保存，但未能把文件权限额外收紧为“仅当前用户”：\n\n"
                + detail +
                "\n\n文件仍受 Windows 用户账户保护；如需更严格，可在文件属性中手动设置权限。")

    def toggle_pause(self) -> None:
        if self._control is None:
            return
        if self._control.paused:
            self._control.resume()
            self.btn_pause.configure(text="暂停自动填写")
            self.status.configure(text="已继续自动填写")
        else:
            self._control.pause()
            self.btn_pause.configure(text="继续自动填写")
            self.status.configure(text="已暂停（可先在浏览器里人工处理，然后点继续）")

    def stop_run(self) -> None:
        if self._control is None:
            return
        self._control.stop()
        self.status.configure(text="已请求停止：将在下一个安全检查点停止并保留浏览器供人工接管")

    def on_close(self) -> None:
        """关闭窗口前提醒未保存的修改，避免分多次填写时丢资料。"""
        if self._worker and self._worker.is_alive():
            if not messagebox.askyesno(
                    "正在运行",
                    "自动填写流程仍在运行。仍要关闭界面吗？\n"
                    "（浏览器页面会被保留，可人工接管）"):
                return
        if self._is_dirty():
            answer = messagebox.askyesnocancel(
                "有未保存的修改",
                "当前填写内容还没有保存，直接关闭会丢失这些修改。\n\n"
                "· 「是」：先把当前内容暂存为本地草稿，再退出；\n"
                "· 「否」：不保存，直接退出；\n"
                "· 「取消」：返回界面继续编辑。")
            if answer is None:
                return
            if answer and not self.save_draft_now(announce=False):
                return
            if answer:
                self.status.configure(text="已暂存草稿后退出")
        self.root.destroy()

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "out":
                    self._last_output += payload
                    self.log.configure(state="normal")
                    self.log.insert("end", payload)
                    self.log.see("end")
                    self.log.configure(state="disabled")
                elif kind == "stage":
                    self.status.configure(text=f"当前步骤：{payload}")
                elif kind == "done":
                    self._on_done(payload)
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    def _on_done(self, rc: int) -> None:
        self._set_running(False)
        self.btn_pause.configure(text="暂停自动填写")
        self._control = None
        out = self._last_output
        if "预览阶段：PASS" in out:
            self.banner.configure(text="✅ 预填完成（尚未提交）", bg="#ffe0b2")
            self.status.configure(text="预填完成：请在浏览器中阅读条款、勾选并自行点击 Submit")
        elif "离线检查（不启动浏览器、不触网）" in out:
            verdict = "通过" if "FAIL=0" in out else "存在失败项，请查看日志"
            self.banner.configure(text=f"离线演练结束（{verdict}）", bg="#e8f1ff")
            self.status.configure(text="离线演练完成")
        else:
            self.banner.configure(text="⚠️ 未完成预填（已保留浏览器供人工接管）", bg="#ffe4e4")
            self.status.configure(text="自动流程未完成，请在浏览器中人工继续")

    def mark_submitted(self) -> None:
        import datetime as dt
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            self._save_json("submit_state.json", {"submittedByUserAt": stamp})
        except Exception:
            pass
        self.submit_state.configure(text=f"正式申报状态：已由本人确认提交（{stamp}）")
        messagebox.showinfo(
            "已记录",
            "已在本机记录“你已自行提交”。\n\n"
            "注意：本程序无法也无法验证官网是否真的受理成功——\n"
            "是否正式申报成功，请以官网回执与官方邮件为准。")

    def _refresh_submit_state(self) -> None:
        path = os.path.join(tdac_paths.config_dir(), "submit_state.json")
        stamp = ""
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    stamp = str(json.load(fh).get("submittedByUserAt", ""))
            except Exception:
                stamp = ""
        self.submit_state.configure(
            text=(f"正式申报状态：已由本人确认提交（{stamp}）" if stamp else "正式申报状态：未确认"))

    # ------------------------------------------------------------------ 工具
    def preview_diagnostic(self) -> None:
        try:
            text = tdac_diag.render_diagnostic(self._diag_data())
        except Exception as e:
            messagebox.showerror("无法生成诊断包", f"{type(e).__name__}: {e}")
            return
        _TextDialog(self.root, "诊断包预览（发送前请自行确认）", text)

    def export_diagnostic(self) -> None:
        path = filedialog.asksaveasfilename(
            title="保存脱敏诊断包", defaultextension=".txt",
            initialfile="TDAC_诊断包.txt", filetypes=[("文本文件", "*.txt")])
        if not path:
            return
        try:
            out = tdac_diag.export_diagnostic(dest_path=path, data=self._diag_data())
        except Exception as e:
            messagebox.showerror("导出失败", f"{type(e).__name__}: {e}")
            return
        messagebox.showinfo("已导出", f"诊断包已保存：\n{out}\n\n请预览后再决定是否发送。")

    def _diag_data(self) -> Dict[str, Any]:
        reader = tdac_diag.config_presence_reader(tdac_fill._read_json)
        return tdac_diag.collect_diagnostic(config_presence=reader)

    def check_update(self) -> None:
        if not messagebox.askyesno("联网检查版本",
                                   "即将联网读取版本信息（只读取版本号与官方下载页，"
                                   "不会下载或安装）。是否继续？"):
            return
        try:
            res = tdac_update.check_for_update(consent=True)
        except Exception as e:
            messagebox.showerror("检查失败", f"{type(e).__name__}: {e}")
            return
        messagebox.showinfo("版本检查结果", tdac_update.describe(res))

    def show_session(self) -> None:
        info = tdac_browser.detect_leftovers(tdac_paths.profile_dir())
        text = tdac_browser.SESSION_NOTES + "\n\n---- 当前残留检测（只读）----\n"
        text += tdac_browser.describe_leftovers(info) + "\n"
        _TextDialog(self.root, "浏览器会话与调试端口", text)

    def delete_data(self) -> None:
        root_dir = tdac_paths.data_root()
        targets = tdac_paths.managed_paths()
        if not targets:
            messagebox.showinfo("无需删除", "当前没有本程序管理的用户资料。")
            return
        ans = simpledialog.askstring(
            "确认删除",
            "将删除以下受管资料目录：\n" + "\n".join(targets) +
            "\n\n此操作不可撤销。请输入 DELETE 确认：",
            parent=self.root)
        if ans is None:
            return
        if ans.strip() != "DELETE":
            messagebox.showinfo("已取消", "输入不匹配，未删除任何内容。")
            return
        try:
            removed = tdac_paths.delete_managed_data(confirmed=True)
        except PermissionError as e:
            messagebox.showerror("已拒绝删除", str(e))
            return
        except RuntimeError as e:
            messagebox.showerror("删除未全部完成", str(e))
            return
        except Exception as e:
            messagebox.showerror("删除失败", f"{type(e).__name__}: {e}")
            return
        self.profile, self.trip, self.dynamic, self.secrets = {}, {}, {}, {}
        self.draft_loaded = False
        self._health_reconfirmed = False
        self._refresh_critical_confirmed()
        self._reload_forms()
        self._refresh_draft_state()
        self.status.configure(text="受管用户资料已删除")
        messagebox.showinfo("已删除", "已删除：\n" + ("\n".join(removed) or "（无）"))

    @staticmethod
    def _open_dir(path: str) -> None:
        try:
            os.makedirs(path, exist_ok=True)
            os.startfile(path)  # noqa: S606 (Windows 专用)
        except Exception:
            pass


class _TextDialog(tk.Toplevel):
    """只读文本查看窗口（预览诊断包 / 会话说明）。"""

    def __init__(self, master, title: str, text: str):
        super().__init__(master)
        self.title(title)
        self.geometry("760x560")
        box = ScrolledText(self, wrap="word", font=FONT)
        box.pack(fill="both", expand=True, padx=10, pady=10)
        box.insert("1.0", text)
        box.configure(state="disabled")
        ttk.Button(self, text="关闭", command=self.destroy).pack(pady=(0, 10))
        self.transient(master)


def _is_date(value: str) -> bool:
    import datetime as dt
    try:
        dt.datetime.strptime(value, "%Y/%m/%d")
        return True
    except Exception:
        return False


def _now_stamp() -> str:
    import datetime as dt
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


ABOUT_TEXT = """泰国电子入境卡（TDAC）填写助手 —— 关于
========================================

【这个程序做什么】
用你本机已安装的 Chrome / Edge 打开泰国官方 TDAC 网站，自动完成“预填 + 回读校验”，
然后停在预览页。它把你从重复的手工输入中解放出来，但**不会**替你做最后的决定。

【明确不做】
· 不自动提交、不自动勾选法律条款；
· 不绕过验证码、风控或官网日期窗口；
· 不猜测健康声明答案，缺值即停止；
· 不向任何第三方上传你的资料；预览 PDF 默认不生成。

【“预填完成” ≠ “正式申报成功”】
预填完成只表示表单已经填好、停在预览页。
是否正式申报成功，取决于**你本人**在官网阅读条款、勾选、点击 Submit 并取得回执。
本程序无法验证官网是否受理成功。

【草稿：可以分多次填写】
顶部有「暂存草稿 / 放弃草稿」。资料没填完也能先暂存，下次打开会自动恢复继续编辑。
· 暂存草稿不做必填 / 格式校验；只有「开始正式预填」才会做完整校验；
· 草稿里的护照号、手机号同样用 Windows DPAPI 加密，不产生明文副本；
· 草稿与已确认的正式资料互相独立，暂存不会覆盖正式资料；
· 重启时以“最近一次明确保存”的内容为准：若你后来又重新保存了正式资料，
  更旧的草稿不会被自动恢复（只提示、不删除）；
· 健康声明即使暂存过，正式填写前仍须你本人重新确认；
· 首版只保留一份本机草稿，不做云同步、多草稿或账号。

【三种状态互不混淆】
草稿（本机未完成的编辑现场）／预填完成（表单已填好、停在预览页）／
正式申报成功（只能由你本人在官网提交并取得回执后自行记录）。

【你的资料在哪里】
默认位于当前 Windows 用户的专属目录（%LOCALAPPDATA%\\TDAC\\），与程序目录分离，
因此升级程序不会覆盖你的资料。
护照号与手机号使用 Windows DPAPI 加密，仅当前 Windows 账户可解密；
换账户或换电脑需要重新录入。
草稿也在同一数据目录内（config\\draft.json 与 secrets\\draft-secrets.dat），
会随「删除受管用户资料」一并清理，不会留在别处。

【隐私保护】
日志默认脱敏；护照号、手机号、邮箱、航班号、邮编、地址都不写入完整值。
界面上敏感字段默认以 * 遮挡，只有你主动点击“显示”才会展示完整内容。
诊断包只包含白名单中的非敏感信息，导出前会自动自检。

【浏览器安全】
使用独立的浏览器用户目录，与你日常浏览完全隔离；
调试端口只绑定回环地址 127.0.0.1 且每次随机分配，局域网无法访问；
绝不使用会扩大远程调试访问范围的参数。

【支持范围】
首版仅支持 Windows 11 64 位、单人、中国普通护照 + 航空入境 + 旅游 + 酒店住宿场景。
本项目为非官方、免费的辅助工具，与泰国政府及移民局无任何隶属关系。
"""


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        root = tk.Tk()
    except Exception as e:
        print("无法启动图形界面（未找到可用的 tkinter 显示环境）：", type(e).__name__, e)
        print("请改用命令行模式：python tdac_fill.py --dry-run / --check")
        return 2
    app = App(root)

    # 仅供自动化截图使用：--screenshot 路径 [--screenshot-tab N]
    if "--screenshot" in argv:
        idx = argv.index("--screenshot")
        path = argv[idx + 1] if idx + 1 < len(argv) else "tdac_gui.png"
        tab = 0
        if "--screenshot-tab" in argv:
            t = argv.index("--screenshot-tab")
            try:
                tab = int(argv[t + 1])
            except Exception:
                tab = 0
        try:
            app.nb.select(tab)
        except Exception:
            pass
        root.after(700, lambda: (_capture_then_exit(root, app, path)))
        root.mainloop()
        return 0

    root.mainloop()
    return 0


def _capture_then_exit(root: tk.Tk, app: "App", path: str) -> None:
    """把当前窗口截图保存为 PNG（开发/验收取证用；不涉及任何真实资料）。"""
    try:
        root.lift()
        root.attributes("-topmost", True)
        root.update_idletasks()
        root.update()
        x = root.winfo_rootx()
        y = root.winfo_rooty()
        w = root.winfo_width()
        h = root.winfo_height()
        # 外扩以包含标题栏；下沿仅留极小边距，避免把任务栏一并截入。
        _screenshot_region(x - 8, y - 34, w + 16, h + 8, path)
        print("截图已保存：", path)
    except Exception as e:
        print("截图失败：", type(e).__name__, e)
    finally:
        try:
            root.attributes("-topmost", False)
        except Exception:
            pass
        root.destroy()


def _screenshot_region(x: int, y: int, w: int, h: int, path: str) -> None:
    """用 PowerShell + System.Drawing 截取屏幕指定区域（仅本机取证用）。"""
    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    ps = (
        "Add-Type -AssemblyName System.Drawing;"
        f"$b = New-Object System.Drawing.Bitmap({int(w)},{int(h)});"
        "$g = [System.Drawing.Graphics]::FromImage($b);"
        f"$g.CopyFromScreen({int(x)},{int(y)},0,0,$b.Size);"
        f"$b.Save('{abs_path}', [System.Drawing.Imaging.ImageFormat]::Png);"
        "$g.Dispose();$b.Dispose()"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, timeout=60)
    if r.returncode != 0 or not os.path.exists(abs_path):
        err = (r.stderr or b"").decode("utf-8", errors="replace")
        raise RuntimeError(f"截图失败：{err.strip()[:200]}")


if __name__ == "__main__":
    sys.exit(main())
