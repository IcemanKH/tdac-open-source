import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault("TDAC_DATA_DIR", os.path.join(tempfile.gettempdir(), "tdac-tests-data"))

import tdac_fill  # noqa: E402
import tdac_paths  # noqa: E402
import tdac_sec  # noqa: E402


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


class EncryptionRoundTripTests(unittest.TestCase):
    """验证资料加密（DPAPI）：可加密 / 可解密 / 落盘为密文 / 可局部更新。"""

    def test_protect_unprotect_roundtrip(self):
        probe = b"roundtrip-" + os.urandom(6).hex().encode()
        self.assertEqual(tdac_sec.unprotect_bytes(tdac_sec.protect_bytes(probe)), probe)

    def test_saved_file_is_not_plaintext(self):
        with tempfile.TemporaryDirectory() as root, _TmpDataDir(root):
            path = tdac_sec.save_secrets({"passportNo": "E00000000", "phoneNo": "13000000000"})
            with open(path, "rb") as fh:
                raw = fh.read()
            self.assertNotIn(b"E00000000", raw)
            self.assertNotIn(b"13000000000", raw)
            self.assertNotIn(b"passportNo", raw)

    def test_partial_update_preserves_other_field(self):
        """“修改”语义：只改一个字段时不得丢掉另一个。"""
        with tempfile.TemporaryDirectory() as root, _TmpDataDir(root):
            tdac_sec.save_secrets({"passportNo": "E00000000", "phoneNo": "13000000000"})
            tdac_sec.save_secrets({"passportNo": "E99999999"})
            data = tdac_sec.load_secrets()
            self.assertEqual(data["passportNo"], "E99999999")
            self.assertEqual(data["phoneNo"], "13000000000")

    def test_corrupt_ciphertext_raises_instead_of_silently_empty(self):
        with tempfile.TemporaryDirectory() as root, _TmpDataDir(root):
            path = tdac_sec.save_secrets({"passportNo": "E00000000"})
            with open(path, "wb") as fh:
                fh.write(b"not a valid dpapi blob")
            with self.assertRaises(Exception):
                tdac_sec.load_secrets()


class UpgradeRetentionTests(unittest.TestCase):
    """升级保留：数据在程序目录之外，且首次生成不会覆盖已有配置。"""

    def test_data_root_is_outside_program_dir(self):
        real_root = tdac_paths.realpath(tdac_paths.data_root())
        real_prog = tdac_paths.realpath(tdac_paths.PROGRAM_DIR)
        self.assertFalse(real_root == real_prog or real_root.startswith(real_prog + os.sep))

    def test_ensure_user_config_never_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as root, _TmpDataDir(root):
            created_first = tdac_paths.ensure_user_config()
            self.assertTrue(created_first, "首次运行应生成用户配置")
            marker = os.path.join(tdac_paths.config_dir(), "profile.json")
            with open(marker, "w", encoding="utf-8") as fh:
                json.dump({"familyName": "KEEPME"}, fh)
            created_again = tdac_paths.ensure_user_config()
            self.assertEqual(created_again, [], "第二次运行不得再生成/覆盖")
            with open(marker, "r", encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["familyName"], "KEEPME")


class ConfigErrorRecoveryTests(unittest.TestCase):
    """错误恢复：配置损坏时应报告并退出，而不是崩溃或误开浏览器。"""

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

    def test_corrupt_profile_json_is_reported_not_crashed(self):
        with tempfile.TemporaryDirectory() as root, _TmpDataDir(root):
            os.makedirs(tdac_paths.config_dir(), exist_ok=True)
            with open(os.path.join(tdac_paths.config_dir(), "profile.json"), "w",
                      encoding="utf-8") as fh:
                fh.write("{ this is not json ")

            def boom(*a, **k):
                raise AssertionError("配置损坏时不得打开浏览器")

            originals = {n: getattr(tdac_fill, n)
                         for n in ("preflight_env", "launch_browser", "open_form")}
            for n in originals:
                setattr(tdac_fill, n, boom)
            try:
                rc, out = self._run_main(["--check"])
            finally:
                for n, fn in originals.items():
                    setattr(tdac_fill, n, fn)
            self.assertNotEqual(rc, 0)
            self.assertIn("读取配置失败", out)

    def test_corrupt_json_raises_from_loader(self):
        with tempfile.TemporaryDirectory() as root, _TmpDataDir(root):
            os.makedirs(tdac_paths.config_dir(), exist_ok=True)
            with open(os.path.join(tdac_paths.config_dir(), "profile.json"), "w",
                      encoding="utf-8") as fh:
                fh.write("nope")
            with self.assertRaises(Exception):
                tdac_fill.load_config(smoke=False)


if __name__ == "__main__":
    unittest.main()
