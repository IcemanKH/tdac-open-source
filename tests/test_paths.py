import os
import shutil
import subprocess
import tempfile
import unittest

os.environ.setdefault("TDAC_DATA_DIR", os.path.join(tempfile.gettempdir(), "tdac-tests-data"))

import tdac_paths  # noqa: E402


class _EnvSandbox:
    """临时把 TDAC_DATA_DIR 指向一个受控临时目录，退出时还原。"""

    def __init__(self, root):
        self.root = root
        self._old = os.environ.get("TDAC_DATA_DIR")

    def __enter__(self):
        os.environ["TDAC_DATA_DIR"] = self.root
        return self.root

    def __exit__(self, *exc):
        if self._old is None:
            os.environ.pop("TDAC_DATA_DIR", None)
        else:
            os.environ["TDAC_DATA_DIR"] = self._old


def _make_junction(link, target) -> bool:
    """尽力创建 Windows 目录联接；无权限时返回 False（测试跳过）。"""
    try:
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                           capture_output=True, errors="ignore")
        return r.returncode == 0 and os.path.exists(link)
    except Exception:
        return False


class DataDirSeparationTests(unittest.TestCase):
    def test_data_root_is_outside_program_dir(self):
        root = os.path.abspath(tdac_paths.data_root())
        prog = os.path.abspath(tdac_paths.PROGRAM_DIR)
        self.assertFalse(root == prog or root.startswith(prog + os.sep),
                         "用户数据目录不得位于程序目录内")

    def test_managed_dirs_are_under_data_root(self):
        root = os.path.abspath(tdac_paths.data_root())
        for path in (tdac_paths.config_dir(), tdac_paths.secrets_dir(),
                     tdac_paths.profile_dir(), tdac_paths.log_dir(), tdac_paths.output_dir()):
            self.assertTrue(os.path.abspath(path).startswith(root + os.sep))

    def test_secrets_path_is_under_secrets_dir(self):
        self.assertEqual(os.path.dirname(tdac_paths.secrets_path()), tdac_paths.secrets_dir())
        self.assertTrue(tdac_paths.secrets_path().endswith("secrets.dat"))

    def test_delete_requires_explicit_confirmation(self):
        with self.assertRaises(PermissionError):
            tdac_paths.delete_managed_data(confirmed=False)

    def test_delete_refuses_root_inside_program_dir(self):
        old = os.environ.get("TDAC_DATA_DIR")
        try:
            os.environ["TDAC_DATA_DIR"] = os.path.join(tdac_paths.PROGRAM_DIR, "data")
            with self.assertRaises(PermissionError):
                tdac_paths.delete_managed_data(confirmed=True)
        finally:
            if old is None:
                os.environ.pop("TDAC_DATA_DIR", None)
            else:
                os.environ["TDAC_DATA_DIR"] = old

    def test_delete_refuses_program_dir_exactly(self):
        with _EnvSandbox(tdac_paths.PROGRAM_DIR):
            with self.assertRaises(PermissionError):
                tdac_paths.delete_managed_data(confirmed=True)

    def test_delete_refuses_drive_root(self):
        drive_root = os.path.splitdrive(os.path.abspath(os.sep))[0] + os.sep
        with _EnvSandbox(drive_root):
            ok, _ = tdac_paths.validate_data_root(drive_root)
            self.assertFalse(ok, "磁盘根目录必须被拒绝")
            with self.assertRaises(PermissionError):
                tdac_paths.delete_managed_data(confirmed=True)


class DeleteSafetyTests(unittest.TestCase):
    def test_delete_removes_only_managed_subdirs(self):
        with tempfile.TemporaryDirectory() as root:
            with _EnvSandbox(root):
                os.makedirs(os.path.join(root, "config"))
                os.makedirs(os.path.join(root, "logs"))
                stray = os.path.join(root, "keepme.txt")
                with open(stray, "w", encoding="utf-8") as fh:
                    fh.write("keep")
                removed = tdac_paths.delete_managed_data(confirmed=True)
                self.assertFalse(os.path.exists(os.path.join(root, "config")))
                self.assertFalse(os.path.exists(os.path.join(root, "logs")))
                self.assertTrue(os.path.exists(stray), "非受管文件不得被删除")
                self.assertTrue(os.path.isdir(root), "数据根目录本身不得被删除")
                self.assertEqual(len(removed), 2)

    def test_delete_reports_error_instead_of_silently_ignoring(self):
        with tempfile.TemporaryDirectory() as root:
            with _EnvSandbox(root):
                os.makedirs(os.path.join(root, "config"))
                original = tdac_paths._rmtree_strict

                def boom(path):
                    raise OSError("simulated failure")

                tdac_paths._rmtree_strict = boom
                try:
                    with self.assertRaises(RuntimeError):
                        tdac_paths.delete_managed_data(confirmed=True)
                finally:
                    tdac_paths._rmtree_strict = original
                self.assertTrue(os.path.exists(os.path.join(root, "config")),
                                "删除失败时目标应仍然存在")

    def test_delete_refuses_reparse_point_subdir(self):
        with tempfile.TemporaryDirectory() as root:
            with tempfile.TemporaryDirectory() as outside:
                with _EnvSandbox(root):
                    link = os.path.join(root, "logs")
                    if not _make_junction(link, outside):
                        self.skipTest("当前环境无法创建目录联接（junction）")
                    self.assertTrue(tdac_paths.is_reparse_point(link))
                    with self.assertRaises(RuntimeError):
                        tdac_paths.delete_managed_data(confirmed=True)
                    self.assertTrue(os.path.isdir(outside),
                                    "联接指向的外部目录不得被删除")

    def test_is_reparse_point_false_for_normal_dir(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(tdac_paths.is_reparse_point(root))

    def test_realpath_resolves_containment(self):
        with tempfile.TemporaryDirectory() as root:
            nested = os.path.join(root, "a", "b")
            os.makedirs(nested)
            self.assertTrue(tdac_paths._is_inside(nested, root))
            self.assertFalse(tdac_paths._is_inside(root, nested))

    def test_validate_data_root_accepts_temp_dir(self):
        with tempfile.TemporaryDirectory() as root:
            ok, reason = tdac_paths.validate_data_root(root)
            self.assertTrue(ok, reason)

    def test_delete_refuses_source_checkout_dir(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "tdac_fill.py"), "w", encoding="utf-8") as fh:
                fh.write("# fake source\n")
            os.makedirs(os.path.join(root, "config"))
            with _EnvSandbox(root):
                ok, _ = tdac_paths.validate_data_root(root)
                self.assertFalse(ok, "含程序源码的目录不得作为数据根目录")
                with self.assertRaises(PermissionError):
                    tdac_paths.delete_managed_data(confirmed=True)
                self.assertTrue(os.path.exists(os.path.join(root, "config")),
                                "被拒绝时不得删除任何内容")


if __name__ == "__main__":
    unittest.main()
