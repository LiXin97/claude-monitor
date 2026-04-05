"""Lightweight HTTP hook server for Claude Code events.

Receives Stop, Notification, and PermissionRequest hooks from Claude Code
and forwards them to Telegram.  The PermissionRequest endpoint blocks until
the user approves or denies via Telegram inline buttons.

Uses only stdlib asyncio (no aiohttp dependency).
"""
import asyncio
import json
import logging
import os
import uuid

logger = logging.getLogger(__name__)


def _project_name(cwd: str) -> str:
    """Extract a short project identifier from cwd path."""
    if not cwd:
        return ""
    return os.path.basename(cwd.rstrip("/"))


class HookServer:
    """HTTP server that receives Claude Code hook events."""

    def __init__(
        self,
        telegram_bot,
        port: int = 9876,
        permission_timeout: float = 300.0,
        machine_name: str = "",
    ):
        self._telegram_bot = telegram_bot
        self._requested_port = port
        self._permission_timeout = permission_timeout
        self._machine_name = machine_name
        self._server: asyncio.Server | None = None
        # Pending permission requests: req_id → (Event, result_dict)
        self._pending_permissions: dict[str, tuple[asyncio.Event, dict]] = {}

    @property
    def port(self) -> int:
        if self._server and self._server.sockets:
            return self._server.sockets[0].getsockname()[1]
        return 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_connection, "127.0.0.1", self._requested_port
        )
        logger.info("Hook server listening on port %d", self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        # Unblock any pending permissions
        for req_id in list(self._pending_permissions):
            self.resolve_permission(req_id, allow=False)

    def resolve_permission(self, request_id: str, allow: bool) -> None:
        """Resolve a pending permission request (called from Telegram button handler)."""
        if request_id not in self._pending_permissions:
            return
        event, result = self._pending_permissions[request_id]
        if allow:
            result["decision"] = "allow"
            result["reason"] = ""
        else:
            result["decision"] = "deny"
            result["reason"] = "User denied via Telegram"
        event.set()

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            # Read request line
            request_line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not request_line:
                writer.close()
                return

            parts = request_line.decode().strip().split()
            if len(parts) < 2:
                await self._send_response(writer, 400, {"error": "Bad request"})
                return

            method, path = parts[0], parts[1]

            # Read headers
            content_length = 0
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=10)
                if line == b"\r\n" or line == b"\n" or not line:
                    break
                header = line.decode().strip().lower()
                if header.startswith("content-length:"):
                    content_length = int(header.split(":", 1)[1].strip())

            # Read body
            body = {}
            if content_length > 0:
                raw = await asyncio.wait_for(
                    reader.readexactly(content_length), timeout=10
                )
                body = json.loads(raw)

            # Route
            if path == "/hook/stop" and method == "POST":
                await self._handle_stop(body, writer)
            elif path == "/hook/notification" and method == "POST":
                await self._handle_notification(body, writer)
            elif path == "/hook/permission" and method == "POST":
                await self._handle_permission(body, writer)
            else:
                await self._send_response(writer, 404, {"error": "Not found"})

        except Exception as e:
            logger.error("Hook connection error: %s", e)
            try:
                await self._send_response(writer, 500, {"error": str(e)})
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _send_response(
        self, writer: asyncio.StreamWriter, status: int, body: dict
    ) -> None:
        status_text = {200: "OK", 400: "Bad Request", 404: "Not Found", 500: "Internal Server Error"}
        payload = json.dumps(body).encode()
        header = (
            f"HTTP/1.1 {status} {status_text.get(status, 'Error')}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
            f"\r\n"
        )
        writer.write(header.encode() + payload)
        await writer.drain()

    def _pane_label_for_cwd(self, cwd: str) -> str:
        """Resolve cwd to a pane label like '1: copilot-api:1.0', or '' if unknown."""
        pane_id = self._telegram_bot._cwd_to_pane.get(cwd)
        if pane_id:
            return self._telegram_bot._format_pane_label(pane_id)
        return ""

    async def _handle_stop(self, body: dict, writer: asyncio.StreamWriter) -> None:
        cwd = body.get("cwd", "")
        project = _project_name(cwd)
        pane_label = self._pane_label_for_cwd(cwd)
        # Stop hook fires every turn end — the tmux scraper already detects
        # IDLE state, so just log it instead of sending a Telegram message.
        logger.info(
            "Stop hook: project=%s pane=%s", project or cwd, pane_label or "?"
        )
        await self._send_response(writer, 200, {"status": "ok"})

    async def _handle_notification(
        self, body: dict, writer: asyncio.StreamWriter
    ) -> None:
        message = body.get("message", "")
        cwd = body.get("cwd", "")
        notification_type = body.get("notification_type", "")

        # Suppress notification types already handled by tmux scraper
        if notification_type in ("idle_prompt", "permission_prompt"):
            logger.debug(
                "Suppressed duplicate %s notification for %s",
                notification_type, _project_name(cwd) or cwd,
            )
            await self._send_response(writer, 200, {"status": "ok"})
            return

        project = _project_name(cwd)
        pane_label = self._pane_label_for_cwd(cwd)
        label = f"[{self._machine_name}] " if self._machine_name else ""

        header = f"ℹ️ {label}Claude Code notification"
        parts = [header]
        if pane_label:
            parts.append(f"Session: <code>{pane_label}</code>")
        if project:
            parts.append(f"Project: <code>{project}</code>")
        if message:
            parts.append(f"\n{message}")
        msg = "\n".join(parts)
        await self._telegram_bot.send_message(msg, parse_mode="HTML")
        await self._send_response(writer, 200, {"status": "ok"})

    async def _handle_permission(
        self, body: dict, writer: asyncio.StreamWriter
    ) -> None:
        tool_name = body.get("tool_name", "unknown")
        tool_input = body.get("tool_input", {})
        session_id = body.get("session_id", "unknown")
        cwd = body.get("cwd", "")
        project = _project_name(cwd)
        pane_label = self._pane_label_for_cwd(cwd)

        req_id = uuid.uuid4().hex[:12]
        event = asyncio.Event()
        result = {"decision": "deny", "reason": "Timeout — denied by default"}

        self._pending_permissions[req_id] = (event, result)

        # Send Telegram notification with buttons
        label = f"[{self._machine_name}] " if self._machine_name else ""
        input_preview = json.dumps(tool_input, ensure_ascii=False)
        if len(input_preview) > 500:
            input_preview = input_preview[:500] + "…"

        await self._telegram_bot.send_hook_permission(
            request_id=req_id,
            label=label,
            tool_name=tool_name,
            input_preview=input_preview,
            project=project,
            pane_label=pane_label,
        )

        # Block until user responds or timeout
        try:
            await asyncio.wait_for(event.wait(), timeout=self._permission_timeout)
        except asyncio.TimeoutError:
            result["decision"] = "deny"
            result["reason"] = "Timeout — denied by default"

        self._pending_permissions.pop(req_id, None)

        # Format response for Claude Code PreToolUse hook
        response = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow" if result["decision"] == "allow" else "deny",
            }
        }
        if result["decision"] != "allow" and result.get("reason"):
            response["hookSpecificOutput"]["permissionDecisionReason"] = result["reason"]
        await self._send_response(writer, 200, response)
