import datetime as dt
import unittest

import monitor as m


def issue(n, title="[$250] Bug", labels=("External", "Help Wanted"), state="open", reason=None):
    return {"number": n, "title": title, "labels": [{"name": x} for x in labels],
            "state": state, "state_reason": reason, "html_url": f"https://x/{n}",
            "created_at": "2026-11-01T00:00:00Z", "comments": 0}


class FakeGH:
    def __init__(self, issues, events=None):
        self.issues, self.events = issues, events or {}

    def paginate(self, path, params, max_pages=30):
        if path.endswith("/events"):
            return self.events.get(int(path.split("/")[-2]), [])
        return self.issues


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

    def run_detect(self, tracked, issues, events=None):
        state = {"cursor": "2026-11-17T23:00:00Z", "tracked": tracked}
        found = m.detect(FakeGH(issues, events), state, self.now)
        return [(k, i["number"]) for k, i in found], state

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

    def test_closed_and_prs_ignored(self):
        pr = dict(issue(10), pull_request={})
        found, _ = self.run_detect({}, [issue(11, state="closed"), pr])
        self.assertEqual(found, [])

    def test_unrelated_issue_not_tracked(self):
        _, state = self.run_detect({}, [issue(12, title="x", labels=("Bug",))])
        self.assertNotIn("12", state["tracked"])


if __name__ == "__main__":
    unittest.main()
