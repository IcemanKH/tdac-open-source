import os
import tempfile
import unittest

os.environ.setdefault("TDAC_DATA_DIR", os.path.join(tempfile.gettempdir(), "tdac-tests-data"))

import tdac_fill  # noqa: E402
from tdac_synth import synthetic_config  # noqa: E402


class SyntheticIsolationTests(unittest.TestCase):
    def test_smoke_mode_never_reads_real_config(self):
        """A5：smoke 必须使用完全合成资料，绝不能读取真实 config/。"""
        def boom(*args, **kwargs):
            raise AssertionError("smoke 模式不得读取真实配置文件")

        original = tdac_fill._read_json
        tdac_fill._read_json = boom
        try:
            cfg = tdac_fill.load_config(smoke=True)
        finally:
            tdac_fill._read_json = original
        self.assertTrue(cfg["synthetic"])
        self.assertEqual(cfg["profile"]["passportNo"], "E00000000")
        self.assertEqual(cfg["profile"]["phoneNo"], "13000000000")

    def test_non_smoke_is_marked_not_synthetic(self):
        original = tdac_fill._read_json
        tdac_fill._read_json = lambda *a, **k: ({}, {}, {})
        try:
            cfg = tdac_fill.load_config(smoke=False)
        finally:
            tdac_fill._read_json = original
        self.assertFalse(cfg["synthetic"])

    def test_synthetic_email_uses_reserved_invalid_domain(self):
        cfg = synthetic_config()
        self.assertTrue(cfg["profile"]["email"].endswith(".invalid"))
        self.assertTrue(cfg["dynamic"]["receiverEmail"].endswith(".invalid"))

    def test_synthetic_trip_is_fully_populated(self):
        cfg = synthetic_config()
        for key, value in cfg["trip"].items():
            self.assertTrue(str(value).strip(), f"合成行程字段 {key} 不应为空")


if __name__ == "__main__":
    unittest.main()
