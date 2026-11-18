import datetime as dt
import json
import os
import tempfile
import unittest
from unittest import mock

import monitor as m


def issue(n, title="[$250] Bug", labels=("External", "Help Wanted"), state="open", reason=None):
    return {"number": n, "title": title, "labels": [{"name": x} for x in labels],
            "state": state, "state_reason": reason, "html_url": f"https://x/{n}",
            "created_at": "2026-11-01T00:00:00Z", "comments": 0}


class FakeGH:
    def __init__(self, issues, events=None, complete=True):
        self.issues, self.events, self.complete = issues, events or {}, complete
        self.calls = 0

    def paginate(self, path, params, max_pages=30, allow_truncated=False):
        if path.endswith("/events"):
            return self.events.get(int(path.split("/")[-2]), []), True
        return self.issues, self.complete


class TestStatus(unittest.TestCase):
    def test_price(self):
        self.assertEqual(m.price_of("[$1,000] x"), "1000")
        self.assertEqual(m.price_of("[$ 250] x"), "250")
        self.assertIsNone(m.price_of("[HOLD] x"))

    def test_status(self):
        self.assertEqual(m.status_of(issue(1)), m.QUALIFIED)
        self.assertEqual(m.status_of(issue(1, title="Bug sin precio")), m.UNQUALIFIED)
        self.assertEqual(m.status_of(issue(1, labels=("External",))), m.UNQUALIFIED)
        self.assertEqual(m.status_of(issue(1, state="closed")), m.CLOSED)


class TestDetect(unittest.TestCase):
    now = dt.datetime(2026, 11, 18, tzinfo=dt.timezone.utc)

    def run_detect(self, tracked, issues, events=None, state=None):
        state = state or {"cursor": "2026-11-17T23:00:00Z", "tracked": tracked}
        found, state["cursor"] = m.detect(FakeGH(issues, events), state, self.now)
        for _, it, entry in found:
            state["tracked"][str(it["number"])] = entry
        return [(k, i["number"]) for k, i, _ in found], state

    def test_old_issue_gets_labels_and_price(self):
        found, state = self.run_detect({"5": {"s": "u"}}, [issue(5)])
        self.assertEqual(found, [("nueva", 5)])
        self.assertEqual(state["tracked"]["5"]["s"], "q")

    def test_unknown_issue_qualifies(self):
        self.assertEqual(self.run_detect({}, [issue(7)])[0], [("nueva", 7)])

    def test_baseline_issue_not_repeated(self):
        self.assertEqual(self.run_detect({"5": {"s": "q"}}, [issue(5)])[0], [])

    def test_price_added_later(self):
        found, _ = self.run_detect({}, [issue(8, title="Bug")])
        self.assertEqual(found, [])
        found, _ = self.run_detect({"8": {"s": "u"}}, [issue(8)])
        self.assertEqual(found, [("nueva", 8)])

    def test_reopen_after_closed(self):
        found, _ = self.run_detect({"9": {"s": "closed"}}, [issue(9, reason="reopened")])
        self.assertEqual(found, [("reapertura", 9)])

    def test_reopen_between_polls(self):
        ev = {9: [{"event": "reopened", "created_at": "2026-11-17T23:30:00Z"}]}
        found, _ = self.run_detect({"9": {"s": "q"}}, [issue(9, reason="reopened")], ev)
        self.assertEqual(found, [("reapertura", 9)])

    def test_old_reopen_not_repeated(self):
        ev = {9: [{"event": "reopened", "created_at": "2026-10-01T00:00:00Z"}]}
        found, _ = self.run_detect({"9": {"s": "q"}}, [issue(9, reason="reopened")], ev)
        self.assertEqual(found, [])

    def test_reopen_in_overlap_alerted_once(self):
        ev = {9: [{"event": "reopened", "created_at": "2026-11-17T22:58:00Z"}]}
        found, state = self.run_detect({"9": {"s": "closed"}}, [issue(9, reason="reopened")], ev)
        self.assertEqual(found, [("reapertura", 9)])
        state["cursor"] = "2026-11-17T23:01:00Z"
        found, _ = self.run_detect(None, [issue(9, reason="reopened")], ev, state)
        self.assertEqual(found, [])

    def test_reopen_before_baseline_ignored(self):
        ev = {9: [{"event": "reopened", "created_at": "2026-11-17T22:58:00Z"}]}
        state = {"cursor": "2026-11-17T23:00:00Z", "baseline_at": "2026-11-17T23:00:00Z",
                 "tracked": {"9": {"s": "q"}}}
        found, _ = self.run_detect(None, [issue(9, reason="reopened")], ev, state)
        self.assertEqual(found, [])

    def test_truncated_page_keeps_cursor_at_last_item(self):
        it = dict(issue(5), updated_at="2026-11-17T23:20:00Z")
        state = {"cursor": "2026-11-17T23:00:00Z", "tracked": {}}
        _, cursor = m.detect(FakeGH([it], complete=False), state, self.now)
        self.assertEqual(cursor, "2026-11-17T23:20:00Z")

    def test_closed_and_prs_ignored(self):
        pr = dict(issue(10), pull_request={})
        found, _ = self.run_detect({}, [issue(11, state="closed"), pr])
        self.assertEqual(found, [])

    def test_unrelated_issue_not_tracked(self):
        _, state = self.run_detect({}, [issue(12, title="x", labels=("Bug",))])
        self.assertNotIn("12", state["tracked"])


class TestMain(unittest.TestCase):
    def test_partial_notify_failure_keeps_delivered(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            m.save_state(path, {"cursor": "2026-11-17T23:00:00Z", "tracked": {}})
            gh = FakeGH([issue(1), issue(2)])
            sent = []

            def fake_notify(_gh, kind, it, test=False):
                if it["number"] == 2:
                    raise RuntimeError("falla")
                sent.append(it["number"])
                return "url"

            with mock.patch.object(m, "GitHub", return_value=gh), \
                    mock.patch.object(m, "notify", fake_notify), \
                    mock.patch.object(m, "SHUTDOWN_AT", dt.datetime(2100, 1, 1, tzinfo=dt.timezone.utc)):
                with self.assertRaises(RuntimeError):
                    m.main(["--state", path])
                with open(path) as f:
                    state = json.load(f)
                self.assertEqual(state["cursor"], "2026-11-17T23:00:00Z")
                self.assertEqual(state["tracked"]["1"]["s"], "q")
                self.assertNotIn("2", state["tracked"])
                found, _ = m.detect(gh, state, dt.datetime(2026, 11, 18, tzinfo=dt.timezone.utc))
                self.assertEqual([i["number"] for _, i, _ in found], [2])


if __name__ == "__main__":
    unittest.main()
