import contextlib
import io
import os
import tempfile
import unittest

os.environ.setdefault("TDAC_DATA_DIR", os.path.join(tempfile.gettempdir(), "tdac-tests-data"))

import tdac_fill  # noqa: E402


class _El:
    def __init__(self, editable=True, value=""):
        self._editable = editable
        self.value = value

    def is_visible(self):
        return True

    def is_editable(self):
        return self._editable

    def click(self):
        pass

    def fill(self, v):
        self.value = v

    def type(self, v, delay=0):
        self.value += v

    def press(self, key):
        pass

    def input_value(self):
        return self.value


class _Loc:
    def __init__(self, els):
        self._els = els

    def count(self):
        return len(self._els)

    def nth(self, i):
        return self._els[i]


class _Page:
    def __init__(self, mapping):
        self._mapping = mapping

    def locator(self, selector, **kw):
        return _Loc(self._mapping.get(selector, []))


class PreviewEmailTests(unittest.TestCase):
    """B5：预览页收件邮箱——可靠识别才填；识别不到就暂停转人工（不算失败）。"""

    def setUp(self):
        self._buf = io.StringIO()
        self._ctx = contextlib.redirect_stdout(self._buf)
        self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)

    def _rep(self):
        return tdac_fill.Rep()

    def test_fills_and_reads_back_when_control_found(self):
        el = _El()
        page = _Page({'input[formcontrolname="email"]': [el]})
        rep = self._rep()
        try:
            res = tdac_fill.fill_preview_email(page, "tester@example.invalid", rep)
        finally:
            rep.close()
        self.assertTrue(res)
        self.assertEqual(el.value, "tester@example.invalid")

    def test_missing_control_pauses_for_manual_entry(self):
        page = _Page({})
        rep = self._rep()
        try:
            res = tdac_fill.fill_preview_email(page, "tester@example.invalid", rep)
        finally:
            rep.close()
        self.assertIsNone(res, "识别不到控件时应暂停（None），而不是判定失败")

    def test_invalid_email_is_a_real_failure(self):
        page = _Page({'input[formcontrolname="email"]': [_El()]})
        rep = self._rep()
        try:
            res = tdac_fill.fill_preview_email(page, "not-an-email", rep)
        finally:
            rep.close()
        self.assertFalse(res)

    def test_readonly_control_is_ignored(self):
        page = _Page({'input[formcontrolname="email"]': [_El(editable=False)]})
        rep = self._rep()
        try:
            res = tdac_fill.fill_preview_email(page, "tester@example.invalid", rep)
        finally:
            rep.close()
        self.assertIsNone(res)

    def test_readback_mismatch_pauses_without_crashing(self):
        class _Stubborn(_El):
            def fill(self, v):
                self.value = ""

            def type(self, v, delay=0):
                self.value = ""

        page = _Page({'input[type="email"]': [_Stubborn()]})
        rep = self._rep()
        try:
            res = tdac_fill.fill_preview_email(page, "tester@example.invalid", rep)
        finally:
            rep.close()
        self.assertIsNone(res)

    def test_preview_email_control_returns_none_when_absent(self):
        self.assertIsNone(tdac_fill.preview_email_control(_Page({})))


if __name__ == "__main__":
    unittest.main()
