import base64
import os
import tempfile
import unittest

os.environ.setdefault("TDAC_DATA_DIR", os.path.join(tempfile.gettempdir(), "tdac-tests-data"))

from tdac_fill import export_preview_pdf  # noqa: E402


class _CdpSession:
    def send(self, method, params):
        return {"data": base64.b64encode(b"%PDF-1.7 optin").decode("ascii")}


class _Context:
    def new_cdp_session(self, page):
        return _CdpSession()


class _Page:
    def __init__(self):
        self.context = _Context()
        self.evaluations = []

    def wait_for_timeout(self, timeout_ms):
        pass

    def wait_for_function(self, expression, timeout=0):
        pass

    def evaluate(self, script):
        self.evaluations.append(script)


class PdfOptInTests(unittest.TestCase):
    def test_default_path_uses_output_dir(self):
        with tempfile.TemporaryDirectory() as folder:
            path = export_preview_pdf(_Page(), output_dir=folder, stamp="20261010-000000")
            self.assertTrue(path.endswith("TDAC_预览_20261010-000000.pdf"))
            self.assertEqual(os.path.dirname(os.path.abspath(path)), os.path.abspath(folder))
            self.assertTrue(os.path.exists(path))

    def test_explicit_user_path_is_respected(self):
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "nested", "my-preview.pdf")
            path = export_preview_pdf(_Page(), path=target)
            self.assertEqual(os.path.abspath(path), os.path.abspath(target))
            self.assertTrue(os.path.exists(target))


if __name__ == "__main__":
    unittest.main()
