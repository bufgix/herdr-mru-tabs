"""Opt-in integration test against an EMPTY, disposable Herdr server.

Start a named server first, then pass its explicit API socket with --socket.
Never targets a default, selected, or remote session.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True)
    args = parser.parse_args()
    socket = Path(args.socket).resolve()
    if socket.parent.parent.name != "sessions":
        raise RuntimeError("Use an explicitly named disposable session")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("HERDR_")}
    env.update(HERDR_SOCKET_PATH=str(socket), HERDR_ENV="1")

    def call(*argv):
        proc = subprocess.run(["herdr", *argv], env=env, capture_output=True,
                              text=True, timeout=10)
        if proc.returncode:
            raise RuntimeError(proc.stderr or proc.stdout)
        return json.loads(proc.stdout)["result"]

    def snapshot():
        return call("api", "snapshot")["snapshot"]

    def wait_until(predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(.01)
        raise AssertionError("Timed out waiting for plugin/server")

    def logs():
        return call("plugin", "log", "list", "--plugin", "herdr-mru-tabs",
                    "--limit", "100")["logs"]

    def settle():
        def idle():
            items = logs()
            errors = [item for item in items if item["status"] == "failed"]
            if errors:
                raise AssertionError(errors)
            return all(item["status"] == "succeeded" for item in items)
        wait_until(idle)

    assert not snapshot()["workspaces"], "Disposable session must start empty"
    workspace = None
    try:
        created = call("workspace", "create", "--label", "MRU integration", "--focus")
        workspace = created["workspace"]["workspace_id"]
        env["HERDR_WORKSPACE_ID"] = workspace
        first = created["tab"]["tab_id"]
        tabs = [first]
        for label in ["B", "C", "D"]:
            created = call("tab", "create", "--workspace", workspace,
                           "--label", label, "--no-focus")
            tabs.append(created["tab"]["tab_id"])
        settle()
        # Focus D, C, B, A to establish the known MRU order A, B, C, D.
        for tab in reversed(tabs):
            call("tab", "focus", tab)
            settle()

        def press(action, expected):
            reply = call("plugin", "action", "invoke", "herdr-mru-tabs." + action)
            wait_until(lambda: snapshot()["focused_tab_id"] == expected)
            settle()
            print(f"{action}: {expected}")

        for expected in tabs[1:] + tabs[:1]:
            press("next", expected)
        # Reverse traverses the same frozen ring.
        press("previous", tabs[-1])
        # Only the final selection gets promoted after the idle boundary.
        time.sleep(.85)
        press("next", tabs[0])
        # A manual focus ends the current gesture.
        call("tab", "focus", tabs[2])
        settle()
        press("next", tabs[0])
        # Close an unselected tab without resetting the remaining ring.
        call("tab", "close", tabs[1])
        settle()
        press("next", tabs[-1])
        print("PASS: real hooks, focus, MRU traversal, wrap, reverse, idle, manual focus, close")
    finally:
        if workspace:
            call("workspace", "close", workspace)


if __name__ == "__main__":
    main()
