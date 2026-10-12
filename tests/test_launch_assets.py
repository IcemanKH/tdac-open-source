from pathlib import Path
import importlib.util
import os
import tempfile
import unittest
import warnings


ROOT = Path(__file__).resolve().parents[1]


class LaunchAndDocsTests(unittest.TestCase):
    def test_one_click_bat_is_self_locating_and_runs_main_program(self):
        path = ROOT / "一键启动TDAC.bat"
        self.assertTrue(path.exists())
        text = path.read_text(encoding="utf-8-sig")
        self.assertIn('cd /d "%~dp0"', text)
        self.assertIn('.venv\\Scripts\\python.exe', text)
        self.assertIn('tdac_fill.py %*', text)
        self.assertIn('PYTHONUTF8=1', text)

    def test_project_status_document_covers_flow_outputs_and_boundaries(self):
        path = ROOT / "项目全貌与当前状态.md"
        self.assertTrue(path.exists())
        text = path.read_text(encoding="utf-8")
        for phrase in (
            "项目目标与边界",
            "完整运行逻辑",
            "健康声明",
            "PDF",
            "必填信息表",
            "不会自动提交",
            "团体同行",
            "当前状态",
        ):
            self.assertIn(phrase, text)

    def test_ignore_rules_cover_sensitive_and_build_artifacts(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in ("config/profile.json", "secrets/", "browser_profile/",
                        "logs/", "*.png", "*.dat", ".build/", "*.spec",
                        "docs/superpowers/"):
            self.assertIn(pattern, text)

    def test_public_files_do_not_reference_private_absolute_path(self):
        """公开候选文件中不得出现原始私有项目的本机绝对路径。

        docs/superpowers/ 为内部设计与实施计划资料，已由 .gitignore 排除，
        不随公开仓库或发行包发布，因此不在本检查范围内。
        """
        private = "D:" + chr(92) + "入境卡填写"
        prune = {".build", ".venv", "__pycache__", ".git", "dist", "build"}
        exts = {".md", ".txt", ".json", ".py", ".csv", ".yml", ".yaml", ".bat", ".cmd"}
        offenders = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if set(path.parts) & prune:
                continue
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith("docs/superpowers/"):
                continue
            if path.suffix.lower() not in exts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if private in text:
                offenders.append(rel)
        self.assertEqual(offenders, [], f"以下公开文件泄露了私有绝对路径：{offenders}")

    def test_portable_entry_defaults_to_gui_and_hides_console(self):
        path = ROOT / "tdac_app.py"
        self.assertTrue(path.exists())
        text = path.read_text(encoding="utf-8")
        self.assertIn("tdac_gui", text)
        self.assertIn("ShowWindow", text)
        self.assertIn("_setup_console_encoding", text)

    def test_build_script_targets_pyinstaller_onedir_with_playwright(self):
        path = ROOT / "build_portable.py"
        self.assertTrue(path.exists())
        text = path.read_text(encoding="utf-8")
        self.assertIn("--onedir", text)
        self.assertIn("--collect-all", text)
        self.assertIn("playwright", text)
        self.assertIn(".build", text)
        self.assertIn("SHA-256", text)

    def test_build_info_records_ci_source_sha_only_when_provided(self):
        """CI 的不可变提交 SHA 必须进入构建元数据；本地无 Git 时不得伪造。"""
        spec = importlib.util.spec_from_file_location("tdac_build_portable", ROOT / "build_portable.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        old = os.environ.get("TDAC_BUILD_SOURCE_SHA")
        try:
            os.environ["TDAC_BUILD_SOURCE_SHA"] = "a" * 40
            with tempfile.TemporaryDirectory(dir=ROOT / ".build") as temp:
                exe = Path(temp) / "probe.exe"
                exe.write_bytes(b"probe")
                info = module._build_info("0.1.0", temp, str(exe))
                self.assertTrue(info["vcs"]["available"])
                self.assertEqual(info["vcs"]["commit"], "a" * 40)
        finally:
            if old is None:
                os.environ.pop("TDAC_BUILD_SOURCE_SHA", None)
            else:
                os.environ["TDAC_BUILD_SOURCE_SHA"] = old

    def test_build_info_does_not_leak_open_source_file_handles(self):
        spec = importlib.util.spec_from_file_location("tdac_build_portable_no_leak", ROOT / "build_portable.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(dir=ROOT / ".build") as temp:
            exe = Path(temp) / "probe.exe"
            exe.write_bytes(b"probe")
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ResourceWarning)
                module._build_info("0.1.0", temp, str(exe))
            self.assertEqual([w for w in caught if issubclass(w.category, ResourceWarning)], [])

    def test_checksum_manifest_excludes_stale_release_zip(self):
        """旧 ZIP 留在 dist 时不得进入本轮 onedir 校验清单。"""
        spec = importlib.util.spec_from_file_location("tdac_build_portable_sums", ROOT / "build_portable.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(dir=ROOT / ".build") as temp:
            root = Path(temp)
            (root / "TDAC填写助手").mkdir()
            (root / "TDAC填写助手" / "TDAC填写助手.exe").write_bytes(b"new-build")
            (root / "BUILD_INFO.json").write_text("{}", encoding="utf-8")
            (root / "TDAC填写助手-0.1.0-windows-x64.zip").write_bytes(b"stale-archive")
            sums = root / "SHA256SUMS.txt"
            module._write_sums(str(root), str(sums), skip=str(sums))
            listed = sums.read_text(encoding="utf-8")
            self.assertIn("TDAC填写助手/TDAC填写助手.exe", listed)
            self.assertNotIn("TDAC填写助手-0.1.0-windows-x64.zip", listed)

    def test_license_is_mit(self):
        text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", text)


if __name__ == "__main__":
    unittest.main()
