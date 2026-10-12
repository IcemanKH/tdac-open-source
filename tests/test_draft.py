# -*- coding: utf-8 -*-
"""草稿暂存 / 恢复 / 加密 / 删除，以及“草稿 ↔ 正式预填”门禁分离的离线测试。

全部离线：不启动浏览器、不联网、不触泰国官网，也不执行任何真实申报。
所有资料均为合成值。
"""
import datetime as dt
import json
import os
import tempfile
import unittest

os.environ.setdefault("TDAC_DATA_DIR", os.path.join(tempfile.gettempdir(), "tdac-tests-data"))

import tdac_draft  # noqa: E402
import tdac_fill  # noqa: E402
import tdac_paths  # noqa: E402
import tdac_privacy  # noqa: E402
import tdac_sec  # noqa: E402
import tdac_validate  # noqa: E402

PASSPORT = "E00000000"
PHONE = "13000000000"
EMAIL = "tester@example.invalid"


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


def _full_profile():
    return {
        "familyName": "TESTFAMILY", "firstName": "TESTGIVEN", "middleName": "",
        "nationality": "CHINESE", "dateOfBirth": "1990/01/01", "gender": "MALE",
        "occupation": "EMPLOYEE", "countryOfResidence": "CHINA",
        "cityOfResidence": "TESTCITY", "phoneCode": "86", "visaNo": "",
        "email": EMAIL, "passportNo": PASSPORT, "phoneNo": PHONE,
    }


def _full_trip(days_ahead=1):
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


class _DataDirCase(unittest.TestCase):
    """把用户数据目录重定向到临时目录，绝不触碰真实的 %LOCALAPPDATA%\\TDAC\\。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = _TmpDataDir(self._tmp.name)
        self._env.__enter__()

    def tearDown(self):
        self._env.__exit__(None, None, None)
        self._tmp.cleanup()


# --------------------------------------------------------------------------- #
# 1. 暂存：不完整资料也允许
# --------------------------------------------------------------------------- #
class DraftIncompleteSaveTests(_DataDirCase):

    def test_incomplete_draft_can_be_saved(self):
        """只剩一个姓，其余全空——也必须允许暂存，不抛异常、不报缺失。"""
        path = tdac_draft.save_draft({"familyName": "TESTFAMILY"}, {}, {})
        self.assertTrue(os.path.exists(path))
        self.assertTrue(tdac_draft.draft_exists())
        draft = tdac_draft.load_draft()
        self.assertEqual(draft["profile"]["familyName"], "TESTFAMILY")

    def test_draft_save_ignores_required_field_gate(self):
        """草稿保存与正式预填校验严格分离：缺必填项不影响暂存，只影响预填。"""
        tdac_draft.save_draft({"familyName": "TESTFAMILY"}, {}, {})
        blockers = tdac_validate.prefill_blockers(
            {"familyName": "TESTFAMILY"}, {}, {},
            critical_confirmed=False, health_reconfirmed=False)
        self.assertTrue(blockers, "资料不完整时必须阻断官网预填")
        self.assertTrue(tdac_draft.draft_exists(), "阻断预填不得影响已暂存的草稿")

    def test_empty_draft_is_still_a_draft(self):
        tdac_draft.save_draft({}, {}, {})
        self.assertTrue(tdac_draft.draft_exists())


# --------------------------------------------------------------------------- #
# 2. 敏感字段加密：不产生明文副本
# --------------------------------------------------------------------------- #
class DraftSecretEncryptionTests(_DataDirCase):

    def _save_with_secrets(self):
        tdac_draft.save_draft(_full_profile(), _full_trip(), _full_dynamic(),
                              secrets={"passportNo": PASSPORT, "phoneNo": PHONE})

    def test_draft_json_contains_no_plaintext_secret(self):
        """draft.json 里不得出现敏感字段的**值**；只允许出现“是否已暂存”的键名标记。"""
        self._save_with_secrets()
        with open(tdac_draft.draft_path(), "rb") as fh:
            raw = fh.read()
        self.assertNotIn(PASSPORT.encode(), raw)
        self.assertNotIn(PHONE.encode(), raw)
        info = json.loads(raw.decode("utf-8"))
        self.assertNotIn("secrets", info)
        self.assertNotIn("passportNo", info["profile"])
        self.assertNotIn("phoneNo", info["profile"])
        # 逐层展开所有取值，确认没有任何一处等于护照号 / 手机号
        values = []

        def walk(node):
            if isinstance(node, dict):
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
            else:
                values.append(node)

        walk(info)
        self.assertNotIn(PASSPORT, values)
        self.assertNotIn(PHONE, values)

    def test_draft_json_records_presence_only(self):
        self._save_with_secrets()
        info = tdac_draft.read_draft_file()
        self.assertEqual(info["secretsPresent"], {"passportNo": True, "phoneNo": True})
        self.assertNotIn("secrets", info)

    def test_draft_ciphertext_is_dpapi_and_decryptable(self):
        self._save_with_secrets()
        path = tdac_sec.draft_secrets_path()
        self.assertTrue(os.path.exists(path))
        with open(path, "rb") as fh:
            raw = fh.read()
        self.assertNotIn(PASSPORT.encode(), raw)
        self.assertNotIn(PHONE.encode(), raw)
        self.assertEqual(tdac_sec.load_draft_secrets(),
                         {"passportNo": PASSPORT, "phoneNo": PHONE})

    def test_draft_secrets_file_lives_in_managed_secrets_dir(self):
        """隐私与目录规则：草稿密文必须落在受管 secrets 目录内。"""
        self._save_with_secrets()
        self.assertEqual(os.path.dirname(tdac_sec.draft_secrets_path()),
                         tdac_paths.secrets_dir())
        self.assertEqual(os.path.dirname(tdac_draft.draft_path()),
                         tdac_paths.config_dir())

    def test_clearing_secrets_removes_ciphertext(self):
        """把护照号 / 手机号清空后再暂存，不得残留旧密文。"""
        self._save_with_secrets()
        tdac_draft.save_draft(_full_profile(), _full_trip(), _full_dynamic(),
                              secrets={"passportNo": "", "phoneNo": ""})
        self.assertFalse(os.path.exists(tdac_sec.draft_secrets_path()))
        info = tdac_draft.read_draft_file()
        self.assertEqual(info["secretsPresent"], {"passportNo": False, "phoneNo": False})
        self.assertEqual(tdac_draft.load_draft()["secrets"], {})

    def test_formal_secrets_are_not_touched_by_draft(self):
        """暂存草稿不得覆盖已确认的正式密文。"""
        tdac_sec.save_secrets({"passportNo": PASSPORT, "phoneNo": PHONE})
        tdac_draft.save_draft(_full_profile(), _full_trip(), _full_dynamic(),
                              secrets={"passportNo": "E99999999", "phoneNo": ""})
        self.assertEqual(tdac_sec.load_secrets()["passportNo"], PASSPORT)
        self.assertEqual(tdac_sec.load_secrets()["phoneNo"], PHONE)

    def test_corrupt_ciphertext_raises_instead_of_silently_empty(self):
        self._save_with_secrets()
        with open(tdac_sec.draft_secrets_path(), "wb") as fh:
            fh.write(b"not a valid dpapi blob")
        with self.assertRaises(Exception):
            tdac_draft.load_draft()


# --------------------------------------------------------------------------- #
# 2b. 一致性核验（G1 验收复核新增）：损坏 / 缺失 / 新旧判定
# --------------------------------------------------------------------------- #
class DraftConsistencyTests(_DataDirCase):

    def test_missing_ciphertext_is_reported_not_silently_empty(self):
        """标记“已暂存护照号”，但密文文件被删 —— 必须报错，不得静默变成空值。"""
        tdac_draft.save_draft(_full_profile(), _full_trip(), _full_dynamic(),
                              secrets={"passportNo": PASSPORT, "phoneNo": PHONE})
        os.remove(tdac_sec.draft_secrets_path())
        # 缺密文现在先触发“代际不完整”拒绝；两种异常都明确拒绝恢复，
        # 且不会把敏感字段静默置空。
        with self.assertRaises((tdac_draft.DraftSecretsUnavailable,
                                tdac_draft.DraftGenerationMismatch)) as ctx:
            tdac_draft.load_draft()
        self.assertNotIn(PASSPORT, str(ctx.exception))

    def test_draft_status_distinguishes_none_ok_corrupt(self):
        self.assertEqual(tdac_draft.draft_status(), tdac_draft.STATUS_NONE)
        tdac_draft.save_draft({"familyName": "TESTFAMILY"}, {}, {})
        self.assertEqual(tdac_draft.draft_status(), tdac_draft.STATUS_OK)
        with open(tdac_draft.draft_path(), "w", encoding="utf-8") as fh:
            fh.write("{ broken json ")
        self.assertEqual(tdac_draft.draft_status(), tdac_draft.STATUS_CORRUPT)

    def test_draft_newer_than_formal_is_not_stale(self):
        """先暂存草稿、后来没再动正式资料 → 草稿应被恢复。"""
        tdac_draft.save_draft({"familyName": "TESTFAMILY"}, {}, {})
        os.makedirs(tdac_paths.config_dir(), exist_ok=True)
        path = os.path.join(tdac_paths.config_dir(), "profile.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"familyName": "OLDFORMAL"}, fh)
        old = tdac_draft.draft_saved_epoch() - 3600
        os.utime(path, (old, old))
        self.assertFalse(tdac_draft.draft_is_stale())

    def test_draft_older_than_formal_is_stale(self):
        """暂存草稿之后又重新保存了正式资料 → 旧草稿不得覆盖更新的正式资料。"""
        tdac_draft.save_draft({"familyName": "TESTFAMILY"}, {}, {},
                              now="2020-01-01 00:00:00")
        os.makedirs(tdac_paths.config_dir(), exist_ok=True)
        with open(os.path.join(tdac_paths.config_dir(), "profile.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"familyName": "NEWFORMAL"}, fh)
        self.assertTrue(tdac_draft.draft_is_stale())

    def test_without_official_config_draft_is_not_stale(self):
        tdac_draft.save_draft({"familyName": "TESTFAMILY"}, {}, {},
                              now="2020-01-01 00:00:00")
        self.assertFalse(tdac_draft.draft_is_stale())

    def test_corrupt_draft_is_never_treated_as_stale(self):
        """损坏的草稿走“明确报错”路径，不能被当成“过期草稿”静默忽略。"""
        os.makedirs(tdac_paths.config_dir(), exist_ok=True)
        with open(tdac_draft.draft_path(), "w", encoding="utf-8") as fh:
            fh.write("{ broken json ")
        self.assertFalse(tdac_draft.draft_is_stale())


# --------------------------------------------------------------------------- #
# 3. 恢复（模拟“下次打开”）与修改
# --------------------------------------------------------------------------- #
class DraftRestoreTests(_DataDirCase):

    def test_roundtrip_restores_every_section(self):
        tdac_draft.save_draft(_full_profile(), _full_trip(), _full_dynamic(),
                              secrets={"passportNo": PASSPORT, "phoneNo": PHONE})
        draft = tdac_draft.load_draft()
        self.assertEqual(draft["profile"]["cityOfResidence"], "TESTCITY")
        self.assertEqual(draft["trip"]["arrivalFlightNo"], "MU0000")
        self.assertEqual(draft["dynamic"]["yellowFeverVaccination"], "NO")
        self.assertEqual(draft["secrets"]["passportNo"], PASSPORT)
        self.assertTrue(draft["savedAt"])

    def test_health_confirmation_is_never_restored_as_confirmed(self):
        """即使暂存时答案已确认，草稿恢复后仍必须是“未确认”。"""
        dyn = _full_dynamic(confirmed=True)
        tdac_draft.save_draft(_full_profile(), _full_trip(), dyn, secrets={})
        restored = tdac_draft.load_draft()
        self.assertFalse(restored["dynamic"]["last14DaysConfirmed"])
        self.assertFalse(restored["dynamic"]["healthDeclarationConfirmed"])
        self.assertFalse(tdac_draft.read_draft_file()["healthConfirmed"])

    def test_draft_answers_are_kept_for_editing(self):
        """答案本身要保留（供继续修改），只是确认标记不继承。"""
        tdac_draft.save_draft(_full_profile(), _full_trip(), _full_dynamic(), secrets={})
        restored = tdac_draft.load_draft()
        self.assertEqual(restored["dynamic"]["last14DaysCountries"], ["CHINA"])
        self.assertEqual(restored["dynamic"]["symptoms"], "NO")

    def test_modify_overwrites_the_single_draft(self):
        tdac_draft.save_draft({"familyName": "FIRST"}, {}, {})
        tdac_draft.save_draft({"familyName": "SECOND"}, {"arrivalFlightNo": "MU0009"}, {})
        draft = tdac_draft.load_draft()
        self.assertEqual(draft["profile"]["familyName"], "SECOND")
        self.assertEqual(draft["trip"]["arrivalFlightNo"], "MU0009")
        names = os.listdir(tdac_paths.config_dir())
        self.assertEqual(sorted(n for n in names if n.startswith("draft")), ["draft.json"],
                         "首版只允许存在一份草稿")

    def test_load_without_draft_returns_empty(self):
        self.assertEqual(tdac_draft.load_draft(), {})
        self.assertEqual(tdac_draft.read_draft_file(), {})

    def test_corrupt_draft_json_is_reported_as_empty(self):
        os.makedirs(tdac_paths.config_dir(), exist_ok=True)
        with open(tdac_draft.draft_path(), "w", encoding="utf-8") as fh:
            fh.write("{ not json ")
        self.assertEqual(tdac_draft.read_draft_file(), {})
        self.assertEqual(tdac_draft.load_draft(), {})

    def test_formal_only_markers_are_not_written_into_draft(self):
        profile = _full_profile()
        profile["criticalConfirmedAt"] = "2026-10-10 10:00:00"
        profile["criticalFingerprint"] = "deadbeef"
        tdac_draft.save_draft(profile, {}, {}, secrets={})
        raw = tdac_draft.read_draft_file()["profile"]
        self.assertNotIn("criticalConfirmedAt", raw)
        self.assertNotIn("criticalFingerprint", raw)


# --------------------------------------------------------------------------- #
# 4. 删除草稿
# --------------------------------------------------------------------------- #
class DraftDeleteTests(_DataDirCase):

    def test_delete_removes_both_files(self):
        tdac_draft.save_draft(_full_profile(), _full_trip(), _full_dynamic(),
                              secrets={"passportNo": PASSPORT, "phoneNo": PHONE})
        removed = tdac_draft.delete_draft()
        self.assertEqual(len(removed), 2)
        self.assertFalse(tdac_draft.draft_exists())
        self.assertFalse(os.path.exists(tdac_sec.draft_secrets_path()))

    def test_delete_without_draft_is_noop(self):
        self.assertEqual(tdac_draft.delete_draft(), [])

    def test_delete_does_not_touch_formal_config(self):
        tdac_sec.save_secrets({"passportNo": PASSPORT, "phoneNo": PHONE})
        path = os.path.join(tdac_paths.config_dir(), "profile.json")
        os.makedirs(tdac_paths.config_dir(), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"familyName": "KEEPME"}, fh)
        tdac_draft.save_draft(_full_profile(), _full_trip(), _full_dynamic(), secrets={})
        tdac_draft.delete_draft()
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["familyName"], "KEEPME")
        self.assertEqual(tdac_sec.load_secrets()["passportNo"], PASSPORT)


# --------------------------------------------------------------------------- #
# 5. 描述与状态区分
# --------------------------------------------------------------------------- #
class DraftDescriptionTests(_DataDirCase):

    def test_describe_marks_encrypted_secrets(self):
        tdac_draft.save_draft(_full_profile(), {}, {},
                              secrets={"passportNo": PASSPORT, "phoneNo": PHONE})
        text = tdac_draft.describe_draft()
        self.assertIn("护照号", text)
        self.assertIn("手机号", text)
        self.assertNotIn(PASSPORT, text)
        self.assertNotIn(PHONE, text)

    def test_describe_without_draft(self):
        self.assertEqual(tdac_draft.describe_draft(), "草稿：无")


# --------------------------------------------------------------------------- #
# 6. 正式预填门禁（与草稿分离）
# --------------------------------------------------------------------------- #
class PrefillGateTests(_DataDirCase):

    def test_incomplete_blocks_prefill(self):
        blockers = tdac_validate.prefill_blockers({}, {}, {},
                                                 critical_confirmed=False,
                                                 health_reconfirmed=False)
        self.assertTrue(any("资料不完整" in b for b in blockers), blockers)

    def test_bad_format_blocks_prefill(self):
        profile = _full_profile()
        profile["passportNo"] = "12345678"
        blockers = tdac_validate.prefill_blockers(profile, _full_trip(), _full_dynamic(),
                                                 critical_confirmed=True,
                                                 health_reconfirmed=True)
        self.assertTrue(any("格式不正确" in b for b in blockers), blockers)

    def test_bad_date_format_blocks_prefill(self):
        trip = _full_trip()
        trip["arrivalDate"] = "2026-01-01"
        blockers = tdac_validate.prefill_blockers(_full_profile(), trip, _full_dynamic(),
                                                 critical_confirmed=True,
                                                 health_reconfirmed=True)
        self.assertTrue(any("入境日期格式不正确" in b for b in blockers), blockers)

    def test_unconfirmed_critical_fields_block_prefill(self):
        blockers = tdac_validate.prefill_blockers(_full_profile(), _full_trip(), _full_dynamic(),
                                                 critical_confirmed=False,
                                                 health_reconfirmed=True)
        self.assertTrue(any("二次确认" in b for b in blockers), blockers)

    def test_health_must_be_reconfirmed_block_prefill(self):
        blockers = tdac_validate.prefill_blockers(_full_profile(), _full_trip(), _full_dynamic(),
                                                 critical_confirmed=True,
                                                 health_reconfirmed=False)
        self.assertTrue(any("健康声明" in b for b in blockers), blockers)

    def test_outside_window_blocks_prefill(self):
        blockers = tdac_validate.prefill_blockers(_full_profile(), _full_trip(days_ahead=30),
                                                 _full_dynamic(),
                                                 critical_confirmed=True,
                                                 health_reconfirmed=True)
        self.assertTrue(any("申报窗口" in b for b in blockers), blockers)

    def test_all_conditions_met_allows_prefill(self):
        blockers = tdac_validate.prefill_blockers(_full_profile(), _full_trip(), _full_dynamic(),
                                                 critical_confirmed=True,
                                                 health_reconfirmed=True)
        self.assertEqual(blockers, [])

    def test_required_field_lists_match_tdac_fill(self):
        """必填清单必须与主流程 check_config 完全一致，防止两处漂移。"""
        expected = {f"profile.{k}" for k, _ in tdac_validate.REQUIRED_PROFILE_FIELDS}
        expected |= {f"trip.{k}" for k, _ in tdac_validate.REQUIRED_TRIP_FIELDS}
        self.assertEqual(set(tdac_fill.check_config({"profile": {}, "trip": {}}, None)),
                         expected)

    def test_missing_required_fields_uses_chinese_labels(self):
        missing = tdac_validate.missing_required_fields({}, {})
        self.assertIn("护照号", missing)
        self.assertIn("入境日期", missing)
        self.assertNotIn("passportNo", missing)

    def test_blockers_are_grouped_in_fixed_order(self):
        groups = tdac_validate.prefill_blocker_groups(
            {}, {}, {}, critical_confirmed=False, health_reconfirmed=False)
        self.assertEqual([name for name, _ in groups],
                         [tdac_validate.GROUP_PROFILE, tdac_validate.GROUP_TRIP,
                          tdac_validate.GROUP_HEALTH])
        flat = tdac_validate.prefill_blockers(
            {}, {}, {}, critical_confirmed=False, health_reconfirmed=False)
        self.assertEqual(flat, [item for _name, items in groups for item in items])

    def test_grouped_blockers_only_include_named_groups_when_blocked(self):
        """全部满足时不产生任何分组；只有缺健康内容时只出现健康分组。"""
        self.assertEqual(tdac_validate.prefill_blocker_groups(
            _full_profile(), _full_trip(), _full_dynamic(),
            critical_confirmed=True, health_reconfirmed=True), [])
        dyn = _full_dynamic()
        dyn["last14DaysCountries"] = []
        groups = tdac_validate.prefill_blocker_groups(
            _full_profile(), _full_trip(), dyn,
            critical_confirmed=True, health_reconfirmed=True)
        self.assertEqual([name for name, _ in groups], [tdac_validate.GROUP_HEALTH])

    def test_missing_health_countries_blocks_prefill(self):
        dyn = _full_dynamic()
        dyn["last14DaysCountries"] = []
        blockers = tdac_validate.prefill_blockers(
            _full_profile(), _full_trip(), dyn,
            critical_confirmed=True, health_reconfirmed=True)
        self.assertTrue(any("14 天" in b for b in blockers), blockers)


class CriticalFingerprintTests(unittest.TestCase):

    def test_fingerprint_changes_when_critical_field_changes(self):
        base = {"passportNo": PASSPORT, "phoneNo": PHONE, "email": EMAIL}
        changed = dict(base, passportNo="E11111111")
        self.assertTrue(tdac_privacy.critical_fingerprint(base))
        self.assertNotEqual(tdac_privacy.critical_fingerprint(base),
                            tdac_privacy.critical_fingerprint(changed))

    def test_fingerprint_is_empty_when_incomplete(self):
        self.assertEqual(
            tdac_privacy.critical_fingerprint({"passportNo": PASSPORT}), "")

    def test_fingerprint_does_not_contain_plaintext(self):
        fp = tdac_privacy.critical_fingerprint(
            {"passportNo": PASSPORT, "phoneNo": PHONE, "email": EMAIL})
        self.assertNotIn(PASSPORT, fp)
        self.assertNotIn(PHONE, fp)


if __name__ == "__main__":
    unittest.main()
