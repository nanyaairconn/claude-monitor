"""Focused tests for the GUI's active-session row preparation (no UI)."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import claude_monitor as cm  # noqa: E402
from claude_monitor_gui import session_rows  # noqa: E402

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def session(sid, minutes_ago, ctx=10_000, turns=5, total=100_000):
    s = {"session_id": sid, "project": "CRM", "model": "claude-opus-5-5",
         "last": NOW - timedelta(minutes=minutes_ago),
         "ctx_latest": ctx, "turns": turns, "total": total}
    s["status"], s["reasons"] = cm.session_status(s)
    return s


class SessionRowsTests(unittest.TestCase):
    def test_urgency_then_recency_order(self):
        rows = session_rows([
            session("keep-new", 1),
            session("review-old", 50, turns=45),
            session("switch", 90, ctx=260_000),
            session("review-new", 5, total=3_500_000),
            session("keep-old", 30),
        ])
        self.assertEqual([r[1] for r in rows],
                         ["CRM [switch]", "CRM [review]", "CRM [review]", "CRM [keep-n]", "CRM [keep-o]"])
        self.assertEqual([r[0] for r in rows], ["SWITCH", "REVIEW", "REVIEW", "KEEP", "KEEP"])
        # within REVIEW, most recently active first
        self.assertEqual(rows[1][6], "total 3.50M")
        self.assertEqual(rows[2][6], "turns 45")

    def test_row_fields_and_tag(self):
        [row] = session_rows([session("74ba5b5d-x", 1, ctx=130_600, turns=33, total=3_150_000)])
        self.assertEqual(row, ("REVIEW", "CRM [74ba5b]", "Opus 5.5", "130.6K", 33,
                               "3.15M", "total 3.15M", "review"))

    def test_keep_has_no_reason_and_empty_input(self):
        self.assertEqual(session_rows([session("a", 1)])[0][6], "")
        self.assertEqual(session_rows([]), [])


if __name__ == "__main__":
    unittest.main()
