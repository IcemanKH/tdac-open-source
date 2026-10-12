import unittest

from tdac_privacy import (confirm_critical_fields, critical_fields_review, mask,
                          safe_value)


class MaskTests(unittest.TestCase):
    def test_generic_mask_keeps_head_and_tail(self):
        self.assertEqual(mask("E00000000", 2, 2), "E0*****00")

    def test_safe_value_masks_secret_fields(self):
        self.assertEqual(safe_value("phoneNo", "13800138000"), "13*******00")
        self.assertEqual(safe_value("passportNo", "E00000000"), "E0*****00")

    def test_safe_value_partially_masks_flight_and_postal(self):
        self.assertEqual(safe_value("arrivalFlightNo", "MU0000"), "M****0")
        self.assertEqual(safe_value("accommodationPostalCode", "12345"), "1***5")

    def test_safe_value_hides_address_and_email(self):
        self.assertEqual(safe_value("accommodationAddress", "1 ROAD"), "<已隐藏>")
        self.assertEqual(safe_value("email", "someone@example.com"), "<已隐藏>")

    def test_safe_value_keeps_ordinary_fields(self):
        self.assertEqual(safe_value("arrivalDate", "2026/01/01"), "2026/01/01")

    def test_safe_value_empty_stays_empty(self):
        self.assertEqual(safe_value("arrivalFlightNo", ""), "")


class CriticalFieldTests(unittest.TestCase):
    @staticmethod
    def _profile(**overrides):
        base = {"passportNo": "E00000000", "phoneNo": "13000000000",
                "email": "tester@example.invalid"}
        base.update(overrides)
        return base

    def test_review_lists_the_three_critical_fields(self):
        items = critical_fields_review(self._profile())
        self.assertEqual([i["field"] for i in items], ["passportNo", "phoneNo", "email"])
        self.assertTrue(all(i["present"] for i in items))
        self.assertTrue(all(i["masked"] for i in items))

    def test_missing_field_blocks_confirmation(self):
        ok, unconfirmed = confirm_critical_fields(self._profile(email=""), lambda p: True)
        self.assertFalse(ok)
        self.assertEqual(unconfirmed, ["收件邮箱"])

    def test_declining_any_field_blocks_confirmation(self):
        answers = iter([True, False, True])
        ok, unconfirmed = confirm_critical_fields(self._profile(), lambda p: next(answers))
        self.assertFalse(ok)
        self.assertEqual(unconfirmed, ["手机号"])

    def test_all_fields_confirmed(self):
        ok, unconfirmed = confirm_critical_fields(self._profile(), lambda p: True)
        self.assertTrue(ok)
        self.assertEqual(unconfirmed, [])


class CriticalFieldGuiHookTests(unittest.TestCase):
    """reveal / revise 交互契约（B1 同步修正：reveal 只能由用户主动触发）。"""

    @staticmethod
    def _profile(**overrides):
        base = {"passportNo": "E00000000", "phoneNo": "13000000000",
                "email": "tester@example.invalid"}
        base.update(overrides)
        return base

    def test_reveal_is_never_invoked_automatically(self):
        """安全要求：确认流程自身绝不得自动揭示完整敏感值。"""
        seen = []
        ok, _ = confirm_critical_fields(self._profile(), lambda p: True,
                                        reveal=lambda field: seen.append(field) or "FULL")
        self.assertTrue(ok)
        self.assertEqual(seen, [],
                         "reveal 不得被自动调用：敏感值只能在用户主动操作时揭示")

    def test_reveal_only_when_the_ask_implementation_requests_it(self):
        """只有声明 (prompt, reveal) 且用户主动点“显示”时，reveal 才会执行。"""
        seen = []

        def ask(prompt, reveal):
            if "护照号" in prompt:      # 模拟用户在对话框里点了“显示完整值”
                reveal("passportNo")
            return True

        ok, _ = confirm_critical_fields(self._profile(), ask,
                                        reveal=lambda field: seen.append(field) or "FULL")
        self.assertTrue(ok)
        self.assertEqual(seen, ["passportNo"])

    def test_single_arg_ask_keeps_legacy_behaviour(self):
        """旧式单参数 CLI 回调仍然可用（向后兼容），且不会触发 reveal。"""
        seen = []
        ok, _ = confirm_critical_fields(self._profile(), lambda p: True,
                                        reveal=lambda field: seen.append(field) or "FULL")
        self.assertTrue(ok)
        self.assertEqual(seen, [])

    def test_revise_allows_correcting_a_declined_field(self):
        answers = iter([False, True, True, True])
        profile = self._profile()
        ok, unconfirmed = confirm_critical_fields(
            profile, lambda p: next(answers), revise=lambda field: "E99999999")
        self.assertTrue(ok)
        self.assertEqual(unconfirmed, [])
        self.assertEqual(profile["passportNo"], "E99999999")

    def test_decline_without_revise_still_blocks(self):
        ok, unconfirmed = confirm_critical_fields(self._profile(), lambda p: False)
        self.assertFalse(ok)
        self.assertEqual(unconfirmed, ["护照号"])

    def test_revise_returning_empty_still_blocks(self):
        ok, unconfirmed = confirm_critical_fields(self._profile(), lambda p: False,
                                                  revise=lambda field: "")
        self.assertFalse(ok)
        self.assertEqual(unconfirmed, ["护照号"])


if __name__ == "__main__":
    unittest.main()
