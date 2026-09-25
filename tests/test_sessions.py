"""Focused tests for active-session detection and context status."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import claude_monitor as cm  # noqa: E402

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def assistant(ts, mid, inp=2, out=100, cw=0, cr=0, model="claude-opus-5-5", **extra):
    return {"type": "assistant", "timestamp": iso(ts), "sessionId": "x", "cwd": r"C:\work\CRM",
            "message": {"id": mid, "model": model, "role": "assistant",
                        "usage": {"input_tokens": inp, "output_tokens": out,
                                  "cache_creation_input_tokens": cw,
                                  "cache_read_input_tokens": cr}}, **extra}


def user(ts, text):
    return {"type": "user", "timestamp": iso(ts), "cwd": r"C:\work\CRM",
            "message": {"role": "user", "content": text}}


def tool_result(ts):
    return {"type": "user", "timestamp": iso(ts),
            "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}}


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, records, mtime=NOW):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        os.utime(p, (mtime.timestamp(), mtime.timestamp()))
        return p

    def active(self, **kw):
        return cm.find_active_sessions(now=NOW, dirs=[self.root], **kw)

    def test_context_history_and_streaming_dedup(self):
        t = NOW - timedelta(minutes=5)
        self.write("proj/aaa111.jsonl", [
            user(t, "fix the bug"),
            assistant(t, "m1", inp=2, out=10, cw=50_000, cr=10_000),
            assistant(t, "m1", inp=2, out=300, cw=50_000, cr=10_000),  # streamed chunk
            tool_result(t),
            assistant(t, "m2", inp=3, out=40, cw=1_000, cr=60_000),
        ])
        [s] = self.active()
        self.assertEqual(s["turns"], 2)
        self.assertEqual(s["prompts"], 1)  # tool_result is not a prompt
        self.assertEqual(s["ctx_latest"], 3 + 1_000 + 60_000)
        self.assertEqual(s["ctx_peak"], 3 + 1_000 + 60_000)
        self.assertEqual(s["total"], (2 + 300 + 50_000 + 10_000) + (3 + 40 + 1_000 + 60_000))

    def test_latest_context_drops_after_compaction_but_peak_remains(self):
        t = NOW - timedelta(minutes=1)
        self.write("proj/bbb.jsonl", [assistant(t, "a", cr=200_000), assistant(t, "b", cr=30_000)])
        [s] = self.active()
        self.assertEqual((s["ctx_latest"], s["ctx_peak"]), (30_002, 200_002))

    def test_session_identity_and_project(self):
        t = NOW - timedelta(minutes=1)
        self.write("C--work-CRM/0a6e3f12-dead-beef.jsonl", [assistant(t, "a")])
        [s] = self.active()
        self.assertEqual(s["session_id"], "0a6e3f12-dead-beef")
        self.assertEqual(s["project"], "CRM")
        self.assertEqual(s["model"], "claude-opus-5-5")

    def test_old_sessions_excluded(self):
        old = NOW - timedelta(hours=5)
        self.write("proj/old.jsonl", [assistant(old, "a")], mtime=old)
        # touched recently but last record old (e.g. copied file) -> still excluded
        self.write("proj/stale.jsonl", [assistant(old, "a")], mtime=NOW)
        self.write("proj/new.jsonl", [assistant(NOW - timedelta(minutes=10), "a")])
        self.assertEqual([s["session_id"] for s in self.active()], ["new"])

    def test_active_window_is_configurable(self):
        t = NOW - timedelta(minutes=90)
        self.write("proj/s.jsonl", [assistant(t, "a")], mtime=t)
        self.assertEqual(len(self.active(window_minutes=120)), 1)
        self.assertEqual(len(self.active(window_minutes=60)), 0)

    def test_multiple_simultaneous_sessions_and_subagents(self):
        t1, t2 = NOW - timedelta(minutes=30), NOW - timedelta(minutes=2)
        self.write("p1/s1.jsonl", [assistant(t1, "a", cr=1_000)], mtime=t1)
        self.write("p2/s2.jsonl", [assistant(t2, "b", cr=2_000)], mtime=t2)
        self.write("p2/s2/subagents/agent-1.jsonl",
                   [assistant(t2, "c", cr=5_000, isSidechain=True)], mtime=t2)
        s2, s1 = self.active()  # most recent first
        self.assertEqual((s1["session_id"], s2["session_id"]), ("s1", "s2"))
        self.assertEqual(s2["turns"], 1)  # sub-agent turns not counted as parent turns
        self.assertEqual(s2["ctx_latest"], 2_002)
        self.assertEqual(s2["total"], (2 + 100 + 2_000) + (2 + 100 + 5_000))

    def test_old_subagent_still_counts_toward_active_parent(self):
        old, new = NOW - timedelta(hours=3), NOW - timedelta(minutes=2)
        self.write("p/s.jsonl", [assistant(old, "a"), assistant(new, "b")])
        self.write("p/s/subagents/agent-1.jsonl",
                   [assistant(old, "c", cr=4_000_000, isSidechain=True)], mtime=old)
        [s] = self.active()
        self.assertEqual(s["total"], 2 * (2 + 100) + (2 + 100 + 4_000_000))
        self.assertEqual(s["status"], "REVIEW")

    def test_threshold_classification(self):
        def status(ctx=0, turns=0, total=0):
            return cm.session_status({"ctx_latest": ctx, "turns": turns, "total": total})[0]
        self.assertEqual(status(ctx=149_999), "KEEP")
        self.assertEqual(status(ctx=150_000), "REVIEW")
        self.assertEqual(status(ctx=249_999), "REVIEW")
        self.assertEqual(status(ctx=250_000), "SWITCH")
        self.assertEqual(status(turns=39), "KEEP")
        self.assertEqual(status(turns=40), "REVIEW")
        self.assertEqual(status(turns=80), "SWITCH")
        self.assertEqual(status(total=2_999_999), "KEEP")
        self.assertEqual(status(total=3_000_000), "REVIEW")
        self.assertEqual(status(total=10_000_000), "SWITCH")

    def test_strongest_condition_wins(self):
        st, why = cm.session_status({"ctx_latest": 100_000, "turns": 45, "total": 12_000_000})
        self.assertEqual(st, "SWITCH")
        self.assertEqual(why, ["total 12.00M"])
        self.assertEqual(cm.session_status({"ctx_latest": 1, "turns": 1, "total": 1}), ("KEEP", []))

    def test_pretty_model(self):
        self.assertEqual(cm.pretty_model("claude-opus-5-5"), "Opus 5.5")
        self.assertEqual(cm.pretty_model("claude-sonnet-5"), "Sonnet 5")
        self.assertEqual(cm.pretty_model("claude-haiku-4-5-20251001"), "Haiku 4.5")


if __name__ == "__main__":
    unittest.main()
