# Herdr MRU Tabs

Cycle [Herdr](https://herdr.dev) tabs in **most recently used order** with
Ctrl+Tab. Jump to your last tab, then keep pressing Tab to reach older tabs
without bouncing between the same two.

No daemon, third-party Python packages, or keyboard permissions required.

## How it works

Suppose your recent tab order is **A → B → C → D**, with A active:

| Input | Active tab |
| --- | --- |
| Ctrl+Tab | B |
| Ctrl+Tab again within 700 ms | C |
| Ctrl+Tab again within 700 ms | D |
| Ctrl+Tab again within 700 ms | A |

The order stays fixed while cycling. After a **700 ms pause**, the selected tab
becomes most recent and the next press starts a new cycle. For example, pause
on C and the next Ctrl+Tab returns to A. Ctrl+Shift+Tab can traverse the same
list in reverse.

**This uses an idle timeout, not Control key release.** Herdr's plugin API does
not expose modifier release events. If you pause longer than the timeout while
holding Control, the next Tab starts a new cycle. Each press immediately focuses
its destination; there is no switcher overlay.

## Requirements

- Herdr **0.9.3 or newer**.
- macOS or Linux.
- Python **3.9 or newer**, available as `python3` in Herdr's environment.

## Installation

### 1. Install the plugin

```sh
herdr plugin install bufgix/herdr-mru-tabs
```

### 2. Bind the shortcuts

Add these entries to `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "ctrl+tab"
type = "plugin_action"
command = "herdr-mru-tabs.next"
description = "Cycle recently used tabs"

# Optional: traverse the same list in reverse.
[[keys.command]]
key = "ctrl+shift+tab"
type = "plugin_action"
command = "herdr-mru-tabs.previous"
description = "Cycle recently used tabs in reverse"
```

Remove these chords from any existing bindings, including `keys.last_pane`, so
each chord has only one action. You can keep the native last-pane toggle on a
different shortcut, such as `last_pane = "ctrl+alt+o"` inside `[keys]`.

### 3. Let your terminal forward the shortcuts

Your outer terminal must pass the chords through to Herdr.

**Ghostty** reserves Ctrl+Tab and Ctrl+Shift+Tab for its own tabs. Add to your
Ghostty configuration:

```ini
keybind = ctrl+tab=csi:9;5u
keybind = ctrl+shift+tab=csi:9;6u
```

Then select **Ghostty → Reload Configuration**. Reloading Herdr alone does not
reload Ghostty's configuration.

For other terminals, remove conflicting tab-switching bindings and ensure they
can encode and forward modified Tab keys.

### 4. Reload Herdr

Inside Herdr, press **Ctrl+B**, release it, then press **Shift+R** (the default
reload binding). Alternatively, choose **reload config** in Herdr's global menu.
This reloads the client's local keybindings as well as the selected server's
configuration.

Visit a few tabs to establish their recent order, then try Ctrl+Tab.

> Install the plugin on the server hosting the tabs you want to cycle.
> Installing it locally does not install it on an SSH machine.

## Configuration

Find the plugin's configuration directory:

```sh
herdr plugin config-dir herdr-mru-tabs
```

Create `config.json` in that directory:

```json
{
  "cycle_timeout_ms": 700
}
```

| Setting | Default | Allowed values |
| --- | --- | --- |
| `cycle_timeout_ms` | `700` | `100`–`5000` milliseconds |

A longer timeout gives you more time between presses. A shorter timeout makes
separate gestures start sooner. Changes apply on the next invocation, with no
restart needed.

## History and scope

- Each workspace has its own tab history; cycling stays in that workspace.
- Ordinary tab switches update recency and end an active cycle.
- Closed tabs are skipped. New tabs join on the next cycle.
- Tabs not yet visited since installation initially follow their displayed
  order, because their earlier usage history is unknown.
- State is kept in `HERDR_PLUGIN_STATE_DIR`, outside the source checkout, and
  separated by server socket and server lifetime. A server restart starts fresh
  history so reused tab IDs cannot select unrelated tabs.

The plugin only changes tab focus. It does not reorder the visible tab bar,
create terminals, or send input to programs inside tabs.

## Troubleshooting

**Ctrl+Tab does nothing**

1. Check `herdr plugin list` to confirm `herdr-mru-tabs` is enabled.
2. Press Ctrl+B, then `?`, and search for the plugin binding. If it is missing,
   reload configuration inside Herdr.
3. Check for conflicting terminal shortcuts. In Ghostty, reload configuration
   from its menu after adding the forwarding bindings above.
4. Check `herdr config check` and the plugin logs:

   ```sh
   herdr plugin log list --plugin herdr-mru-tabs --limit 10
   ```

**It still switches only between two tabs**

Ensure the chord invokes `herdr-mru-tabs.next` rather than `last_pane`. There
must be at least three tabs in the current workspace to traverse more than two.
Press again within the timeout, or increase `cycle_timeout_ms`.

**Python cannot be found**

Ensure `python3 --version` reports Python 3.9 or newer and that `python3` is on
the PATH inherited by the Herdr server. The plugin uses only the standard
library; there is nothing to install with pip.

## Development

Clone and link the checkout instead of installing a managed GitHub copy:

```sh
git clone https://github.com/bufgix/herdr-mru-tabs.git
cd herdr-mru-tabs
herdr plugin link "$PWD"
```

Run the tests:

```sh
python3 -m unittest discover -s tests -v
```

Tests cover ordering, wrap, reverse traversal, idle commit, manual focus,
closed and newly opened tabs, delayed focus hooks, session/workspace isolation,
concurrent invocations, and failed focus requests.

An opt-in integration check also exercises real Herdr events and plugin actions
in an **empty disposable session**. Start a named server in another terminal:

```sh
herdr --session mru-test server
```

Pass the explicit API socket printed by that server:

```sh
python3 tests/integration_herdr.py --socket /path/to/sessions/mru-test/herdr.sock
```

The check refuses default sessions and nonempty sessions. Stop the disposable
server afterward:

```sh
herdr --session mru-test server stop
```

## Uninstall

Remove the plugin's `[[keys.command]]` entries from your Herdr configuration,
then run:

```sh
herdr plugin uninstall herdr-mru-tabs
```

For a linked development checkout, use `herdr plugin unlink herdr-mru-tabs`
instead. Reload configuration inside Herdr afterward. If you no longer want
the terminal forwarding bindings, remove those from Ghostty separately and
reload its configuration too.
