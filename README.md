# claude-monitor

Monitor [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions running in tmux — get Telegram push notifications when tasks finish or need your input, and send responses right from your phone.

```
┌──────────────────────────────────────────────────────┐
│  Machine A (behind NAT)                              │
│  tmux ─► claude-monitor ──┐                          │
│  (claude code)       ◄────┤                          │
└───────────────────────────┤                          │
                            ├──► Telegram Bot ──► Phone
┌───────────────────────────┤                          │
│  Machine B (behind NAT)   │                          │
│  tmux ─► claude-monitor ──┘                          │
│  (claude code)       ◄────┘                          │
└──────────────────────────────────────────────────────┘
```

## Why

You're running Claude Code in tmux on a remote server. It finishes a 20-minute task, asks you a question, and... you're not at your desk. Minutes (or hours) wasted waiting for a response you didn't know was needed.

claude-monitor watches your tmux panes, detects when Claude Code changes state, and pings you on Telegram. You can read the context and reply — all from your phone.

## Features

- **Auto-discovers** Claude Code sessions in tmux — no manual pane configuration
- **Push notifications** via Telegram when Claude Code:
  - Finishes a task (🟢 idle)
  - Needs your input (🟡 needs_input)
  - Asks for permission (🔴 permission)
- **Inline buttons** — Approve/Deny permission requests or View terminal output directly from the notification
- **Reply routing** — reply to any notification message to send text to that specific pane
- **Smart silence** — suppresses notifications when you're actively using Telegram (configurable window, disabled by default)
- **Hook integration** — optional HTTP hook server receives Claude Code events (Stop, Notification, PermissionRequest) for instant, zero-delay notifications
- **Blocking permission approval** — PermissionRequest hooks block Claude Code until you approve or deny via Telegram
- **Smart filtering** — only notifies on actionable states; ignores transitions to working/unknown
- **Scheduled task aware** — detects cron monitoring pauses ("Will check again in...") as working, not idle
- **Remote input** — reply directly from Telegram to send text into the tmux pane
- **Quick reply** — when only one pane is waiting, just type your message (no commands needed)
- **Pane aliases** — auto-assigned numeric IDs (1, 2, 3...) for easy reference in commands
- **Multi-machine** — run on multiple servers with the same Telegram bot, each identified by name
- **Debounced** — state must be stable for 2 consecutive polls before notifying (no false alarms)
- **HTML notifications** — formatted messages with bold headers and code blocks in Telegram

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/status` | Show all Claude Code pane states across all machines |
| `/status <machine>` | Show pane states for a specific machine |
| `/view <machine>` | View last 30 lines of terminal output |
| `/view <machine> <alias>` | View a specific pane by number |
| `/send <machine> <text>` | Send input to a pane |
| `/send <machine> <alias> <text>` | Send to a specific pane by number |
| `/send <machine>:<pane> <text>` | Send to a specific pane by full ID |
| `/machines` | List connected machines |

When exactly one pane is waiting for input, just type your message directly — no command needed. You can also **reply to any notification** to send text to that pane.

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
  index: 0                 # unique per machine (0, 1, 2...) — staggers polling to prevent message loss

monitor:
  poll_interval: 5         # seconds between checks
  stable_threshold: 2      # polls before notification (avoids flapping)
  context_lines: 30        # terminal lines to capture
  notification_silence_seconds: 0  # suppress notifications for N seconds after user interaction (0 = disabled)
  hooks_enabled: false     # enable Claude Code hooks integration
  hook_server_port: 9876   # HTTP port for hook server

# Optional: only monitor specific panes (default: auto-discover all)
sessions: []
```

## Claude Code Hooks (Optional)

For instant, zero-delay notifications and blocking permission approval, you can connect Claude Code's hooks system directly to claude-monitor.

```bash
claude-monitor install-hooks
```

This configures `~/.claude/settings.json` with hooks for Stop, Notification, and PreToolUse events, and enables `hooks_enabled` in your monitor config. Restart the monitor after installation.

When hooks are enabled, the monitor starts an HTTP server on `localhost:9876`. Claude Code sends events to this server, which forwards them to Telegram. Permission requests (Bash, Write, Edit) block Claude Code until you approve or deny via inline buttons in Telegram.

## Multi-Machine Setup

Use the **same** `bot_token` and `chat_id` on every machine. Set a different `machine.name` and a unique `machine.index` (starting from 0):

```yaml
# On server-a
machine:
  name: "server-a"
  index: 0

# On server-b
machine:
  name: "server-b"
  index: 1
```

All notifications are prefixed with `[machine-name]`. Commands like `/send` and `/view` route by machine name.

The `index` staggers each machine's Telegram polling interval (by `index × 1.5s`) so they don't compete for the same updates simultaneously. This prevents message loss when multiple machines share one bot token.

## How It Works

1. **Scraper** — runs `tmux capture-pane` every 5 seconds on all panes where `pane_current_command == "claude"`
2. **State machine** — regex patterns detect 4 states from the terminal output:
   - `working` — tool execution (`● Bash(...)`) or spinners (`✢`, `✽`), scheduled task pauses
   - `idle` — prompt (`❯`) visible with completion message (`✻ Worked for ...`)
   - `needs_input` — prompt visible with a question above it
   - `permission` — approval prompt (`Allow?`, `(y/n)`)
3. **Debounce** — state must be stable for `stable_threshold` consecutive polls before triggering
4. **Notification filter** — only sends alerts for actionable states (idle, needs_input, permission); transitions to working or unknown are silent
5. **Smart silence** — suppresses notifications if you interacted with the bot within the silence window
6. **Inline buttons** — Approve/Deny for permission prompts, View for idle/input states
7. **Hook server** (optional) — receives Claude Code events via HTTP for instant notifications and blocking permission approval
8. **Telegram** — sends HTML-formatted notifications on state transitions, accepts commands via non-blocking polling

No inbound ports needed — all communication uses outbound HTTPS. Works behind NAT/firewalls.

## CLI Reference

```
claude-monitor init              # Interactive setup
claude-monitor run [-v] [-c ..]  # Run in foreground
claude-monitor status [-c ..]    # Show discovered panes
claude-monitor install-hooks     # Configure Claude Code hooks integration
claude-monitor install-service   # Install systemd user service
claude-monitor stop              # Stop the systemd service
```

## License

MIT
