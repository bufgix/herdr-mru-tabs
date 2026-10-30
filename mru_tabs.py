#!/usr/bin/env python3
"""Herdr MRU tab gestures. No daemon, keyboard interception, or dependencies."""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from contextlib import contextmanager


DEFAULT_TIMEOUT_MS = 700


def touch(history: list[str], tab_id: str) -> list[str]:
    return [tab_id] + [item for item in history if item != tab_id]


def reconcile(state: dict, valid: list[str], current: str, now: float,
              timeout: float) -> None:
    """Prune stale tabs, finish idle gestures, and account for manual focus."""
    live = set(valid)
    state["history"] = list(dict.fromkeys(
        tab for tab in state.get("history", []) if tab in live
    ))
    cycle = state.get("cycle")
    if cycle:
        elapsed = now - cycle["last_at"]
        if (not 0 <= elapsed < timeout or current != cycle["target"]
                or cycle["target"] not in live):
            if cycle["target"] in live:
                state["history"] = touch(state["history"], cycle["target"])
            state.pop("cycle", None)
    if "cycle" not in state and current in live:
        state["history"] = touch(state["history"], current)
    # Tabs not visited since installation have no known relative recency.
    # Append them in Herdr's tab order until actual focus events establish it.
    state["history"] += [tab for tab in valid if tab not in state["history"]]


def advance(state: dict, valid: list[str], current: str, now: float,
            timeout: float, direction: int) -> str | None:
    reconcile(state, valid, current, now, timeout)
    if current not in valid or len(valid) < 2:
        state.pop("cycle", None)
        return None
    cycle = state.get("cycle")
    if cycle:
        # New tabs join on the next gesture; closed tabs disappear immediately.
        order = [tab for tab in cycle["order"] if tab in valid]
    else:
        order = state["history"][:]
    if len(order) < 2:
        state.pop("cycle", None)
        return None
    index = (order.index(current) + direction) % len(order)
    target = order[index]
    state["cycle"] = {"order": order, "target": target, "last_at": now}
    return target


class Herdr:
    def __init__(self, env: dict[str, str]):
        self.binary = env.get("HERDR_BIN_PATH", "herdr")
        self.env = env

    def call(self, *args: str) -> dict:
        proc = subprocess.run([self.binary, *args], env=self.env,
                              capture_output=True, text=True, timeout=8)
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip()
                               or f"Herdr exited {proc.returncode}")
        reply = json.loads(proc.stdout)
        if "error" in reply:
            raise RuntimeError(str(reply["error"]))
        return reply["result"]

    def snapshot(self) -> dict:
        return self.call("api", "snapshot")["snapshot"]

    def focus(self, tab_id: str) -> None:
        self.call("tab", "focus", tab_id)


def read_json(path: Path, default: dict) -> dict:
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return copy.deepcopy(default)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def timeout_seconds(env: dict[str, str]) -> float:
    config = read_json(Path(env["HERDR_PLUGIN_CONFIG_DIR"]) / "config.json", {})
    ms = config.get("cycle_timeout_ms", DEFAULT_TIMEOUT_MS)
    if isinstance(ms, bool) or not isinstance(ms, (int, float)) \
            or not math.isfinite(ms) or not 100 <= ms <= 5000:
        raise ValueError("cycle_timeout_ms must be a number from 100 to 5000")
    return ms / 1000


@contextmanager
def locked_state(env: dict[str, str]):
    # Socket identity separates sessions and invalidates history after restart,
    # when Herdr may reuse tab IDs for entirely different processes.
    socket_path = Path(env["HERDR_SOCKET_PATH"]).resolve()
    stat = socket_path.stat()
    identity = f"{socket_path}:{stat.st_ino}:{stat.st_ctime_ns}"
    key = hashlib.sha256(identity.encode()).hexdigest()[:20]
    directory = Path(env["HERDR_PLUGIN_STATE_DIR"])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{key}.json"
    with (directory / f"{key}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_json(path, {"version": 1, "workspaces": {}})
        if state.get("version") != 1:
            raise ValueError("Unrecognized MRU state version")
        yield state
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps(state, separators=(",", ":")) + "\n")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def workspace_id_for(command: str, snapshot: dict, env: dict[str, str]) -> str | None:
    if command != "observe":
        return env.get("HERDR_WORKSPACE_ID") or snapshot.get("focused_workspace_id")
    event = json.loads(env.get("HERDR_PLUGIN_EVENT_JSON", "{}"))
    data = event.get("data", event)
    return (data.get("workspace_id") or data.get("tab", {}).get("workspace_id")
            or snapshot.get("focused_workspace_id"))


def run(command: str, env: dict[str, str], api: Herdr | None = None) -> None:
    if env.get("HERDR_ENV") != "1":
        raise RuntimeError("Run this plugin through Herdr inside a managed session")
    if not env.get("HERDR_SOCKET_PATH"):
        raise RuntimeError("Missing explicit Herdr socket; refusing an ambiguous target")
    api = api or Herdr(env)
    timeout = timeout_seconds(env)
    with locked_state(env) as state:
        snapshot = api.snapshot()
        workspace_id = workspace_id_for(command, snapshot, env)
        workspace = next((w for w in snapshot["workspaces"]
                          if w["workspace_id"] == workspace_id), None)
        if not workspace:
            return
        tabs = [t["tab_id"] for t in snapshot["tabs"]
                if t["workspace_id"] == workspace_id]
        current = workspace["active_tab_id"]
        item = state["workspaces"].setdefault(workspace_id, {"history": []})
        now = time.monotonic()
        if command == "observe":
            # Read current server focus rather than replaying a delayed event's
            # old destination. Own focus hooks must not reorder the frozen ring.
            reconcile(item, tabs, current, now, timeout)
        else:
            before = copy.deepcopy(item)
            target = advance(item, tabs, current, now, timeout,
                             -1 if command == "previous" else 1)
            if target:
                try:
                    api.focus(target)
                except Exception:
                    item.clear()
                    item.update(before)
                    raise
                # Idle time starts after the focus round trip, not before it.
                item["cycle"]["last_at"] = time.monotonic()
        live_workspaces = {w["workspace_id"] for w in snapshot["workspaces"]}
        state["workspaces"] = {k: v for k, v in state["workspaces"].items()
                               if k in live_workspaces}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["observe", "next", "previous"])
    args = parser.parse_args()
    try:
        run(args.command, dict(os.environ))
    except (OSError, ValueError, RuntimeError, KeyError,
            subprocess.SubprocessError) as error:
        print(f"MRU Tabs: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
