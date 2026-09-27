"""GUI load-path tests: Active Sessions must not wait on the historical scan.

Needs tkinter and a display (skipped otherwise). On Linux CI: xvfb-run python -m unittest
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import claude_monitor as cm  # noqa: E402

try:
    import tkinter as tk
    import claude_monitor_gui as gui
    _root = tk.Tk()
    _root.destroy()
    GUI_OK = True
except Exception:  # no tkinter / no display
    GUI_OK = False


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def write_transcript(root, now):
    t = now - timedelta(minutes=3)
    recs = [
        {"type": "user", "timestamp": iso(t), "cwd": "/work/CRM",
         "message": {"role": "user", "content": "hello"}},
        {"type": "assistant", "timestamp": iso(t), "cwd": "/work/CRM", "requestId": "r1",
         "message": {"id": "m1", "model": "claude-opus-4-5", "role": "assistant",
                     "usage": {"input_tokens": 5, "output_tokens": 50,
                               "cache_creation_input_tokens": 1000,
                               "cache_read_input_tokens": 2000}}},
    ]
    p = root / "proj" / "sess1.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")


@unittest.skipUnless(GUI_OK, "tkinter/display unavailable")
class GuiLoadingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        write_transcript(root, datetime.now(timezone.utc))
        self.counts = {"sessions": 0, "entries": 0}
        self.release = threading.Event()
        real_find, real_load = cm.find_active_sessions, cm.EntryCache.load
        counts, release = self.counts, self.release

        def find(*a, **kw):
            counts["sessions"] += 1
            return real_find(*a, **kw)

        def slow_load(cache, *a, **kw):
            counts["entries"] += 1
            release.wait(10)  # historical scan blocks until the test releases it
            return real_load(cache, *a, **kw)

        def quota_429(creds, timeout=8):
            raise cm.QuotaError("http_429")

        self.patches = [
            mock.patch.object(cm, "data_dirs", lambda: [root]),
            mock.patch.object(gui, "find_active_sessions", find),
            mock.patch.object(cm.EntryCache, "load", slow_load),
            mock.patch.object(cm, "fetch_live_quota", quota_429),
            mock.patch.object(cm, "read_account_credentials", lambda *a: None),
            mock.patch.object(gui, "TRAY_AVAILABLE", False),
            mock.patch.object(gui.MonitorGUI, "_save_config", lambda self: None),
        ]
        for p in self.patches:
            p.start()
        self.root = tk.Tk()
        self.app = gui.MonitorGUI(self.root)

    def settle(self):
        """Let in-flight worker threads deliver before the root goes away."""
        self.release.set()
        self.pump(lambda: not self.app.loading and not self.app.entries_loading
                  and self.app.quota_cache is not None, 5)

    def tearDown(self):
        self.settle()
        self.root.destroy()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def pump(self, cond, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            # run the real mainloop briefly: worker threads' root.after() needs it
            self.root.after(20, self.root.quit)
            self.root.mainloop()
            if cond():
                return True
        return False

    def session_rows(self):
        return len(self.app.tree_sessions.get_children())

    def test_sessions_render_while_history_is_slow(self):
        self.assertTrue(self.pump(lambda: self.session_rows() == 1, 3),
                        "Active Sessions stuck behind historical scan")
        self.assertIsNone(self.app.entries_cache)
        self.assertTrue(self.app.entries_loading)
        self.assertFalse(self.app.loading)
        self.assertIsNotNone(self.app._refresh_timer)  # auto-refresh re-armed

        # quota 429 blocked neither path
        self.assertTrue(self.pump(lambda: self.app.quota_cache is not None, 2))
        self.assertEqual(self.app.quota_cache, {"error": "http_429"})

        # manual refresh while the scan is in flight: sessions reload, no 2nd scan
        self.app.refresh()
        self.assertTrue(self.pump(lambda: self.counts["sessions"] == 2 and not self.app.loading, 3))
        self.assertEqual(self.counts["entries"], 1)

        # historical finishes later and renders
        self.release.set()
        self.assertTrue(self.pump(lambda: self.app.entries_cache is not None, 5))
        self.assertFalse(self.app.entries_loading)
        self.assertEqual(len(self.app.entries_cache), 1)
        self.assertIn("1", self.app.status.cget("text"))
        self.assertTrue(self.app.tree_daily.get_children())
        self.assertEqual(self.session_rows(), 1)

        # next refresh starts a fresh scan (cache hit path)
        self.app.refresh()
        self.assertTrue(self.pump(lambda: self.counts["entries"] == 2
                                  and not self.app.entries_loading, 5))
        self.assertEqual(len(self.app.entries_cache), 1)

    def test_history_error_does_not_block_sessions_or_refresh(self):
        with mock.patch.object(cm.EntryCache, "load", side_effect=RuntimeError("boom")):
            self.settle()
            self.root.destroy()
            self.root = tk.Tk()
            self.app = gui.MonitorGUI(self.root)
            self.assertTrue(self.pump(lambda: self.session_rows() == 1
                                      and not self.app.entries_loading, 3))
            self.assertIn("boom", self.app.status.cget("text"))
            self.assertIsNotNone(self.app._refresh_timer)
        self.app.refresh()  # recovers on the next refresh
        self.assertTrue(self.pump(lambda: self.app.entries_cache is not None, 5))


if __name__ == "__main__":
    unittest.main()
