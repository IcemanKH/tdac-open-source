import os
import tempfile
import threading
import time
import unittest

os.environ.setdefault("TDAC_DATA_DIR", os.path.join(tempfile.gettempdir(), "tdac-tests-data"))

import tdac_fill  # noqa: E402


class RunControlTests(unittest.TestCase):
    """B1：GUI 的暂停 / 停止是协作式的，且默认完全不生效（CLI 行为不变）。"""

    def tearDown(self):
        tdac_fill.set_run_control(None)

    def test_default_tick_is_always_true(self):
        tdac_fill.set_run_control(None)
        self.assertTrue(tdac_fill._tick("anywhere"))

    def test_initial_state_is_running(self):
        c = tdac_fill.RunControl()
        self.assertFalse(c.paused)
        self.assertFalse(c.stopped)
        self.assertTrue(c.tick())

    def test_pause_blocks_then_resume_releases(self):
        c = tdac_fill.RunControl()
        c.pause()
        self.assertTrue(c.paused)
        done = []

        def worker():
            done.append(c.tick("x"))

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        time.sleep(0.25)
        self.assertEqual(done, [], "暂停时安全检查点应阻塞")
        c.resume()
        t.join(timeout=3)
        self.assertEqual(done, [True], "恢复后应继续执行")

    def test_stop_makes_tick_return_false(self):
        c = tdac_fill.RunControl()
        c.stop()
        self.assertTrue(c.stopped)
        self.assertFalse(c.tick())

    def test_stop_releases_a_paused_worker(self):
        c = tdac_fill.RunControl()
        c.pause()
        done = []
        t = threading.Thread(target=lambda: done.append(c.tick("x")), daemon=True)
        t.start()
        time.sleep(0.2)
        c.stop()          # 停止应同时解除暂停，避免卡死
        t.join(timeout=3)
        self.assertEqual(done, [False])

    def test_on_status_callback_receives_checkpoint(self):
        c = tdac_fill.RunControl()
        seen = []
        c.on_status = seen.append
        c.tick("步骤A")
        self.assertEqual(seen, ["步骤A"])

    def test_on_status_exception_does_not_break_flow(self):
        c = tdac_fill.RunControl()
        c.on_status = lambda where: (_ for _ in ()).throw(RuntimeError("boom"))
        self.assertTrue(c.tick("x"))

    def test_installed_control_governs_module_tick(self):
        c = tdac_fill.RunControl()
        c.stop()
        tdac_fill.set_run_control(c)
        self.assertFalse(tdac_fill._tick("x"))


if __name__ == "__main__":
    unittest.main()
