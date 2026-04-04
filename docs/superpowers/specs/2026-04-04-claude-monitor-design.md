# Claude Monitor — Design Spec

## Overview

A Python daemon that monitors Claude Code instances running inside tmux sessions, sends push notifications via Telegram when they finish tasks or need input, and allows remote interaction (viewing output, sending input) through a Telegram bot.

## Problem

- User runs Claude Code in tmux across multiple machines
- Machines are behind NAT, not directly reachable from phone
- No push notifications when Claude Code finishes or needs input
- User often away from computer, misses prompts, wastes time

## Solution: Telegram-Centric Monitor

One lightweight Python daemon per machine. Telegram serves as both the notification channel and the relay for remote input (since machines are behind NAT and not directly reachable).

## Architecture

```
┌─────────────────────────────────────┐
│         Each Machine                │
│                                     │
│  ┌───────────┐    ┌──────────────┐  │
│  │ tmux pane  │◄──│  Monitor     │  │
│  │ (claude    │    │  Daemon      │  │
│  │  code)     │──►│              │  │
│  └───────────┘    │ - scraper    │  │
│       ▲           │ - state FSM  │  │
│       │           │ - telegram   │  │
│  send-keys        │   client     │  │
│       │           └──────┬───────┘  │
│       │                  │          │
└───────┼──────────────────┼──────────┘
        │                  │
        │    ┌─────────────▼──────┐
        │    │  Telegram Bot API  │
        │    └─────────────┬──────┘
        │                  │
        │    ┌─────────────▼──────┐
        └────│  Your Phone        │
             │  (Telegram app)    │
             └────────────────────┘
```

## Components

### 1. Scraper (`scraper.py`)

Periodically captures tmux pane content via `tmux capture-pane -p -S -50`.

- Auto-discovers tmux panes running Claude Code by checking `pane_current_command == "claude"`
- Runs every 5 seconds (configurable)
- Returns raw text content of each monitored pane

### 2. State Machine (`state.py`)

Tracks per-pane state and fires events on transitions. Four states:

| State | Detection Pattern (last ~10 lines) |
|-------|-----------------------------------|
| `working` | Lines with `●` (tool execution), spinner characters, or actively changing output |
| `idle` | `❯` prompt line followed by separator `───`, no pending question above |
| `needs_input` | `❯` prompt present AND output above contains a question, checkpoint, or explicit user prompt |
| `permission` | Permission/approval UI elements detected (e.g., "Allow", Yes/No prompts) |

State transition rules:
- State must be stable for 2 consecutive polls (10 seconds) before triggering a notification, to avoid false positives from brief intermediate states
- No repeat notifications for the same state (debounced)

Notification triggers:
- `working → idle`: "task finished"
- `working → needs_input`: "waiting for your input" (includes context)
- `working → permission`: "asking permission" (includes permission text)

### 3. Telegram Bot (`telegram_bot.py`)

Single Telegram bot token shared across all machines. Each machine registers with a unique name.

**Outgoing notifications:**

```
🟢 [machine-name] Claude Code finished task
Session: session:window.pane
Last output:
> ✻ Worked for 9m 8s
> Phase 2 Checkpoint: Ideas Generated...
```

```
🟡 [machine-name] Claude Code waiting for input
Session: session:window.pane
Context:
> Does this landscape match your understanding?
```

```
🔴 [machine-name] Claude Code asking permission
Session: session:window.pane
> Allow Bash: npm install express?
```

**Incoming commands:**

| Command | Description |
|---------|-------------|
| `/status` | List all machines and their Claude Code states |
| `/status <machine>` | Detailed status for one machine |
| `/view <machine>` | Show last ~30 lines of the active pane |
| `/send <machine> <text>` | Send text input to a specific pane |
| `/send <machine>:<session:win.pane> <text>` | Send to a specific pane when multiple exist |
| `/machines` | List all registered machines |

**Quick reply shortcut:** When exactly one pane across all machines is in `needs_input` or `permission` state, plain text messages (no `/` prefix) are forwarded directly to that pane. This enables fast mobile replies.

**Input forwarding:** Uses `tmux send-keys -t <target> "<text>" Enter` to inject user input into the Claude Code pane.

### 4. Monitor Loop (`monitor.py`)

Main loop that ties everything together:

```
every poll_interval seconds:
    panes = scraper.discover_and_capture()
    for pane in panes:
        old_state = state_machine.get(pane.id)
        new_state = state_machine.update(pane.id, pane.content)
        if state_machine.should_notify(pane.id, old_state, new_state):
            telegram.notify(pane, new_state, context=pane.content)
```

### 5. CLI (`cli.py`)

Entry point using `click`:

```bash
claude-monitor init            # Interactive setup: creates config, guides Telegram bot creation
claude-monitor run             # Foreground mode
claude-monitor start           # Daemonize (background)
claude-monitor stop            # Stop background daemon
claude-monitor install-service # Create systemd user service
claude-monitor status          # Show local monitoring status
```

### 6. Service Helper (`service.py`)

Generates and installs a systemd user service file for automatic startup.

## Configuration

File: `~/.claude-monitor/config.yaml`

```yaml
telegram:
  bot_token: "123456:ABC-DEF..."   # Telegram bot token from @BotFather
  chat_id: 123456789               # Your Telegram user ID

machine:
  name: "lab-server"               # Human-readable label for this machine

monitor:
  poll_interval: 5                 # Seconds between tmux captures
  stable_threshold: 2              # Consecutive polls before notification fires
  context_lines: 30                # Lines to capture from tmux pane for context

# Optional: explicitly list sessions to monitor
# If omitted, auto-discovers all tmux panes running 'claude'
sessions: []
```

## Auto-Discovery

By default, the monitor scans all tmux panes via:
```bash
tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index} #{pane_current_command}'
```
Any pane whose `pane_current_command` is `claude` is monitored. New panes are picked up automatically; removed panes are cleaned up.

## Multi-Machine Design

- All machines share the same Telegram bot token and chat_id
- Each machine identifies itself by `machine.name` in config
- All notifications are prefixed with `[machine-name]`
- Commands like `/status`, `/view`, `/send` accept a machine name parameter
- `/machines` lists all machines that have sent a heartbeat recently. Each monitor sends a periodic heartbeat message (every 60s) to a pinned "registry" message in the Telegram chat, updating its name + status. Other monitors read this message to build the machine list.
- `/status` without a machine name: the local monitor responds with its own status. To see all machines, use `/machines`.
- No central server needed — each machine independently talks to Telegram Bot API. The pinned Telegram message acts as a lightweight shared registry.

## Project Structure

```
claude-monitor/
├── pyproject.toml
├── README.md
├── src/
│   └── claude_monitor/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── scraper.py
│       ├── state.py
│       ├── telegram_bot.py
│       ├── monitor.py
│       └── service.py
└── tests/
    ├── test_scraper.py
    ├── test_state.py
    └── test_telegram_bot.py
```

## Dependencies

- `python-telegram-bot` — Telegram Bot API (async)
- `pyyaml` — Config parsing
- `click` — CLI framework
- Python 3.10+
- `tmux` — called via subprocess (not a Python dependency)

## Security Considerations

- Bot token and chat_id stored in `~/.claude-monitor/config.yaml` with user-only permissions (600)
- Only responds to messages from the configured `chat_id` — ignores all other Telegram users
- No inbound network listeners on the machine (Telegram uses outbound polling)
- Input forwarding is restricted to monitored tmux panes only

## Out of Scope (for v1)

- Web UI (can be added later for port-forwarded sessions)
- End-to-end encryption beyond Telegram's built-in
- Multi-user support (single user per bot)
- Claude Code log file parsing (screen scraping only)
