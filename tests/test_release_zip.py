# -*- coding: utf-8 -*-
"""发行 ZIP 打包工具的发布顺序回归测试。

覆盖 ``tools/make_release_zip.py`` 的关键安全属性：**只有**在临时 ZIP 通过完整
回读自检之后，才允许替换正式 ZIP 与它的 ``.sha256``；自检失败时必须保留原有有效
ZIP 与校验文件，且不留下任何半成品。
"""
from pathlib import Path
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
_BUILD = ROOT / ".build"


def _temp_root() -> Path:
    """测试临时目录根：优先项目内 ``.build``。

    公开仓库检出中**不存在** ``.build``（由 .gitignore 排除），因此这里必须先创建，
    不能假设它已存在。
    """
    _BUILD.mkdir(parents=True, exist_ok=True)
    return _BUILD


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_dist(dist: Path, version: str) -> None:
    """构造一份最小的「同次构建产物」：onedir + BUILD_INFO.json + SHA256SUMS.txt。"""
    app = dist / "TDAC填写助手"
    (app / "_internal").mkdir(parents=True)
    exe = app / "TDAC填写助手.exe"
    exe.write_bytes(b"synthetic-exe-bytes")
    (app / "_internal" / "data.txt").write_bytes(bytes(range(256)) * 4)
    info = {
        "appVersion": version,
        "artifact": {
            "exe": "TDAC填写助手/TDAC填写助手.exe",
            "exeSha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
        },
        "buildTimeUtc": "2026-10-12T00:00:00Z",
        "vcs": {"available": False},
    }
    (dist / "BUILD_INFO.json").write_text(
        json.dumps(info, ensure_ascii=False), encoding="utf-8")
    sums = dist / "SHA256SUMS.txt"
    _load("build_portable.py", "tdac_build_portable_for_zip_tests")._write_sums(
        str(dist), str(sums), skip=str(sums))


def _leftovers(out: Path):
    """打包失败后目录里不得残留任何临时/备份文件（``.part`` / ``.part.bak`` / ``.bak``）。"""
    return sorted(p.name for p in out.iterdir()
                  if ".part" in p.name or p.name.endswith(".bak"))


class ReleaseZipOrderingTests(unittest.TestCase):
    def setUp(self):
        self.zipmod = _load("tools/make_release_zip.py", "tdac_make_release_zip")

    def test_failed_verify_keeps_previous_zip_and_sidecar(self):
        """故障注入：打包后产物被改动，自检失败 -> 原 ZIP 与原 .sha256 必须仍然有效。"""
        m = self.zipmod
        version = m.app_version()
        zip_name = m.zip_basename(version)
        with tempfile.TemporaryDirectory(dir=_temp_root()) as temp:
            temp = Path(temp)
            dist, out = temp / "dist", temp / "out"
            _make_dist(dist, version)
            out.mkdir()

            old_bytes = b"OLD-VALID-ZIP-CONTENT"
            old_zip = out / zip_name
            old_side = out / (zip_name + ".sha256")
            old_zip.write_bytes(old_bytes)
            old_digest = hashlib.sha256(old_bytes).hexdigest()
            old_side.write_text(f"{old_digest}  {zip_name}\n",
                                encoding="utf-8", newline="\n")

            # 注入故障：在临时 ZIP 写出之后改动 onedir 内的文件，真实 verify_zip 必然
            # 报「ZIP 内内容与磁盘不一致」，从而走失败分支。
            real_build = m.build_zip
            victim = dist / "TDAC填写助手" / "_internal" / "data.txt"
            original = victim.read_bytes()

            def corrupting_build(*args, **kwargs):
                tmp = real_build(*args, **kwargs)
                victim.write_bytes(bytes(b ^ 0xFF for b in original))  # 同长度、异内容
                return tmp

            with mock.patch.object(m, "build_zip", corrupting_build):
                with contextlib.redirect_stdout(io.StringIO()) as out_text:
                    rc = m.main(["--dist", str(dist), "--out", str(out)])
            self.assertIn("[FAIL]", out_text.getvalue())

            self.assertEqual(rc, m.EXIT_MISMATCH, "自检失败必须返回非零退出码")
            # 原正式 ZIP 与原校验文件必须一字未动。
            self.assertEqual(old_zip.read_bytes(), old_bytes)
            self.assertEqual(old_side.read_text(encoding="utf-8"),
                             f"{old_digest}  {zip_name}\n")
            # 且原校验文件仍然自洽（仍然「有效」）。
            self.assertEqual(hashlib.sha256(old_zip.read_bytes()).hexdigest(), old_digest)
            # 不得留下任何半成品 ZIP / 校验文件。
            self.assertEqual(_leftovers(out), [], "失败后残留临时文件")

    def test_success_promotes_zip_and_matching_sidecar(self):
        """正常路径：打包成功后才出现正式 ZIP 与指向它的、自洽的 .sha256。"""
        m = self.zipmod
        version = m.app_version()
        zip_name = m.zip_basename(version)
        with tempfile.TemporaryDirectory(dir=_temp_root()) as temp:
            temp = Path(temp)
            dist, out = temp / "dist", temp / "out"
            _make_dist(dist, version)

            with contextlib.redirect_stdout(io.StringIO()):
                rc = m.main(["--dist", str(dist), "--out", str(out)])

            self.assertEqual(rc, m.EXIT_OK)
            zip_path = out / zip_name
            side = out / (zip_name + ".sha256")
            self.assertTrue(zip_path.is_file())
            digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
            self.assertEqual(side.read_text(encoding="utf-8"), f"{digest}  {zip_name}\n")
            # 4 个文件条目：BUILD_INFO.json + SHA256SUMS.txt + EXE + _internal/data.txt
            self.assertEqual(m.verify_zip(str(zip_path), str(dist))["files"], 4)
            self.assertEqual(_leftovers(out), [])

    def test_sidecar_replace_failure_rolls_back_to_previous_zip(self):
        """故障注入：正式 ZIP **已替换**、``.sha256`` 替换失败 -> 必须回滚到旧 ZIP 与旧校验。

        这正是「两次 os.replace() 不构成原子事务」的窗口：第 ④ 步失败后磁盘上会短暂
        出现「新 ZIP + 旧校验」。本用例断言补偿回滚把状态收敛回「旧 ZIP + 旧校验」，
        且不残留 ``.part`` / ``.bak``。
        """
        m = self.zipmod
        version = m.app_version()
        zip_name = m.zip_basename(version)
        with tempfile.TemporaryDirectory(dir=_temp_root()) as temp:
            temp = Path(temp)
            dist, out = temp / "dist", temp / "out"
            _make_dist(dist, version)
            out.mkdir()

            old_bytes = b"OLD-VALID-ZIP-CONTENT"
            old_zip = out / zip_name
            old_side = out / (zip_name + ".sha256")
            old_zip.write_bytes(old_bytes)
            old_digest = hashlib.sha256(old_bytes).hexdigest()
            old_side.write_text(f"{old_digest}  {zip_name}\n",
                                encoding="utf-8", newline="\n")

            # 只让「替换 .sha256」那一步失败（src 形如 <zip>.sha256.part），其余放行。
            real_replace = os.replace
            calls = {"promote": 0, "side": 0, "rollback": 0}

            def flaky_replace(src, dst, *args, **kwargs):
                src, dst = str(src), str(dst)
                if src.endswith(".zip.sha256.part"):
                    calls["side"] += 1
                    raise OSError("模拟：替换 .sha256 失败")
                if src.endswith(".zip.part") and dst.endswith(".zip"):
                    calls["promote"] += 1          # ③ 正式 ZIP 已替换 ← 本用例要覆盖的窗口
                elif src.endswith(".bak") and dst.endswith(".zip"):
                    calls["rollback"] += 1         # ⑤ 补偿回滚
                return real_replace(src, dst, *args, **kwargs)

            with mock.patch.object(m.os, "replace", flaky_replace):
                with contextlib.redirect_stdout(io.StringIO()) as out_text:
                    rc = m.main(["--dist", str(dist), "--out", str(out)])

            self.assertEqual(calls["promote"], 1, "正式 ZIP 应先被替换（这正是本用例要覆盖的窗口）")
            self.assertEqual(calls["side"], 1, "校验文件替换必须被尝试过")
            self.assertEqual(calls["rollback"], 1, "必须对正式 ZIP 执行补偿回滚")
            self.assertIn("[FAIL]", out_text.getvalue())
            self.assertEqual(rc, m.EXIT_MISMATCH)
            # 回滚后必须仍是旧 ZIP + 旧校验，且二者自洽。
            self.assertEqual(old_zip.read_bytes(), old_bytes)
            self.assertEqual(old_side.read_text(encoding="utf-8"),
                             f"{old_digest}  {zip_name}\n")
            self.assertEqual(hashlib.sha256(old_zip.read_bytes()).hexdigest(), old_digest)
            self.assertEqual(_leftovers(out), [], "回滚后不得残留临时/备份文件")

    def test_sidecar_replace_failure_without_previous_zip_leaves_nothing(self):
        """同一窗口、但原本没有旧 ZIP：回滚应删除新 ZIP，不得留下无校验文件的孤儿 ZIP。"""
        m = self.zipmod
        version = m.app_version()
        zip_name = m.zip_basename(version)
        with tempfile.TemporaryDirectory(dir=_temp_root()) as temp:
            temp = Path(temp)
            dist, out = temp / "dist", temp / "out"
            _make_dist(dist, version)
            out.mkdir()

            real_replace = os.replace

            def flaky_replace(src, dst, *args, **kwargs):
                if str(src).endswith(".zip.sha256.part"):
                    raise OSError("模拟：替换 .sha256 失败")
                return real_replace(src, dst, *args, **kwargs)

            with mock.patch.object(m.os, "replace", flaky_replace):
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = m.main(["--dist", str(dist), "--out", str(out)])

            self.assertEqual(rc, m.EXIT_MISMATCH)
            self.assertFalse((out / zip_name).exists(), "不得留下孤儿 ZIP")
            self.assertFalse((out / (zip_name + ".sha256")).exists())
            self.assertEqual(_leftovers(out), [])


if __name__ == "__main__":
    unittest.main()
