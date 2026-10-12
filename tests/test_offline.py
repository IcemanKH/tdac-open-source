import contextlib
import io
import os
import sys
import tempfile
import unittest

os.environ.setdefault("TDAC_DATA_DIR", os.path.join(tempfile.gettempdir(), "tdac-tests-data"))

import tdac_fill  # noqa: E402
from tdac_synth import synthetic_config  # noqa: E402


class OfflineModeTests(unittest.TestCase):
    """A5 补强：--smoke / --dry-run 必须完全离线（不启动浏览器、不触网、不读真实配置）。"""

    def _run_main(self, argv):
        old = sys.argv
        sys.argv = ["tdac_fill.py"] + argv
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc = tdac_fill.main()
        finally:
            sys.argv = old
        return rc, buf.getvalue()

    def _assert_offline(self, argv):
        with tempfile.TemporaryDirectory() as root:
            old = os.environ.get("TDAC_DATA_DIR")
            os.environ["TDAC_DATA_DIR"] = root

            def boom(*a, **k):
                raise AssertionError("离线模式不得启动浏览器或访问官网")

            originals = {
                "preflight_env": tdac_fill.preflight_env,
                "launch_browser": tdac_fill.launch_browser,
                "open_form": tdac_fill.open_form,
            }
            tdac_fill.preflight_env = boom
            tdac_fill.launch_browser = boom
            tdac_fill.open_form = boom
            try:
                rc, out = self._run_main(argv)
            finally:
                for name, fn in originals.items():
                    setattr(tdac_fill, name, fn)
                if old is None:
                    os.environ.pop("TDAC_DATA_DIR", None)
                else:
                    os.environ["TDAC_DATA_DIR"] = old
            self.assertEqual(rc, 0, out)
            self.assertIn("离线检查", out)
            return out

    def test_smoke_is_fully_offline(self):
        self._assert_offline(["--smoke"])

    def test_dry_run_is_fully_offline(self):
        self._assert_offline(["--dry-run"])

    def test_offline_runner_rejects_non_synthetic_config(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = tdac_fill.run_offline({"synthetic": False}, tdac_fill.Rep(), mode="smoke")
        self.assertNotEqual(rc, 0)

    def test_synthetic_config_is_marked_synthetic(self):
        self.assertTrue(synthetic_config()["synthetic"])


if __name__ == "__main__":
    unittest.main()
