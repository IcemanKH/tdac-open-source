import json
import os
import tempfile
import unittest

os.environ.setdefault("TDAC_DATA_DIR", os.path.join(tempfile.gettempdir(), "tdac-tests-data"))

import tdac_update  # noqa: E402


class VersionParseTests(unittest.TestCase):
    def test_parses_plain_and_v_prefixed(self):
        self.assertEqual(tdac_update.parse_version("1.2.3"), (1, 2, 3))
        self.assertEqual(tdac_update.parse_version("v1.2.3"), (1, 2, 3))

    def test_suffix_is_ignored(self):
        self.assertEqual(tdac_update.parse_version("1.2.3-beta.1"), (1, 2, 3))

    def test_is_newer(self):
        self.assertTrue(tdac_update.is_newer("0.2.0", "0.1.0"))
        self.assertTrue(tdac_update.is_newer("1.0.0", "0.9.9"))
        self.assertFalse(tdac_update.is_newer("0.1.0", "0.1.0"))
        self.assertFalse(tdac_update.is_newer("0.0.9", "0.1.0"))


class UpdateSourceConfigTests(unittest.TestCase):
    def test_default_source_is_empty_never_fabricated(self):
        """仓库尚未建立：默认版本来源必须为空，不得预置任何虚构地址。"""
        cfg = tdac_update.load_update_config()
        self.assertEqual(cfg["versionSource"], "")
        self.assertEqual(cfg["downloadPage"], "")

    def test_env_override_is_respected(self):
        old = os.environ.get("TDAC_UPDATE_SOURCE")
        os.environ["TDAC_UPDATE_SOURCE"] = "https://example.invalid/version.json"
        try:
            cfg = tdac_update.load_update_config()
        finally:
            if old is None:
                os.environ.pop("TDAC_UPDATE_SOURCE", None)
            else:
                os.environ["TDAC_UPDATE_SOURCE"] = old
        self.assertEqual(cfg["versionSource"], "https://example.invalid/version.json")

    def test_config_file_is_read(self):
        with tempfile.TemporaryDirectory() as root:
            os.environ["TDAC_DATA_DIR"] = root
            old_env = os.environ.pop("TDAC_UPDATE_SOURCE", None)
            try:
                os.makedirs(os.path.join(root, "config"), exist_ok=True)
                with open(os.path.join(root, "config", "update.json"), "w", encoding="utf-8") as fh:
                    json.dump({"versionSource": "https://example.invalid/v.json",
                               "downloadPage": "https://example.invalid/dl"}, fh)
                cfg = tdac_update.load_update_config()
            finally:
                if old_env is not None:
                    os.environ["TDAC_UPDATE_SOURCE"] = old_env
            self.assertEqual(cfg["versionSource"], "https://example.invalid/v.json")
            self.assertEqual(cfg["downloadPage"], "https://example.invalid/dl")

    def test_corrupt_config_does_not_crash(self):
        with tempfile.TemporaryDirectory() as root:
            old = os.environ.get("TDAC_DATA_DIR")
            os.environ["TDAC_DATA_DIR"] = root
            os.environ.pop("TDAC_UPDATE_SOURCE", None)
            try:
                os.makedirs(os.path.join(root, "config"), exist_ok=True)
                with open(os.path.join(root, "config", "update.json"), "w", encoding="utf-8") as fh:
                    fh.write("{ not json ")
                cfg = tdac_update.load_update_config()
            finally:
                if old is None:
                    os.environ.pop("TDAC_DATA_DIR", None)
                else:
                    os.environ["TDAC_DATA_DIR"] = old
            self.assertEqual(cfg["versionSource"], "")


class UpdateCheckTests(unittest.TestCase):
    def test_without_consent_no_request_is_made(self):
        calls = []
        res = tdac_update.check_for_update(
            consent=False, source="https://example.invalid/v.json",
            fetch=lambda u: calls.append(u) or "{}")
        self.assertFalse(res["checked"])
        self.assertEqual(calls, [], "未同意时不得发起任何请求")

    def test_unconfigured_source_reports_and_skips(self):
        res = tdac_update.check_for_update(consent=True, source="", fetch=lambda u: "{}")
        self.assertFalse(res["checked"])
        self.assertIn("未配置", res["reason"])

    def test_non_https_source_rejected(self):
        res = tdac_update.check_for_update(consent=True, source="http://x/v.json",
                                          fetch=lambda u: "{}")
        self.assertFalse(res["checked"])
        self.assertIn("https", res["reason"])

    def test_newer_version_detected_with_download_page(self):
        payload = json.dumps({"version": "9.9.9", "downloadPage": "https://example.invalid/dl"})
        res = tdac_update.check_for_update(consent=True, current="0.1.0",
                                          source="https://example.invalid/v.json",
                                          fetch=lambda u: payload)
        self.assertTrue(res["checked"])
        self.assertTrue(res["update_available"])
        self.assertEqual(res["latest"], "9.9.9")
        self.assertEqual(res["downloadPage"], "https://example.invalid/dl")
        self.assertIn("不会自动下载", tdac_update.describe(res))

    def test_insecure_download_page_is_dropped(self):
        payload = json.dumps({"version": "9.9.9", "downloadPage": "http://evil.invalid/x"})
        res = tdac_update.check_for_update(consent=True, current="0.1.0",
                                          source="https://example.invalid/v.json",
                                          fetch=lambda u: payload)
        self.assertNotEqual(res["downloadPage"], "http://evil.invalid/x")

    def test_same_version_reports_up_to_date(self):
        payload = json.dumps({"version": "0.1.0"})
        res = tdac_update.check_for_update(consent=True, current="0.1.0",
                                          source="https://example.invalid/v.json",
                                          fetch=lambda u: payload)
        self.assertTrue(res["checked"])
        self.assertFalse(res["update_available"])
        self.assertIn("最新", tdac_update.describe(res))

    def test_bad_payload_is_handled(self):
        for payload in ("not json", "[]", '{"nope": 1}'):
            res = tdac_update.check_for_update(consent=True, source="https://example.invalid/v.json",
                                              fetch=lambda u, p=payload: p)
            self.assertFalse(res["checked"])
            self.assertTrue(res["reason"])


if __name__ == "__main__":
    unittest.main()
