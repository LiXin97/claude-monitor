# claude-monitor

Monitor [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions running in tmux — get Telegram push notifications when tasks finish or need your input, and send responses right from your phone.

```
┌──────────────────────────────┐
│  Machine (behind NAT)        │
│                              │
│  tmux ─► claude-monitor ─────┼──► Telegram Bot API ──► Your Phone
│  (claude code)          ◄────┼──◄                  ◄──
│          send-keys           │
└──────────────────────────────┘
```

## Why

You're running Claude Code in tmux on a remote server. It finishes a 20-minute task, asks you a question, and... you're not at your desk. Minutes (or hours) wasted waiting for a response you didn't know was needed.

claude-monitor watches your tmux panes, detects when Claude Code changes state, and pings you on Telegram. You can read the context and reply — all from your phone.

## Features

- **Auto-discovers** Claude Code sessions in tmux — no manual pane configuration
- **Push notifications** via Telegram when Claude Code:
  - Finishes a task (🟢)
  - Needs your input (🟡)
  - Asks for permission (🔴)
- **Remote input** — reply directly from Telegram to send text into the tmux pane
- **Quick reply** — when only one pane is waiting, just type your message (no commands needed)
- **Multi-machine** — run on multiple servers with the same Telegram bot, each identified by name
- **Debounced** — state must be stable for 10s before notifying (no false alarms)

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/status` | Show all Claude Code pane states |
| `/view <machine>` | View last 30 lines of terminal output |
| `/send <machine> <text>` | Send input to a pane |
| `/send <machine>:<pane> <text>` | Send to a specific pane |
| `/machines` | List connected machines |

When exactly one pane is waiting for input, just type your message directly — no command needed.

## Install

```bash
pip install git+https://github.com/LiXin97/claude-monitor.git
```

Or clone and install locally:

```bash
git clone https://github.com/LiXin97/claude-monitor.git
cd claude-monitor
pip install -e .
```

Requires Python 3.10+ and tmux.

## Setup

### 1. Create a Telegram bot

1. Open Telegram, search for [@BotFather](https://t.me/BotFather)
2. Send `/newbot`, follow the prompts
3. Copy the bot token

### 2. Get your chat ID

1. Send any message to your new bot
2. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Find `"chat":{"id": 123456789}` in the response

### 3. Run setup

```bash
claude-monitor init
```

This creates `~/.claude-monitor/config.yaml` with owner-only permissions (600).

### 4. Start monitoring

```bash
# Foreground (recommended for first run)
claude-monitor run -v

# Or install as a systemd user service
claude-monitor install-service
```

## Configuration

`~/.claude-monitor/config.yaml`:

```yaml
telegram:
  bot_token: "123456:ABC-DEF..."
  chat_id: 123456789

machine:
  name: "my-server"       # shows in notifications as [my-server]

monitor:
  poll_interval: 5         # seconds between checks
  stable_threshold: 2      # polls before notification (avoids flapping)
  context_lines: 30        # terminal lines to capture

# Optional: only monitor specific panes (default: auto-discover all)
sessions: []
```

## Multi-Machine Setup

Use the **same** `bot_token` and `chat_id` on every machine. Just set a different `machine.name`:

```yaml
# On server-a
machine:
  name: "server-a"

# On server-b
machine:
  name: "server-b"
```

All notifications are prefixed with `[machine-name]`. Commands like `/send` and `/view` route by machine name.

> **Note:** Telegram only allows one bot instance to poll for commands at a time. All machines can send notifications simultaneously, but only one machine will receive commands (e.g., `/send`, `/view`). The library handles this automatically with retries — if one machine stops, another will take over polling.

## How It Works

1. **Scraper** — runs `tmux capture-pane` every 5 seconds on all panes where `pane_current_command == "claude"`
2. **State machine** — regex patterns detect 4 states from the terminal output:
   - `working` — tool execution (`● Bash(...)`) or spinners (`✢`, `✽`)
   - `idle` — prompt (`❯`) visible with completion message (`✻ Worked for ...`)
   - `needs_input` — prompt visible with a question above it
   - `permission` — approval prompt (`Allow?`, `(y/n)`)
3. **Debounce** — state must be stable for `stable_threshold` consecutive polls before triggering
4. **Telegram** — sends formatted notification on state transitions, accepts commands via long polling

No inbound ports needed — Telegram uses outbound HTTPS polling. Works behind NAT/firewalls.

## CLI Reference

```
claude-monitor init              # Interactive setup
claude-monitor run [-v] [-c ..]  # Run in foreground
claude-monitor status [-c ..]    # Show discovered panes
claude-monitor install-service   # Install systemd user service
claude-monitor stop              # Stop the systemd service
```

## License

MIT
