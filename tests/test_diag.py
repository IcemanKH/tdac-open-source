import os
import tempfile
import unittest

os.environ.setdefault("TDAC_DATA_DIR", os.path.join(tempfile.gettempdir(), "tdac-tests-data"))

import tdac_diag  # noqa: E402


class ForbiddenPatternTests(unittest.TestCase):
    def test_phone_email_passport_are_detected(self):
        self.assertIn("手机号", tdac_diag.scan_forbidden("call 13800138000 now"))
        self.assertIn("邮箱", tdac_diag.scan_forbidden("mail a.b@example.com"))
        self.assertIn("证件号", tdac_diag.scan_forbidden("passport E12345678"))

    def test_windows_user_path_is_detected(self):
        self.assertIn("Windows 用户名", tdac_diag.scan_forbidden(r"open C:\Users\someone\x"))

    def test_version_strings_are_not_false_positives(self):
        self.assertEqual(tdac_diag.scan_forbidden("python 3.13.14 app 0.1.0"), [])

    def test_scrub_removes_matches(self):
        out = tdac_diag.scrub("call 13800138000 mail a@b.com pass E12345678")
        self.assertNotIn("13800138000", out)
        self.assertNotIn("a@b.com", out)
        self.assertNotIn("E12345678", out)


class RenderTests(unittest.TestCase):
    def test_rendered_package_passes_self_check(self):
        text = tdac_diag.render_diagnostic()
        self.assertEqual(tdac_diag.scan_forbidden(text), [])
        self.assertIn("TDAC 脱敏诊断包", text)
        self.assertIn("不包含", text)

    def test_forbidden_values_injected_are_scrubbed_not_leaked(self):
        """即使被误塞进数据，也不得出现在最终文本里（宁可脱敏也不泄露）。"""
        data = {
            "姓名": "SOMEONE",
            "电话": "13800138000",
            "邮箱": "leak@example.com",
            "护照": "E12345678",
            "路径": r"C:\Users\somebody\secret",
        }
        text = tdac_diag.render_diagnostic(data)
        for bad in ("13800138000", "leak@example.com", "E12345678", r"C:\Users\somebody"):
            self.assertNotIn(bad, text)
        self.assertEqual(tdac_diag.scan_forbidden(text), [])

    def test_whitelist_only_booleans_for_presence(self):
        data = tdac_diag.collect_diagnostic(
            config_presence=lambda: {"个人资料必填项齐全": {"email": True, "phoneNo": False}})
        blob = str(data)
        self.assertIn("'email': True", blob)
        self.assertNotIn("tester@", blob)


class PresenceReaderTests(unittest.TestCase):
    def test_reader_returns_booleans_only(self):
        real = {"profile.json": {"familyName": "SOMEONE", "firstName": "X", "email": "a@b.com"},
                "trip.json": {"arrivalDate": "2026/01/01"},
                "dynamic.json": {"symptoms": "NO"}}

        def loader(name):
            return real.get(name, {})

        out = tdac_diag.config_presence_reader(loader)()
        blob = str(out)
        self.assertIn("个人资料必填项齐全", blob)
        self.assertNotIn("SOMEONE", blob)
        self.assertNotIn("a@b.com", blob)
        self.assertNotIn("2026/01/01", blob)

    def test_reader_survives_broken_loader(self):
        def boom(name):
            raise RuntimeError("broken")

        out = tdac_diag.config_presence_reader(boom)()
        self.assertTrue(any(k.endswith("可读") for k in out))


class ExportTests(unittest.TestCase):
    def test_export_writes_self_checked_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = tdac_diag.export_diagnostic(os.path.join(d, "diag.txt"))
            self.assertTrue(os.path.exists(path))
            with open(path, "r", encoding="utf-8") as fh:
                content = fh.read()
            self.assertEqual(tdac_diag.scan_forbidden(content), [])


if __name__ == "__main__":
    unittest.main()
