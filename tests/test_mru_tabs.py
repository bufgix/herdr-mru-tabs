import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mru_tabs as mru


class GestureTests(unittest.TestCase):
    def setUp(self):
        self.tabs = ["A", "B", "C", "D"]
        self.state = {"history": self.tabs[:]}

    def step(self, current, at, direction=1):
        return mru.advance(self.state, self.tabs, current, at, .7, direction)

    def test_repeat_wrap_and_own_focus_events_preserve_ring(self):
        current = "A"
        for at, expected in [(10, "B"), (10.1, "C"), (10.2, "D"), (10.3, "A")]:
            current = self.step(current, at)
            self.assertEqual(current, expected)
            mru.reconcile(self.state, self.tabs, current, at + .01, .7)
        self.assertEqual(self.state["history"], self.tabs)

    def test_idle_commits_only_final_selection_and_next_returns_to_origin(self):
        self.assertEqual(self.step("A", 10), "B")
        self.assertEqual(self.step("B", 10.1), "C")
        self.assertEqual(self.step("C", 11), "A")
        self.assertEqual(self.state["history"], ["C", "A", "B", "D"])

    def test_mouse_focus_ends_gesture_and_becomes_most_recent(self):
        self.step("A", 10)
        mru.reconcile(self.state, self.tabs, "D", 10.1, .7)
        self.assertNotIn("cycle", self.state)
        self.assertEqual(self.state["history"], ["D", "B", "A", "C"])
        self.assertEqual(self.step("D", 10.2), "B")

    def test_reverse_and_direction_change(self):
        self.assertEqual(self.step("A", 10, -1), "D")
        self.assertEqual(self.step("D", 10.1, -1), "C")
        self.assertEqual(self.step("C", 10.2), "D")

    def test_closed_tab_is_skipped_during_cycle(self):
        self.step("A", 10)
        self.tabs.remove("C")
        self.assertEqual(self.step("B", 10.1), "D")

    def test_closing_selected_tab_restarts_safely(self):
        self.step("A", 10)
        self.tabs.remove("B")
        self.assertEqual(self.step("A", 10.1), "C")
        self.assertNotIn("B", self.state["history"])

    def test_new_tab_does_not_disturb_current_ring(self):
        self.step("A", 10)
        self.tabs.append("E")
        self.assertEqual(self.step("B", 10.1), "C")
        self.assertNotIn("E", self.state["cycle"]["order"])
        self.step("C", 11)
        self.assertIn("E", self.state["cycle"]["order"])

    def test_no_op_with_zero_or_one_tab(self):
        for tabs in [[], ["A"]]:
            self.assertIsNone(mru.advance({}, tabs, "A", 10, .7, 1))

    def test_late_event_uses_live_focus_and_does_not_reset_idle_clock(self):
        self.step("A", 10)
        self.step("B", 10.1)
        mru.reconcile(self.state, self.tabs, "C", 10.2, .7)
        self.assertEqual(self.state["cycle"]["last_at"], 10.1)
        self.assertEqual(self.step("C", 10.81), "A")

    def test_clock_reset_expires_old_cycle(self):
        self.step("A", 100)
        self.assertEqual(self.step("B", 10), "A")


class FakeAPI:
    def __init__(self):
        self.current = "A"
        self.fail = False

    def snapshot(self):
        return {"focused_workspace_id": "w1", "workspaces": [
            {"workspace_id": "w1", "active_tab_id": self.current},
            {"workspace_id": "w2", "active_tab_id": "X"}],
            "tabs": [{"workspace_id": "w1", "tab_id": t} for t in "ABCD"]
                    + [{"workspace_id": "w2", "tab_id": t} for t in "XY"]}

    def focus(self, tab_id):
        if self.fail:
            raise RuntimeError("Tab was closed")
        self.current = tab_id


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.socket = root / "socket"
        self.socket.touch()
        self.env = {"HERDR_ENV": "1", "HERDR_SOCKET_PATH": str(self.socket),
                    "HERDR_PLUGIN_CONFIG_DIR": str(root / "config"),
                    "HERDR_PLUGIN_STATE_DIR": str(root / "state"),
                    "HERDR_WORKSPACE_ID": "w1"}
        self.api = FakeAPI()

    def state(self):
        with mru.locked_state(self.env) as state:
            return copy.deepcopy(state)

    def test_real_state_file_and_separate_workspace(self):
        mru.run("observe", self.env, self.api)
        mru.run("next", self.env, self.api)
        mru.run("observe", self.env, self.api)
        mru.run("next", self.env, self.api)
        self.assertEqual(self.api.current, "C")
        self.env["HERDR_WORKSPACE_ID"] = "w2"
        mru.run("next", self.env, self.api)
        self.assertEqual(self.api.current, "Y")
        self.assertEqual(self.state()["workspaces"]["w1"]["cycle"]["target"], "C")

    def test_failed_focus_preserves_disk_state(self):
        mru.run("observe", self.env, self.api)
        before = self.state()
        self.api.fail = True
        with self.assertRaises(RuntimeError):
            mru.run("next", self.env, self.api)
        self.assertEqual(self.state(), before)

    def test_session_socket_replacement_invalidates_history(self):
        mru.run("next", self.env, self.api)
        replacement = self.socket.with_name("replacement")
        replacement.touch()
        replacement.replace(self.socket)
        self.assertEqual(self.state()["workspaces"], {})

    def test_simultaneous_shortcuts_serialize_and_keep_valid_json(self):
        failures = []
        def press():
            try:
                mru.run("next", self.env, self.api)
            except Exception as error:
                failures.append(error)
        threads = [threading.Thread(target=press) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failures, [])
        self.assertEqual(self.api.current, "D")
        self.assertEqual(self.state()["workspaces"]["w1"]["history"], list("ABCD"))

    def test_timeout_config_is_validated(self):
        config = Path(self.env["HERDR_PLUGIN_CONFIG_DIR"])
        config.mkdir()
        for value in [True, "700", 0, -1, float("nan"), 6000]:
            (config / "config.json").write_text(json.dumps({"cycle_timeout_ms": value}))
            with self.assertRaises(ValueError):
                mru.timeout_seconds(self.env)
        (config / "config.json").write_text('{"cycle_timeout_ms": 1200}')
        self.assertEqual(mru.timeout_seconds(self.env), 1.2)

    def test_refuses_missing_socket_instead_of_using_global_focus(self):
        self.env.pop("HERDR_SOCKET_PATH")
        with self.assertRaisesRegex(RuntimeError, "explicit Herdr socket"):
            mru.run("next", self.env, self.api)


if __name__ == "__main__":
    unittest.main()
