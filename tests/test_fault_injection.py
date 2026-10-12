# -*- coding: utf-8 -*-
"""故障注入测试（G1 最终修复）。

覆盖 Codex 独立审计提出的四类问题：

  * P1-01 草稿 JSON 与 DPAPI 密文**非原子提交** → 中断 / 崩溃后不得拼接不同版本；
  * P1-02 密文解密失败不得把旧资料当空对象，也不得覆盖 / 删除旧密文；
  * P1-03 ``--skip-confirm`` 等公开开关不得绕过正式预填确认；
  * P2    写入路径统一保护、ACL 加固失败可查。

全部离线：不启动浏览器、不联网、不使用任何真实资料。
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("TDAC_DATA_DIR", os.path.join(tempfile.gettempdir(), "tdac-tests-data"))

import tdac_draft  # noqa: E402
import tdac_fill  # noqa: E402
import tdac_gate  # noqa: E402
import tdac_paths  # noqa: E402
import tdac_privacy  # noqa: E402
import tdac_sec  # noqa: E402
import tdac_validate  # noqa: E402

PASSPORT = "E00000000"
PHONE = "13000000000"
EMAIL = "tester@example.invalid"


def _full_profile(passport=PASSPORT, phone=PHONE):
    return {
        "familyName": "TESTFAMILY", "firstName": "TESTGIVEN", "middleName": "",
        "nationality": "CHINESE", "dateOfBirth": "1990/01/01", "gender": "MALE",
        "occupation": "EMPLOYEE", "countryOfResidence": "CHINA",
        "cityOfResidence": "TESTCITY", "phoneCode": "86", "visaNo": "",
        "email": EMAIL, "passportNo": passport, "phoneNo": phone,
    }


def _full_trip(days_ahead=1):
    import datetime as dt
    arrival = dt.date.today() + dt.timedelta(days=days_ahead)
    departure = arrival + dt.timedelta(days=7)
    return {
        "arrivalDate": arrival.strftime("%Y/%m/%d"),
        "departureDate": departure.strftime("%Y/%m/%d"),
        "countryOfBoarding": "CHINA", "purposeOfTravel": "HOLIDAY",
        "arrivalMode": "AIR", "arrivalFlightType": "COMMERCIAL FLIGHT",
        "arrivalFlightNo": "MU0000", "departureMode": "AIR",
        "departureFlightType": "COMMERCIAL FLIGHT", "departureFlightNo": "MU0001",
        "accommodationType": "HOTEL", "accommodationProvince": "TESTPROVINCE",
        "accommodationDistrict": "TESTDISTRICT",
        "accommodationSubdistrict": "TESTSUBDISTRICT",
        "accommodationPostalCode": "00000", "accommodationAddress": "1 TEST ROAD",
    }


def _full_dynamic(confirmed=True):
    return {
        "last14DaysCountries": ["CHINA"],
        "last14DaysConfirmed": confirmed,
        "healthDeclarationConfirmed": confirmed,
        "yellowFeverVaccination": "NO", "symptoms": "NO",
        "receiverEmail": EMAIL,
    }


class _FakeTtyStdin(io.StringIO):
    """最小交互式终端替身。

    CLI 的确认分支取决于 ``sys.stdin.isatty()``：真实终端里走人工确认、被重定向
    时走“非交互终端”分支。测试若依赖运行环境是否带终端，用例就会时真时假，
    因此这里显式给出一个稳定的 ``isatty() is True`` 的 stdin 替身。
    """

    def isatty(self):
        return True


class _DataDirCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("TDAC_DATA_DIR")
        os.environ["TDAC_DATA_DIR"] = self._tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("TDAC_DATA_DIR", None)
        else:
            os.environ["TDAC_DATA_DIR"] = self._old
        self._tmp.cleanup()


# --------------------------------------------------------------------------- #
# P1-01 原子提交：中断 / 崩溃不得拼接不同版本
# --------------------------------------------------------------------------- #
class AtomicCommitTests(_DataDirCase):

    def _save_v1(self):
        tdac_draft.save_draft(_full_profile(), _full_trip(), _full_dynamic(),
                              secrets={"passportNo": PASSPORT, "phoneNo": PHONE})

    def test_successful_save_leaves_no_transient_files(self):
        """正常保存后不得残留暂存 / prev / 事务标记文件。"""
        self._save_v1()
        for path in (tdac_draft.staged_json_path(), tdac_draft.prev_json_path(),
                     tdac_draft.commit_marker_path(), tdac_draft.staged_secrets_path(),
                     tdac_draft.prev_secrets_path()):
            self.assertFalse(os.path.exists(path), f"残留事务文件：{path}")
        self.assertEqual(tdac_draft.recover_draft_transaction(), tdac_draft.RECOVER_NONE)

    def test_json_and_cipher_share_one_generation(self):
        """两份文件必须带同一个代际标记。"""
        self._save_v1()
        info = tdac_draft.read_draft_file()
        cipher = tdac_sec.load_draft_secrets_raw()
        self.assertTrue(info.get("generation"))
        self.assertEqual(info["generation"], cipher["generation"])

    def test_mismatched_generation_is_refused_not_stitched(self):
        """旧 JSON + 新密文：必须报错拒绝恢复，绝不把两个版本拼在一起。"""
        self._save_v1()
        tdac_sec.save_draft_secrets({"passportNo": "E11111111", "phoneNo": ""},
                                    generation="deadbeefdeadbeef")
        with self.assertRaises(tdac_draft.DraftGenerationMismatch) as ctx:
            tdac_draft.load_draft()
        message = str(ctx.exception)
        self.assertNotIn(PASSPORT, message)
        self.assertNotIn("E11111111", message)
        # 两份文件保持原样（不得被“顺手修复”）
        self.assertTrue(os.path.exists(tdac_draft.draft_path()))
        self.assertTrue(os.path.exists(tdac_sec.draft_secrets_path()))

    def test_missing_cipher_generation_is_refused_not_stitched(self):
        """新 JSON 不能与没有代际标记的旧密文拼接。"""
        self._save_v1()
        info = tdac_draft.read_draft_file()
        info["generation"] = "00112233445566aa"
        info["profile"]["familyName"] = "NEWGENERATION"
        tdac_paths.atomic_write_json(tdac_draft.draft_path(), info)
        tdac_sec.save_draft_secrets({"passportNo": PASSPORT, "phoneNo": PHONE}, generation="")
        with self.assertRaises(tdac_draft.DraftGenerationMismatch):
            tdac_draft.load_draft()
        self.assertTrue(os.path.exists(tdac_draft.draft_path()))
        self.assertTrue(os.path.exists(tdac_sec.draft_secrets_path()))

    def _craft_interrupted_commit(self, gen2="00112233445566aa"):
        """构造“提交进行到一半就崩溃”的磁盘状态。

        状态：事务标记已写、旧版已转 prev、**新版密文已安装**、但新版 JSON 尚未安装。
        这正是“两个独立 os.replace”最容易把资料拼接起来的位置。
        """
        self._save_v1()
        tdac_sec.save_draft_secrets({"passportNo": "E11111111", "phoneNo": ""},
                                    generation=gen2, path=tdac_draft.staged_secrets_path())
        tdac_paths.atomic_write_json(tdac_draft.commit_marker_path(), {
            "generation": gen2, "hadJson": True, "hadSecrets": True})
        os.replace(tdac_draft.draft_path(), tdac_draft.prev_json_path())
        os.replace(tdac_sec.draft_secrets_path(), tdac_draft.prev_secrets_path())
        os.replace(tdac_draft.staged_secrets_path(), tdac_sec.draft_secrets_path())

    def test_interrupted_commit_never_returns_mixed_content(self):
        self._craft_interrupted_commit()
        # 中断状态下 JSON 缺失 → 不返回任何内容（更不会返回“旧 JSON + 新护照号”）
        self.assertEqual(tdac_draft.load_draft(), {})

    def test_interrupted_commit_is_rolled_back_to_previous_version(self):
        self._craft_interrupted_commit()
        self.assertEqual(tdac_draft.recover_draft_transaction(), tdac_draft.RECOVER_ROLLED_BACK)
        draft = tdac_draft.load_draft()
        self.assertEqual(draft["profile"]["familyName"], "TESTFAMILY")
        self.assertEqual(draft["secrets"]["passportNo"], PASSPORT, "必须回到上一版护照号")
        self.assertEqual(draft["generation"], tdac_sec.load_draft_secrets_raw()["generation"])
        for path in (tdac_draft.staged_json_path(), tdac_draft.prev_json_path(),
                     tdac_draft.commit_marker_path(), tdac_draft.prev_secrets_path()):
            self.assertFalse(os.path.exists(path), f"回滚后不得残留：{path}")

    def test_interrupted_commit_where_files_already_installed_is_finalized(self):
        """两份文件都已装成新代际 → 提交其实已完成，只需补做收尾。"""
        self._save_v1()
        self._craft_interrupted_commit()
        os.replace(tdac_draft.staged_json_path() if os.path.exists(tdac_draft.staged_json_path())
                   else tdac_draft.prev_json_path(), tdac_draft.draft_path())
        # 现在让 JSON 也带上 gen2 代际（模拟“JSON 也装完了才崩溃”）
        with open(tdac_draft.draft_path(), encoding="utf-8") as fh:
            info = json.load(fh)
        info["generation"] = "00112233445566aa"
        tdac_paths.atomic_write_json(tdac_draft.draft_path(), info)
        self.assertEqual(tdac_draft.recover_draft_transaction(), tdac_draft.RECOVER_FINALIZED)
        self.assertFalse(os.path.exists(tdac_draft.commit_marker_path()))

    def test_replace_failure_keeps_previous_draft_intact(self):
        """替换失败（磁盘错误 / 文件被占用）时，旧草稿必须保持可用且一致。"""
        self._save_v1()
        real_replace = os.replace
        calls = {"n": 0}

        def flaky_replace(src, dst):
            calls["n"] += 1
            # 第 3 次替换（安装新版密文）时抛错，模拟写入中断
            if calls["n"] == 3:
                raise OSError("模拟替换失败")
            return real_replace(src, dst)

        with mock.patch.object(tdac_draft.os, "replace", side_effect=flaky_replace):
            with self.assertRaises(OSError):
                tdac_draft.save_draft(_full_profile(), _full_trip(), _full_dynamic(),
                                      secrets={"passportNo": "E22222222", "phoneNo": ""})
        # 恢复后必须仍是一份**一致**的旧草稿
        tdac_draft.recover_draft_transaction()
        draft = tdac_draft.load_draft()
        self.assertEqual(draft["profile"]["familyName"], "TESTFAMILY")
        self.assertEqual(draft["secrets"]["passportNo"], PASSPORT)
        self.assertNotIn("E22222222", json.dumps(draft, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# P1-02 解密失败：停止保存，绝不覆盖 / 删除旧资料
# --------------------------------------------------------------------------- #
class DecryptFailureTests(_DataDirCase):

    def test_draft_save_stops_when_existing_cipher_is_unreadable(self):
        tdac_draft.save_draft(_full_profile(), _full_trip(), _full_dynamic(),
                              secrets={"passportNo": PASSPORT, "phoneNo": PHONE})
        cipher_path = tdac_sec.draft_secrets_path()
        with open(cipher_path, "rb") as fh:
            before_cipher = fh.read()
        with open(tdac_draft.draft_path(), "rb") as fh:
            before_json = fh.read()

        with open(cipher_path, "wb") as fh:
            fh.write(b"not a valid dpapi blob")
        with open(cipher_path, "rb") as fh:
            broken = fh.read()

        with self.assertRaises(tdac_draft.DraftSecretsUnavailable):
            tdac_draft.save_draft(_full_profile(), _full_trip(), _full_dynamic(),
                                  secrets={"passportNo": "E33333333", "phoneNo": ""})
        # 旧密文原样保留（既没被新内容覆盖，也没被删掉）
        with open(cipher_path, "rb") as fh:
            self.assertEqual(fh.read(), broken)
        with open(tdac_draft.draft_path(), "rb") as fh:
            self.assertEqual(fh.read(), before_json)

    def test_formal_secrets_save_stops_when_unreadable(self):
        """正式密文解密失败时，save_secrets 必须中止，不得把旧资料当空对象写回。"""
        tdac_sec.save_secrets({"passportNo": PASSPORT, "phoneNo": PHONE})
        path = tdac_sec.secrets_path()
        with open(path, "wb") as fh:
            fh.write(b"not a valid dpapi blob")
        with open(path, "rb") as fh:
            broken = fh.read()
        with self.assertRaises(Exception):
            tdac_sec.save_secrets({"passportNo": "E44444444"})
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), broken, "解密失败时不得覆盖旧密文")
        self.assertFalse(tdac_sec.clear_draft_secrets.__doc__ is None)  # 模块可用

    def test_partial_field_update_does_not_blank_other_field(self):
        """只更新一个字段时，另一个字段必须保留（旧资料不能被当空对象）。"""
        tdac_sec.save_secrets({"passportNo": PASSPORT, "phoneNo": PHONE})
        tdac_sec.save_secrets({"passportNo": "E55555555"})
        data = tdac_sec.load_secrets()
        self.assertEqual(data["passportNo"], "E55555555")
        self.assertEqual(data["phoneNo"], PHONE, "未提供的字段必须保留原值")


# --------------------------------------------------------------------------- #
# P1-03 统一门禁：公开开关不得绕过
# --------------------------------------------------------------------------- #
class UnifiedGateTests(_DataDirCase):

    def _seed_full_config(self, health_confirmed=True):
        os.makedirs(tdac_paths.config_dir(), exist_ok=True)
        tdac_paths.atomic_write_json(os.path.join(tdac_paths.config_dir(), "profile.json"),
                                     _full_profile())
        tdac_paths.atomic_write_json(os.path.join(tdac_paths.config_dir(), "trip.json"),
                                     _full_trip())
        tdac_paths.atomic_write_json(
            os.path.join(tdac_paths.config_dir(), "dynamic.json"),
            _full_dynamic(confirmed=health_confirmed))
        tdac_sec.save_secrets({"passportNo": PASSPORT, "phoneNo": PHONE})

    def test_skip_confirm_switch_no_longer_exists(self):
        """``--skip-confirm`` 必须彻底消失（公开参数不得用于绕过确认）。"""
        with mock.patch.object(sys, "argv", ["tdac_fill.py", "--skip-confirm"]):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                with self.assertRaises(SystemExit):
                    tdac_fill.main()
        self.assertIn("unrecognized", err.getvalue().lower())
        with open(os.path.join(os.path.dirname(os.path.abspath(tdac_fill.__file__)),
                               "tdac_fill.py"), encoding="utf-8") as fh:
            source = fh.read()
        # 不得再注册该参数，也不得有对应的属性判断（注释里说明“不再提供”是可以的）
        self.assertNotIn('add_argument("--skip-confirm"', source)
        self.assertNotIn('"--skip-confirm"', source)
        self.assertNotIn("args.skip_confirm", source)
        self.assertNotIn('"--skip_confirm"', source)
        with open(os.path.join(os.path.dirname(os.path.abspath(tdac_fill.__file__)),
                               "tdac_gui.py"), encoding="utf-8") as fh:
            gui_source = fh.read()
        self.assertNotIn('"--skip-confirm"', gui_source)

    def test_confirmation_evidence_drives_the_gate(self):
        self._seed_full_config()
        self.assertFalse(tdac_gate.confirmation_valid(_full_profile(), config_dir=tdac_paths.config_dir()))
        tdac_gate.record_critical_confirmation(_full_profile(), tdac_paths.config_dir())
        self.assertTrue(tdac_gate.confirmation_valid(_full_profile(), config_dir=tdac_paths.config_dir()))
        # 改一个关键字段 → 证据立即失效
        self.assertFalse(tdac_gate.confirmation_valid(
            _full_profile(passport="E99999999"), config_dir=tdac_paths.config_dir()))

    def test_persisted_confirmation_is_not_current_operation_authorization(self):
        """用户可写 profile.json 中的旧记录只能提示变更，不能放行本次预填。"""
        self._seed_full_config()
        tdac_gate.record_critical_confirmation(_full_profile(), tdac_paths.config_dir())
        groups = tdac_gate.prefill_blocker_groups(
            _full_profile(), _full_trip(), _full_dynamic(), config_dir=tdac_paths.config_dir())
        self.assertTrue(groups, "仅凭落盘记录不得授权正式预填")

    def test_current_operation_authorization_is_memory_only_and_one_shot(self):
        """当次授权必须由当前进程颁发，并且消费后不能重放。"""
        profile, dynamic, trip = _full_profile(), _full_dynamic(), _full_trip()
        self.assertFalse(tdac_gate.consume_current_prefill_authorization(profile, dynamic, trip))
        tdac_gate.authorize_current_prefill(profile, dynamic, trip)
        self.assertTrue(tdac_gate.consume_current_prefill_authorization(profile, dynamic, trip))
        self.assertFalse(tdac_gate.consume_current_prefill_authorization(profile, dynamic, trip))

    def test_trip_or_accommodation_change_invalidates_current_authorization(self):
        """确认后变更入境日期、航班或住宿，旧授权不得用于另一份预填资料。"""
        profile, dynamic, trip = _full_profile(), _full_dynamic(), _full_trip()
        self.assertTrue(tdac_gate.authorize_current_prefill(profile, dynamic, trip))
        changed = dict(trip, arrivalDate="2099/01/01", arrivalFlightNo="MU9999",
                       accommodationAddress="99 CHANGED ROAD")
        self.assertFalse(tdac_gate.consume_current_prefill_authorization(
            profile, dynamic, changed))

    def test_cli_blocks_before_browser_without_confirmation(self):
        """CLI：没有本人确认证据（且非交互终端）时，必须在打开浏览器之前中止。"""
        self._seed_full_config()
        launched = []
        with mock.patch.object(tdac_fill, "preflight_env", return_value=("chrome", 1234)), \
                mock.patch.object(tdac_fill, "launch_browser",
                                  side_effect=lambda *a, **k: launched.append(a)):
            with mock.patch.object(sys, "argv", ["tdac_fill.py"]):
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = tdac_fill.main()
        self.assertNotEqual(rc, 0)
        self.assertEqual(launched, [], "未确认时绝不能打开浏览器")

    def test_cli_blocks_when_health_not_reconfirmed(self):
        """CLI：资料完整 + 关键字段已确认，但健康声明未当次确认 → 仍然阻断。"""
        self._seed_full_config(health_confirmed=False)
        tdac_gate.record_critical_confirmation(_full_profile(), tdac_paths.config_dir())
        self.assertFalse(tdac_gate.authorize_current_prefill(
            _full_profile(), _full_dynamic(confirmed=False), _full_trip()),
            "未确认的健康声明不得获得本次操作授权")
        launched = []
        # 显式给出交互式 stdin，并让关键字段确认全部通过，使阻断原因稳定落在
        # “健康声明”，而不随运行环境是否真的带终端而变（见 _FakeTtyStdin）。
        with mock.patch.object(tdac_fill, "preflight_env", return_value=("chrome", 1234)), \
                mock.patch.object(tdac_fill, "launch_browser",
                                  side_effect=lambda *a, **k: launched.append(a)), \
                mock.patch.object(sys, "argv", ["tdac_fill.py"]), \
                mock.patch.object(sys, "stdin", _FakeTtyStdin()), \
                mock.patch.object(tdac_fill, "_ask_yes", return_value=True):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = tdac_fill.main()
        self.assertNotEqual(rc, 0)
        self.assertEqual(launched, [])
        self.assertIn("健康声明", out.getvalue())

    def test_cli_noninteractive_rejects_forged_persisted_confirmation(self):
        """非交互 CLI 不能把可编辑 JSON 的旧确认记录当作本次授权。"""
        self._seed_full_config()
        tdac_gate.record_critical_confirmation(_full_profile(), tdac_paths.config_dir())
        launched = []
        with mock.patch.object(tdac_fill, "preflight_env", return_value=("chrome", 1234)), \
                mock.patch.object(tdac_fill, "launch_browser",
                                  side_effect=lambda *a, **k: launched.append(a)), \
                mock.patch.object(sys, "argv", ["tdac_fill.py"]):
            with contextlib.redirect_stdout(io.StringIO()):
                rc = tdac_fill.main()
        self.assertNotEqual(rc, 0)
        self.assertEqual(launched, [], "伪造的落盘确认记录不得启动浏览器")

    def test_cli_proceeds_only_with_current_operation_authorization(self):
        """内存中的一次性当次授权才可让 CLI 继续到浏览器启动点。"""
        self._seed_full_config()
        tdac_gate.authorize_current_prefill(_full_profile(), _full_dynamic(), _full_trip())
        rep = self._run_cli_expecting(RuntimeError)
        self.assertIn("REACHED_BROWSER", str(rep))

    def _run_cli_expecting(self, exc_type):
        """在受控环境下运行 CLI 主流程，并把 Rep 产生的日志句柄收干净。

        返回捕获到的异常实例。日志文件句柄必须显式关闭，否则 Windows 下
        临时目录无法清理（这是一个真实的资源泄漏路径，本测试予以覆盖）。
        """
        created = []

        class _Rep(tdac_fill.Rep):
            def __init__(self):
                super().__init__()
                created.append(self)

        captured = None
        with mock.patch.object(tdac_fill, "Rep", _Rep), \
                mock.patch.object(tdac_fill, "preflight_env", return_value=("chrome", 1234)), \
                mock.patch.object(tdac_fill, "launch_browser",
                                  side_effect=RuntimeError("REACHED_BROWSER")):
            with mock.patch.object(sys, "argv", ["tdac_fill.py"]):
                with contextlib.redirect_stdout(io.StringIO()):
                    try:
                        tdac_fill.main()
                    except exc_type as e:          # noqa: PERF203
                        captured = e
        for r in created:
            if not getattr(r, "_closed", False):
                r._fh.close()                       # 兜底关闭，避免临时目录被占用
        return captured

    def test_gui_and_cli_share_the_same_gate_function(self):
        """GUI 与 CLI 必须调用同一个门禁实现（避免两套判定漂移）。

        只做源码文本检查（**不导入 tdac_gui**），保证本模块保持纯逻辑、
        不触碰 tkinter；否则会与 GUI 测试互相干扰 Tcl 解释器的生命周期。
        """
        root = os.path.dirname(os.path.abspath(tdac_fill.__file__))
        for name, expected in (("tdac_gui.py", "tdac_gate.data_blocker_groups"),
                               ("tdac_fill.py", "tdac_gate.prefill_blocker_groups")):
            with open(os.path.join(root, name), encoding="utf-8") as fh:
                source = fh.read()
            self.assertIn(expected, source, name)

    def test_gate_still_computes_blockers_from_records(self):
        groups = tdac_gate.prefill_blocker_groups({}, {}, {}, config_dir=tdac_paths.config_dir())
        self.assertEqual([name for name, _ in groups],
                         [tdac_validate.GROUP_PROFILE, tdac_validate.GROUP_TRIP,
                          tdac_validate.GROUP_HEALTH])


# --------------------------------------------------------------------------- #
# P2 写入路径统一保护 + ACL 加固失败可查
# --------------------------------------------------------------------------- #
class PathGuardTests(_DataDirCase):

    def test_write_outside_managed_root_is_refused(self):
        outside = os.path.join(tempfile.gettempdir(), "tdac-outside-should-not-exist.json")
        if os.path.exists(outside):
            os.remove(outside)
        with self.assertRaises(PermissionError):
            tdac_paths.atomic_write_json(outside, {"a": 1})
        self.assertFalse(os.path.exists(outside), "越界写入必须被拒绝且不留文件")

    def test_write_inside_managed_root_is_allowed(self):
        target = os.path.join(tdac_paths.config_dir(), "guard-ok.json")
        tdac_paths.atomic_write_json(target, {"a": 1})
        self.assertTrue(os.path.exists(target))

    def test_draft_transient_files_are_inside_managed_root(self):
        for path in (tdac_draft.staged_json_path(), tdac_draft.prev_json_path(),
                     tdac_draft.commit_marker_path(), tdac_draft.staged_secrets_path(),
                     tdac_draft.prev_secrets_path()):
            ok = True
            try:
                tdac_paths.assert_managed_write(path)
            except PermissionError:
                ok = False
            self.assertTrue(ok, f"事务文件必须位于受管目录内：{path}")

    def test_secret_paths_follow_data_dir_relocation(self):
        """密文路径必须随 TDAC_DATA_DIR 动态解析（历史缺陷：导入时冻结）。"""
        self.assertEqual(tdac_sec.secrets_path(), tdac_paths.secrets_path())
        self.assertEqual(os.path.dirname(tdac_sec.draft_secrets_path()),
                         tdac_paths.secrets_dir())

    def test_source_like_data_root_is_rejected_for_init_read_and_write(self):
        unsafe = os.path.join(self._tmp.name, "source-like")
        os.makedirs(os.path.join(unsafe, "config"))
        with open(os.path.join(unsafe, "tdac_fill.py"), "w", encoding="utf-8") as fh:
            fh.write("# source marker\n")
        with open(os.path.join(unsafe, "config", "draft.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        os.environ["TDAC_DATA_DIR"] = unsafe
        with self.assertRaises(PermissionError):
            tdac_paths.ensure_data_dirs()
        with self.assertRaises(PermissionError):
            tdac_paths.atomic_write_json(os.path.join(unsafe, "config", "x.json"), {"x": 1})
        with self.assertRaises(PermissionError):
            tdac_draft.read_draft_file()


class AclHardeningTests(_DataDirCase):

    def _without_username(self):
        """只把 USERNAME 置空（**不能**清空整个环境，否则会丢掉 TDAC_DATA_DIR，
        导致写入落到真实的用户数据目录）。"""
        return mock.patch.dict(os.environ, {"USERNAME": ""})

    def test_acl_failure_is_reported_not_silently_ignored(self):
        target = os.path.join(tdac_paths.secrets_dir(), "acl-probe.dat")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(b"x")
        with self._without_username():
            ok = tdac_sec.harden_acl_path(target)
            self.assertFalse(ok, "无法确定用户名时必须返回失败")
            self.assertTrue(tdac_sec.acl_warning(), "ACL 加固失败必须留下可查的说明")

    def test_acl_failure_does_not_block_saving(self):
        """ACL 加固是加固项，失败不应让保存本身失败（但必须有提示）。"""
        with self._without_username():
            tdac_sec.save_secrets({"passportNo": PASSPORT, "phoneNo": PHONE})
            self.assertTrue(tdac_sec.acl_warning())
            # 保存必须**留在受管沙箱内**，绝不能写进真实用户数据目录
            self.assertEqual(os.path.dirname(tdac_sec.secrets_path()),
                             tdac_paths.secrets_dir())
            self.assertTrue(tdac_sec.secrets_path().startswith(self._tmp.name))
        self.assertEqual(tdac_sec.load_secrets()["passportNo"], PASSPORT)


# --------------------------------------------------------------------------- #
# P2 草稿时间戳异常的恢复策略
# --------------------------------------------------------------------------- #
class DraftTimestampTests(_DataDirCase):

    def _save(self, **kw):
        tdac_draft.save_draft({"familyName": "TESTFAMILY"}, {}, {}, **kw)

    def test_missing_timestamp_is_unknown_not_stale(self):
        self._save()
        with open(tdac_draft.draft_path(), encoding="utf-8") as fh:
            info = json.load(fh)
        info.pop("savedAtEpoch", None)
        info.pop("savedAt", None)
        with open(tdac_draft.draft_path(), "w", encoding="utf-8") as fh:
            json.dump(info, fh)
        self.assertEqual(tdac_draft.draft_time_status(), tdac_draft.TIME_UNKNOWN)
        self.assertFalse(tdac_draft.draft_is_stale(), "时间未知不得判为过期（优先恢复）")

    def test_abnormal_future_timestamp_is_not_stale(self):
        self._save()
        with open(tdac_draft.draft_path(), encoding="utf-8") as fh:
            info = json.load(fh)
        info["savedAtEpoch"] = 4102444800.0      # 2100-01-01
        info["savedAt"] = "2100-01-01 00:00:00"
        with open(tdac_draft.draft_path(), "w", encoding="utf-8") as fh:
            json.dump(info, fh)
        self.assertEqual(tdac_draft.draft_time_status(), tdac_draft.TIME_ABNORMAL)
        self.assertFalse(tdac_draft.draft_is_stale())

    def test_abnormal_ancient_timestamp_is_not_stale(self):
        self._save()
        with open(tdac_draft.draft_path(), encoding="utf-8") as fh:
            info = json.load(fh)
        info["savedAtEpoch"] = 0
        info["savedAt"] = "1970-01-01 00:00:00"
        with open(tdac_draft.draft_path(), "w", encoding="utf-8") as fh:
            json.dump(info, fh)
        self.assertEqual(tdac_draft.draft_time_status(), tdac_draft.TIME_ABNORMAL)
        self.assertFalse(tdac_draft.draft_is_stale())

    def test_garbage_timestamp_is_abnormal(self):
        self._save()
        with open(tdac_draft.draft_path(), encoding="utf-8") as fh:
            info = json.load(fh)
        info.pop("savedAtEpoch", None)
        info["savedAt"] = "not-a-timestamp"
        with open(tdac_draft.draft_path(), "w", encoding="utf-8") as fh:
            json.dump(info, fh)
        self.assertEqual(tdac_draft.draft_time_status(), tdac_draft.TIME_ABNORMAL)

    def test_normal_timestamp_still_drives_stale_detection(self):
        """时间戳正常时，过期判定必须照常生效（不能被上面的放宽抹掉）。"""
        self._save(now="2020-01-01 00:00:00")
        tdac_paths.atomic_write_json(
            os.path.join(tdac_paths.config_dir(), "profile.json"), {"familyName": "NEWFORMAL"})
        self.assertEqual(tdac_draft.draft_time_status(), tdac_draft.TIME_OK)
        self.assertTrue(tdac_draft.draft_is_stale())


if __name__ == "__main__":
    unittest.main()
