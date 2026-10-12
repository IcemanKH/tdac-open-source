import json
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("TDAC_DATA_DIR", os.path.join(tempfile.gettempdir(), "tdac-tests-data"))

try:
    import tkinter as tk
except Exception:                                   # pragma: no cover
    tk = None

try:
    import tdac_draft
    import tdac_gate
    import tdac_gui
    import tdac_paths
    import tdac_privacy
    import tdac_sec
    _IMPORT_ERR = None
except Exception as e:                              # pragma: no cover
    tdac_gui = None
    _IMPORT_ERR = e


def _can_open_window() -> bool:
    if tk is None or tdac_gui is None:
        return False
    try:
        r = tk.Tk()
        r.withdraw()
        r.destroy()
        return True
    except Exception:
        return False


CAN_GUI = _can_open_window()
SKIP_REASON = ("需要可用的 tkinter 显示环境："
               "请用便携版或独立构建环境（.build\\venv）运行本套测试。")


class _TmpDataDir:
    def __init__(self, root):
        self.root = root

    def __enter__(self):
        self._old = os.environ.get("TDAC_DATA_DIR")
        os.environ["TDAC_DATA_DIR"] = self.root
        return self.root

    def __exit__(self, *exc):
        if self._old is None:
            os.environ.pop("TDAC_DATA_DIR", None)
        else:
            os.environ["TDAC_DATA_DIR"] = self._old


def _seed(root: str) -> None:
    os.makedirs(os.path.join(root, "config"), exist_ok=True)
    with open(os.path.join(root, "config", "profile.json"), "w", encoding="utf-8") as fh:
        json.dump({"familyName": "TESTFAMILY", "firstName": "TESTGIVEN",
                   "nationality": "CHINESE", "dateOfBirth": "1990/01/01",
                   "gender": "MALE", "occupation": "EMPLOYEE",
                   "countryOfResidence": "CHINA", "cityOfResidence": "TESTCITY",
                   "phoneCode": "86", "visaNo": "", "email": "tester@example.invalid"}, fh)
    with open(os.path.join(root, "config", "trip.json"), "w", encoding="utf-8") as fh:
        json.dump({"arrivalDate": "2026/01/01", "departureDate": "2026/01/08",
                   "countryOfBoarding": "CHINA", "purposeOfTravel": "HOLIDAY",
                   "arrivalMode": "AIR", "arrivalFlightType": "COMMERCIAL FLIGHT",
                   "arrivalFlightNo": "MU0000", "departureMode": "AIR",
                   "departureFlightType": "COMMERCIAL FLIGHT",
                   "departureFlightNo": "MU0001", "accommodationType": "HOTEL",
                   "accommodationProvince": "TESTPROVINCE",
                   "accommodationDistrict": "TESTDISTRICT",
                   "accommodationSubdistrict": "TESTSUBDISTRICT",
                   "accommodationPostalCode": "00000",
                   "accommodationAddress": "1 TEST ROAD"}, fh)
    with open(os.path.join(root, "config", "dynamic.json"), "w", encoding="utf-8") as fh:
        json.dump({"last14DaysCountries": ["CHINA"], "last14DaysConfirmed": True,
                   "healthDeclarationConfirmed": True,
                   "yellowFeverVaccination": "NO", "symptoms": "NO",
                   "receiverEmail": "tester@example.invalid"}, fh)


@unittest.skipUnless(CAN_GUI, SKIP_REASON)
class GuiConfigConsistencyTests(unittest.TestCase):
    """GUI 显示值必须与底层配置一致。"""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self._tmp = tempfile.TemporaryDirectory()
        self._env = _TmpDataDir(self._tmp.name)
        self._env.__enter__()
        _seed(self._tmp.name)
        tdac_sec.save_secrets({"passportNo": "E00000000", "phoneNo": "13000000000"})
        self.app = tdac_gui.App(self.root)

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        # 及时回收 Tk 对象，避免解释器退出时 Tcl 异步处理器被错误线程回收而中断进程。
        import gc
        gc.collect()
        self._env.__exit__(None, None, None)
        self._tmp.cleanup()

    def test_forms_reflect_config_values(self):
        self.assertEqual(self.app.profile_form.widgets["familyName"].get(), "TESTFAMILY")
        self.assertEqual(self.app.profile_form.widgets["cityOfResidence"].get(), "TESTCITY")
        self.assertEqual(self.app.trip_form.widgets["arrivalFlightNo"].get(), "MU0000")

    def test_gui_matches_underlying_loader(self):
        path = os.path.join(tdac_paths.config_dir(), "profile.json")
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertEqual(self.app.profile_form.widgets["email"].get(), cfg["email"])

    def test_collected_profile_matches_saved_secrets(self):
        collected = self.app._collect_profile()
        self.assertEqual(collected["passportNo"], "E00000000")
        self.assertEqual(collected["phoneNo"], "13000000000")

    def test_collect_profile_covers_every_defined_field(self):
        collected = self.app._collect_profile()
        for key, *_rest in tdac_gui.PROFILE_FIELDS:
            self.assertIn(key, collected)
        for key, _label in tdac_gui.SECRET_FIELDS:
            self.assertIn(key, collected)


@unittest.skipUnless(CAN_GUI, SKIP_REASON)
class SensitiveMaskingTests(unittest.TestCase):
    """敏感字段默认遮挡；只有用户主动点击“显示”才揭示。"""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self._tmp = tempfile.TemporaryDirectory()
        self._env = _TmpDataDir(self._tmp.name)
        self._env.__enter__()
        _seed(self._tmp.name)
        tdac_sec.save_secrets({"passportNo": "E00000000", "phoneNo": "13000000000"})
        self.app = tdac_gui.App(self.root)

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        # 及时回收 Tk 对象，避免解释器退出时 Tcl 异步处理器被错误线程回收而中断进程。
        import gc
        gc.collect()
        self._env.__exit__(None, None, None)
        self._tmp.cleanup()

    def test_secret_entries_start_masked(self):
        self.assertEqual(self.app.secret_widgets["passportNo"].entry.cget("show"), "*")
        self.assertEqual(self.app.secret_widgets["phoneNo"].entry.cget("show"), "*")
        self.assertEqual(self.app.profile_form.widgets["email"].entry.cget("show"), "*")

    def test_toggle_reveals_and_hides_again(self):
        w = self.app.secret_widgets["passportNo"]
        w.toggle()
        self.assertEqual(w.entry.cget("show"), "")
        self.assertEqual(w.btn.cget("text"), "隐藏")
        w.toggle()
        self.assertEqual(w.entry.cget("show"), "*")

    def test_confirm_dialog_masks_until_user_clicks_show(self):
        profile = self.app._collect_profile()
        dlg = tdac_gui.CriticalConfirmDialog(self.root, profile)
        try:
            row = dlg._rows["passportNo"]
            self.assertEqual(row["value_lbl"].cget("text"), tdac_privacy.mask("E00000000"))
            # 用户主动点击“显示”按钮
            row["show_btn"].invoke()
            self.assertEqual(row["value_lbl"].cget("text"), "E00000000")
        finally:
            dlg.destroy()

    def test_shared_confirm_logic_never_auto_reveals(self):
        """回归保护：确认流程本身绝不自动揭示完整值（B1 同步修正）。"""
        profile = self.app._collect_profile()
        dlg = tdac_gui.CriticalConfirmDialog(self.root, profile)
        try:
            for f in dlg._rows:
                dlg._rows[f]["var"].set(True)
            dlg.accepted = True
            ok, unconfirmed = tdac_privacy.confirm_critical_fields(
                dlg.profile, ask=dlg.ask, reveal=dlg.reveal)
            self.assertTrue(ok, unconfirmed)
            for field, row in dlg._rows.items():
                self.assertEqual(row["value_lbl"].cget("text"),
                                 tdac_privacy.mask(dlg.profile.get(field, "")),
                                 "未经用户点击，不得展示完整敏感值")
        finally:
            dlg.destroy()

    def test_dialog_ask_is_false_before_accept(self):
        profile = self.app._collect_profile()
        dlg = tdac_gui.CriticalConfirmDialog(self.root, profile)
        try:
            for f in dlg._rows:
                dlg._rows[f]["var"].set(True)
            self.assertFalse(dlg.ask("请确认护照号 [xx] 正确 (y/N): "))
        finally:
            dlg.destroy()

    def test_dialog_declares_two_arg_ask_so_reveal_is_wired(self):
        self.assertTrue(tdac_privacy.reveal_is_user_driven(
            tdac_gui.CriticalConfirmDialog.ask))


@unittest.skipUnless(CAN_GUI, SKIP_REASON)
class DraftGuiTests(unittest.TestCase):
    """草稿暂存 / 自动恢复 / 未保存提醒 / 官网预填门禁（全部离线，不联网）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = _TmpDataDir(self._tmp.name)
        self._env.__enter__()
        _seed(self._tmp.name)
        tdac_sec.save_secrets({"passportNo": "E00000000", "phoneNo": "13000000000"})
        self._roots = []
        self.app = self._new_app()

    def tearDown(self):
        for root in self._roots:
            try:
                root.destroy()
            except Exception:
                pass
        self._roots = []
        self.app = None
        # 显式回收 Tk 对象：Windows 下若 Tcl 解释器在退出时被别的时机回收，
        # 会触发 "Tcl_AsyncDelete: async handler deleted by the wrong thread"
        # 直接中断整个测试进程（导致看不到汇总）。这里主动、及时回收。
        import gc
        gc.collect()
        self._env.__exit__(None, None, None)
        self._tmp.cleanup()

    def _new_app(self):
        root = tk.Tk()
        root.withdraw()
        self._roots.append(root)
        return tdac_gui.App(root)

    @staticmethod
    def _set(widget, value):
        widget.delete(0, "end")
        widget.insert(0, value)

    def _make_ready_for_prefill(self):
        """构造“资料完整 + 格式正确 + 关键字段已确认 + 处于申报窗口”的界面状态。"""
        import datetime as dt

        app = self.app
        trip_w = app.trip_form.widgets
        arrival = dt.date.today() + dt.timedelta(days=1)
        self._set(trip_w["arrivalDate"], arrival.strftime("%Y/%m/%d"))
        self._set(trip_w["departureDate"],
                  (arrival + dt.timedelta(days=7)).strftime("%Y/%m/%d"))

        # 模拟「校验并保存个人资料」成功后的落盘证据（时间 + 不可逆指纹）
        profile = app._collect_profile()
        stored = {k: v for k, v in profile.items() if k not in tdac_sec.SECRET_KEYS}
        stored["criticalConfirmedAt"] = "2026-10-10 10:00:00"
        stored["criticalFingerprint"] = tdac_privacy.critical_fingerprint(profile)
        app._save_json("profile.json", stored)
        app.profile.update(stored)
        app._refresh_critical_confirmed()

        # 健康声明由本人在本次使用中确认
        app.var_days_confirmed.set(True)
        app.var_health_confirmed.set(True)
        app.save_health()
        app._mark_clean()

    # --- 暂存：不完整也允许 ---------------------------------------------
    def test_save_draft_allows_incomplete_data(self):
        self._set(self.app.profile_form.widgets["familyName"], "")
        self._set(self.app.profile_form.widgets["cityOfResidence"], "")
        self.assertTrue(self.app.save_draft_now(announce=False))
        self.assertTrue(tdac_draft.draft_exists())
        self.assertEqual(tdac_draft.read_draft_file()["profile"].get("familyName"), "")
        self.assertIn("草稿", self.app.draft_state.cget("text"))

    def test_draft_never_contains_plaintext_secret(self):
        self.assertTrue(self.app.save_draft_now(announce=False))
        with open(tdac_draft.draft_path(), "rb") as fh:
            raw = fh.read()
        self.assertNotIn(b"E00000000", raw)
        self.assertNotIn(b"13000000000", raw)

    # --- 恢复：重启后自动恢复，健康声明保持未确认 -------------------------
    def test_restart_restores_draft_and_health_stays_unconfirmed(self):
        self._set(self.app.profile_form.widgets["cityOfResidence"], "DRAFTONLY")
        self._set(self.app.trip_form.widgets["arrivalFlightNo"], "MU9999")
        self.app.var_days_confirmed.set(True)
        self.app.var_health_confirmed.set(True)
        self.assertTrue(self.app.save_draft_now(announce=False))

        app2 = self._new_app()                      # 模拟“下次打开软件”
        self.assertTrue(app2.draft_loaded)
        self.assertEqual(app2.profile_form.widgets["cityOfResidence"].get(), "DRAFTONLY")
        self.assertEqual(app2.trip_form.widgets["arrivalFlightNo"].get(), "MU9999")
        self.assertFalse(app2.var_days_confirmed.get(), "草稿不得继承健康确认标记")
        self.assertFalse(app2.var_health_confirmed.get())
        self.assertFalse(app2._health_reconfirmed)
        self.assertFalse(app2._is_dirty(), "刚恢复草稿不应被当成“有未保存修改”")

    def test_discard_draft_returns_to_formal_config(self):
        self._set(self.app.profile_form.widgets["cityOfResidence"], "DRAFTONLY")
        self.assertTrue(self.app.save_draft_now(announce=False))
        with mock.patch.object(tdac_gui.messagebox, "askyesno", return_value=True):
            self.app.discard_draft()
        self.assertFalse(tdac_draft.draft_exists())
        self.assertEqual(self.app.profile_form.widgets["cityOfResidence"].get(), "TESTCITY")

    # --- 未保存修改提醒 ---------------------------------------------------
    def test_close_with_unsaved_changes_prompts_and_can_stay(self):
        self._set(self.app.profile_form.widgets["familyName"], "CHANGED")
        self.assertTrue(self.app._is_dirty())
        with mock.patch.object(self.app.root, "destroy") as destroy, \
                mock.patch.object(tdac_gui.messagebox, "askyesnocancel", return_value=None) as ask:
            self.app.on_close()
            self.assertTrue(ask.called, "有未保存修改时必须提醒")
        self.assertFalse(destroy.called, "选择“取消”应留在界面")

    def test_close_with_unsaved_changes_can_save_draft_then_exit(self):
        self._set(self.app.profile_form.widgets["familyName"], "CHANGED")
        with mock.patch.object(self.app.root, "destroy") as destroy, \
                mock.patch.object(tdac_gui.messagebox, "askyesnocancel", return_value=True):
            self.app.on_close()
        self.assertTrue(destroy.called)
        self.assertTrue(tdac_draft.draft_exists(), "选“是”应先暂存草稿再退出")
        self.assertEqual(tdac_draft.read_draft_file()["profile"]["familyName"], "CHANGED")

    def test_close_with_unsaved_changes_can_discard(self):
        self._set(self.app.profile_form.widgets["familyName"], "CHANGED")
        with mock.patch.object(self.app.root, "destroy") as destroy, \
                mock.patch.object(tdac_gui.messagebox, "askyesnocancel", return_value=False):
            self.app.on_close()
        self.assertTrue(destroy.called)
        self.assertFalse(tdac_draft.draft_exists())

    def test_close_when_clean_exits_without_prompt(self):
        with mock.patch.object(self.app.root, "destroy") as destroy, \
                mock.patch.object(tdac_gui.messagebox, "askyesnocancel") as ask:
            self.app.on_close()
            self.assertFalse(ask.called, "没有修改时不应打扰用户")
        self.assertTrue(destroy.called)

    # --- 官网预填门禁：不完整即禁止 ---------------------------------------
    def test_incomplete_data_blocks_official_prefill(self):
        self._set(self.app.profile_form.widgets["familyName"], "")
        started = []
        with mock.patch.object(self.app, "_start_run",
                               side_effect=lambda *a, **k: started.append(a)), \
                mock.patch.object(tdac_gui.messagebox, "askyesno", return_value=True), \
                mock.patch.object(tdac_gui.messagebox, "showerror") as err:
            self.app.run_real()
        self.assertEqual(started, [], "资料不完整不得打开官网")
        self.assertTrue(err.called)
        self.assertIn("阻止", self.app.status.cget("text"))

    def test_current_confirmation_dialog_is_required_even_without_old_record(self):
        """旧 profile.json 确认记录缺失不应拦截补确认，但取消本次对话必须停止。"""
        self._make_ready_for_prefill()
        stored = self.app._saved_config("profile.json")
        stored.pop("criticalConfirmedAt", None)
        stored.pop("criticalFingerprint", None)
        self.app._save_json("profile.json", stored)
        self.app._refresh_critical_confirmed()
        started = []
        dialog = mock.Mock(accepted=False, profile=self.app._collect_profile())
        with mock.patch.object(self.app, "_start_run",
                               side_effect=lambda *a, **k: started.append(a)), \
                mock.patch.object(self.app.root, "wait_window"), \
                mock.patch.object(tdac_gui, "CriticalConfirmDialog", return_value=dialog) as created, \
                mock.patch.object(tdac_gui.messagebox, "askyesno", return_value=True), \
                mock.patch.object(tdac_gui.messagebox, "showerror"):
            self.app.run_real()
        self.assertEqual(started, [])
        self.assertTrue(created.called, "每次正式预填都必须展示本次关键字段确认")

    def test_editing_critical_field_invalidates_confirmation(self):
        self._make_ready_for_prefill()
        self.assertEqual(self.app._prefill_blockers(), [])
        profile, trip, dynamic = self.app._collect_all()
        self.assertTrue(tdac_gate.authorize_current_prefill(profile, dynamic, trip))
        self.app.secret_widgets["passportNo"].set("E99999999")
        changed, changed_trip, changed_dynamic = self.app._collect_all()
        self.assertFalse(tdac_gate.consume_current_prefill_authorization(
            changed, changed_dynamic, changed_trip),
                         "修改护照号后不得复用本次操作授权")

    def test_health_reconfirmation_required_after_draft_restore(self):
        self._make_ready_for_prefill()
        self.assertTrue(self.app.save_draft_now(announce=False))
        app2 = self._new_app()
        self.assertTrue(app2.draft_loaded)
        _profile, _trip, dynamic = app2._collect_all()
        self.assertFalse(tdac_gate.health_confirmed(dynamic),
                         "草稿恢复后的健康确认标记不得作为本次预填授权")

    # --- 全部门条件满足 → 允许开始官网预填 ---------------------------------
    def test_prefill_starts_when_all_conditions_met(self):
        self._make_ready_for_prefill()
        self.assertEqual(self.app._prefill_blockers(), [])
        started = []
        dialog = mock.Mock(accepted=True, profile=self.app._collect_profile())
        dialog.ask.return_value = True
        with mock.patch.object(self.app, "_start_run",
                               side_effect=lambda *a, **k: started.append(a)), \
                mock.patch.object(self.app.root, "wait_window"), \
                mock.patch.object(tdac_gui, "CriticalConfirmDialog", return_value=dialog), \
                mock.patch.object(tdac_gui.messagebox, "askyesno", return_value=True), \
                mock.patch.object(tdac_gui.messagebox, "showerror") as err:
            self.app.run_real()
        self.assertEqual(err.call_count, 0)
        # G1 最终修复：界面**不再**向命令行传任何“跳过确认”的开关。
        # 关键字段确认以 profile.json 的确认时间 + 不可逆指纹为准，命令行侧会用
        # 同一套证据（tdac_gate）重新校验，公共参数无法绕过。
        self.assertEqual(started[0][0], [])
        # 界面内容已落盘为正式配置（命令行流程读取的就是磁盘配置，且不含草稿内容）
        with open(os.path.join(tdac_paths.config_dir(), "trip.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["arrivalFlightNo"], "MU0000")
        self.assertEqual(tdac_sec.load_secrets()["passportNo"], "E00000000")
        self.assertTrue(self.app._saved_config("profile.json").get("criticalFingerprint"))

    # --- 一致性核验（G1 验收复核新增）--------------------------------------
    def test_stale_draft_is_not_restored_and_formal_wins(self):
        """草稿之后又重新保存了正式资料 → 重启保留正式资料，不恢复旧草稿。"""
        self._set(self.app.profile_form.widgets["cityOfResidence"], "DRAFT_CITY")
        self.assertTrue(self.app.save_draft_now(announce=False))
        # 把草稿时间改早：模拟“先暂存草稿，随后更新了正式资料”
        with open(tdac_draft.draft_path(), encoding="utf-8") as fh:
            info = json.load(fh)
        info["savedAt"] = "2020-01-01 00:00:00"
        info["savedAtEpoch"] = 1577836800.0
        with open(tdac_draft.draft_path(), "w", encoding="utf-8") as fh:
            json.dump(info, fh, ensure_ascii=False)

        with mock.patch.object(tdac_gui.messagebox, "showwarning") as warn:
            app2 = self._new_app()
        self.assertFalse(app2.draft_loaded)
        self.assertTrue(app2.draft_stale)
        self.assertEqual(app2.profile_form.widgets["cityOfResidence"].get(), "TESTCITY")
        self.assertTrue(warn.called, "旧草稿未恢复时必须明确提示")
        self.assertTrue(tdac_draft.draft_exists(), "提示不等于删除，草稿文件须保留")
        self.assertIn("更旧", app2.draft_state.cget("text"))

    def test_corrupt_draft_file_is_reported_and_kept(self):
        os.makedirs(tdac_paths.config_dir(), exist_ok=True)
        with open(tdac_draft.draft_path(), "w", encoding="utf-8") as fh:
            fh.write("{ broken json ")
        with mock.patch.object(tdac_gui.messagebox, "showwarning") as warn:
            app2 = self._new_app()
        self.assertFalse(app2.draft_loaded)
        self.assertTrue(app2.draft_corrupt)
        self.assertTrue(warn.called, "草稿损坏必须明确提示")
        self.assertIn("无法解析", app2.draft_state.cget("text"))
        self.assertTrue(os.path.exists(tdac_draft.draft_path()), "损坏文件不得被静默覆盖")

    def test_missing_draft_ciphertext_is_reported_and_kept(self):
        self.assertTrue(self.app.save_draft_now(announce=False))
        os.remove(tdac_sec.draft_secrets_path())
        with mock.patch.object(tdac_gui.messagebox, "showwarning") as warn:
            app2 = self._new_app()
        self.assertFalse(app2.draft_loaded)
        self.assertTrue(warn.called, "密文缺失必须明确提示")
        self.assertTrue(tdac_draft.draft_exists(), "草稿文件须保留待用户处理")

    def test_unchecking_health_confirmation_reblocks_prefill(self):
        """取消健康确认后，启动正式预填前不得建立当次授权。"""
        self._make_ready_for_prefill()
        self.assertEqual(self.app._prefill_blockers(), [])
        self.app.var_health_confirmed.set(False)
        profile, trip, dynamic = self.app._collect_all()
        self.assertFalse(tdac_gate.health_confirmed(dynamic))
        self.assertFalse(tdac_gate.authorize_current_prefill(profile, dynamic, trip))

    def test_clearing_health_countries_reblocks_prefill(self):
        self._make_ready_for_prefill()
        self.assertEqual(self.app._prefill_blockers(), [])
        self._set(self.app.dyn_form.widgets["last14DaysCountries"], "")
        self.assertTrue(any("14 天" in b for b in self.app._prefill_blockers()),
                        self.app._prefill_blockers())

    def test_run_real_renders_blockers_grouped(self):
        self._set(self.app.profile_form.widgets["familyName"], "")
        started = []
        with mock.patch.object(self.app, "_start_run",
                               side_effect=lambda *a, **k: started.append(a)), \
                mock.patch.object(tdac_gui.messagebox, "showerror") as err:
            self.app.run_real()
        self.assertEqual(started, [])
        message = err.call_args[0][1]
        self.assertIn("【个人资料】", message)
        self.assertIn("【行程住宿】", message)

    def test_discard_confirm_explains_scope(self):
        self.assertTrue(self.app.save_draft_now(announce=False))
        seen = {}

        def fake_yes(title, message, *a, **k):
            seen["title"], seen["message"] = title, message
            return False

        with mock.patch.object(tdac_gui.messagebox, "askyesno", side_effect=fake_yes):
            self.app.discard_draft()
        self.assertIn("draft.json", seen["message"])
        self.assertIn("draft-secrets.dat", seen["message"])
        self.assertIn("不会", seen["message"])
        self.assertTrue(tdac_draft.draft_exists(), "选择取消时不得删除草稿")

    # --- 三种状态彼此独立 -------------------------------------------------
    def test_draft_prefill_and_submission_states_are_distinct(self):
        self.assertTrue(self.app.save_draft_now(announce=False))
        self.assertIn("草稿", self.app.draft_state.cget("text"))
        self.assertIn("正式申报状态", self.app.submit_state.cget("text"))
        self.assertNotIn("预填完成", self.app.banner.cget("text"))
        self.app._last_output = "预览阶段：PASS"
        self.app._on_done(0)
        self.assertIn("预填完成", self.app.banner.cget("text"))
        self.assertNotIn("提交", self.app.draft_state.cget("text"))
        self.assertIn("未确认", self.app.submit_state.cget("text"))


if __name__ == "__main__":
    unittest.main()
