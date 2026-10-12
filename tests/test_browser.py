import os
import tempfile
import unittest

os.environ.setdefault("TDAC_DATA_DIR", os.path.join(tempfile.gettempdir(), "tdac-tests-data"))

import tdac_browser  # noqa: E402


class DetectionTests(unittest.TestCase):
    def test_detect_returns_known_kind_and_existing_path(self):
        kind, path = tdac_browser.detect_browser()
        if path is None:
            self.assertIsNone(kind)
            return
        self.assertIn(kind, ("Chrome", "Edge"))
        self.assertTrue(os.path.exists(path))

    def test_find_browser_matches_detect(self):
        self.assertEqual(tdac_browser.find_browser(), tdac_browser.detect_browser()[1])

    def test_only_supported_browsers_declared(self):
        kinds = {k for k, _ in tdac_browser.CANDIDATES}
        self.assertEqual(kinds, {"Chrome", "Edge"})
        for _k, p in tdac_browser.CANDIDATES:
            self.assertTrue(p.lower().endswith(("chrome.exe", "msedge.exe")))


class MissingBrowserGuidanceTests(unittest.TestCase):
    def test_help_mentions_official_links_and_no_auto_install(self):
        text = tdac_browser.missing_browser_help()
        self.assertIn(tdac_browser.DOWNLOAD_PAGES["Chrome"], text)
        self.assertIn(tdac_browser.DOWNLOAD_PAGES["Edge"], text)
        self.assertIn("不会自动安装", text)
        self.assertIn("默认浏览器", text)


class SwitchSafetyTests(unittest.TestCase):
    """B7：绝不使用会扩大远程调试访问范围的启动参数。"""

    def test_safe_switches_pass(self):
        tdac_browser.assert_safe_switches(
            ["chrome.exe", "--remote-debugging-port=12345", "--user-data-dir=C:\\x"])

    def test_remote_allow_origins_rejected(self):
        with self.assertRaises(ValueError):
            tdac_browser.assert_safe_switches(["chrome.exe", "--remote-allow-origins=*"])

    def test_remote_debugging_address_rejected(self):
        with self.assertRaises(ValueError):
            tdac_browser.assert_safe_switches(["chrome.exe", "--remote-debugging-address=0.0.0.0"])

    def test_disable_web_security_rejected(self):
        with self.assertRaises(ValueError):
            tdac_browser.assert_safe_switches(["chrome.exe", "--disable-web-security"])

    def test_unsafe_switches_lists_all_hits(self):
        hits = tdac_browser.unsafe_switches(
            ["x", "--remote-allow-origins=*", "--disable-web-security"])
        self.assertEqual(len(hits), 2)

    def test_real_launch_args_are_safe(self):
        """与 tdac_fill.launch_browser 实际使用的参数集保持一致的只读校验。"""
        import tdac_fill
        args = [
            "chrome.exe",
            "--remote-debugging-port=50000",
            "--user-data-dir=" + tdac_fill.PROFILE_DIR,
            "--no-first-run", "--no-default-browser-check",
            "--disable-features=Translate", "--start-maximized", "about:blank",
        ]
        tdac_browser.assert_safe_switches(args)
        self.assertEqual(tdac_browser.unsafe_switches(args), [])


class SessionNotesTests(unittest.TestCase):
    def test_notes_cover_required_topics(self):
        text = tdac_browser.SESSION_NOTES
        for phrase in ("独立用户目录", "回环", "127.0.0.1", "随机", "人工接管",
                       "主动结束", "残留", "扩大远程调试访问范围"):
            self.assertIn(phrase, text)

    def test_leftover_markers_detected_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(tdac_browser.leftover_markers(d), [])
            with open(os.path.join(d, "SingletonLock"), "w", encoding="utf-8") as fh:
                fh.write("x")
            self.assertEqual(tdac_browser.leftover_markers(d), ["SingletonLock"])

    def test_describe_leftovers_reports_clean_state(self):
        with tempfile.TemporaryDirectory() as d:
            info = tdac_browser.detect_leftovers(d)
            self.assertEqual(info["markers"], [])
            self.assertIn("未发现", tdac_browser.describe_leftovers(info))

    def test_describe_leftovers_mentions_profile_markers(self):
        info = {"profile_dir": "x", "markers": ["DevToolsActivePort"], "pids": []}
        self.assertIn("DevToolsActivePort", tdac_browser.describe_leftovers(info))

    def test_list_stale_processes_returns_list(self):
        self.assertIsInstance(tdac_browser.list_stale_processes("C:\\nonexistent-dir-xyz"), list)


if __name__ == "__main__":
    unittest.main()
