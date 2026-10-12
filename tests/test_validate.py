import os
import tempfile
import unittest

os.environ.setdefault("TDAC_DATA_DIR", os.path.join(tempfile.gettempdir(), "tdac-tests-data"))

from tdac_validate import (format_problems, validate_email, validate_passport,
                           validate_phone, validate_profile)


class PassportTests(unittest.TestCase):
    def test_cn_ordinary_passport(self):
        ok, _ = validate_passport("E12345678")
        self.assertTrue(ok)

    def test_lowercase_letter_accepted(self):
        self.assertTrue(validate_passport("e12345678")[0])

    def test_all_digits_rejected(self):
        self.assertFalse(validate_passport("12345678")[0])

    def test_empty_rejected(self):
        self.assertFalse(validate_passport("")[0])
        self.assertFalse(validate_passport(None)[0])

    def test_non_ascii_rejected(self):
        self.assertFalse(validate_passport("护照12345678")[0])


class PhoneTests(unittest.TestCase):
    def test_cn_mobile_ok(self):
        self.assertTrue(validate_phone("13800138000")[0])

    def test_cn_mobile_wrong_prefix_rejected(self):
        self.assertFalse(validate_phone("12800138000")[0])

    def test_cn_mobile_too_short_rejected(self):
        self.assertFalse(validate_phone("1380013800")[0])

    def test_plus_sign_rejected(self):
        self.assertFalse(validate_phone("+8613800138000")[0])

    def test_other_country_code_uses_length_rule(self):
        self.assertTrue(validate_phone("0412345678", "61")[0])
        self.assertFalse(validate_phone("123", "61")[0])


class EmailTests(unittest.TestCase):
    def test_common_email_ok(self):
        self.assertTrue(validate_email("tester@example.invalid")[0])

    def test_missing_at_rejected(self):
        self.assertFalse(validate_email("tester.example.com")[0])

    def test_double_at_rejected(self):
        self.assertFalse(validate_email("a@@b.com")[0])

    def test_empty_rejected(self):
        self.assertFalse(validate_email("")[0])


class ProfileValidationTests(unittest.TestCase):
    def _good(self):
        return {"passportNo": "E00000000", "phoneNo": "13000000000",
                "phoneCode": "86", "email": "tester@example.invalid"}

    def test_good_profile_has_no_problems(self):
        self.assertEqual(validate_profile(self._good()), [])

    def test_problems_do_not_carry_plaintext(self):
        bad = self._good()
        bad["phoneNo"] = "123"
        bad["email"] = "nope"
        problems = validate_profile(bad)
        self.assertEqual({p["field"] for p in problems}, {"phoneNo", "email"})
        blob = format_problems(problems)
        self.assertNotIn("123", blob)
        self.assertNotIn("nope", blob)
        for p in problems:
            self.assertNotIn("value", p)          # 只暴露长度，不暴露明文
            self.assertIn("value_len", p)


if __name__ == "__main__":
    unittest.main()
