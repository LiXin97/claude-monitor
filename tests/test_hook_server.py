"""Tests for Claude Code hooks HTTP server."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_monitor.hook_server import HookServer


@pytest.fixture
def hook_server():
    telegram = AsyncMock()
    telegram.send_notification = AsyncMock()
    telegram.send_message = AsyncMock()
    telegram.send_hook_permission = AsyncMock()
    telegram.pane_label_for_cwd = MagicMock(return_value="")
    server = HookServer(telegram_bot=telegram, port=0)  # port=0 = random available
    return server


@pytest.mark.asyncio
async def test_hook_server_starts_and_stops(hook_server):
    """Server can start and stop without error."""
    await hook_server.start()
    assert hook_server.port > 0
    await hook_server.stop()


@pytest.mark.asyncio
async def test_hook_stop_logs_only(hook_server):
    """POST /hook/stop returns OK but does not send Telegram message (scraper handles it)."""
    await hook_server.start()
    port = hook_server.port

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    body = json.dumps({
        "session_id": "abc123",
        "cwd": "/home/user/project",
    })
    request = (
        f"POST /hook/stop HTTP/1.1\r\n"
        f"Host: localhost:{port}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"\r\n"
        f"{body}"
    )
    writer.write(request.encode())
    await writer.drain()
    response = await reader.read(4096)
    writer.close()
    await writer.wait_closed()

    assert b"200" in response
    hook_server._telegram_bot.send_message.assert_not_called()

    await hook_server.stop()


@pytest.mark.asyncio
async def test_hook_notification_forwards_to_telegram(hook_server):
    """POST /hook/notification forwards the message to Telegram with project name."""
    await hook_server.start()
    port = hook_server.port

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    body = json.dumps({
        "session_id": "abc123",
        "cwd": "/home/user/my-app",
        "message": "Task completed successfully",
    })
    request = (
        f"POST /hook/notification HTTP/1.1\r\n"
        f"Host: localhost:{port}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"\r\n"
        f"{body}"
    )
    writer.write(request.encode())
    await writer.drain()
    response = await reader.read(4096)
    writer.close()
    await writer.wait_closed()

    assert b"200" in response
    hook_server._telegram_bot.send_message.assert_called_once()
    msg = hook_server._telegram_bot.send_message.call_args[0][0]
    assert "Task completed" in msg
    assert "my-app" in msg  # project name from cwd

    await hook_server.stop()


@pytest.mark.asyncio
async def test_hook_notification_suppresses_idle_prompt(hook_server):
    """POST /hook/notification with idle_prompt type is suppressed (scraper handles it)."""
    await hook_server.start()
    port = hook_server.port

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    body = json.dumps({
        "session_id": "abc123",
        "cwd": "/home/user/my-app",
        "message": "Claude is waiting for your input",
        "notification_type": "idle_prompt",
    })
    request = (
        f"POST /hook/notification HTTP/1.1\r\n"
        f"Host: localhost:{port}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"\r\n"
        f"{body}"
    )
    writer.write(request.encode())
    await writer.drain()
    response = await reader.read(4096)
    writer.close()
    await writer.wait_closed()

    assert b"200" in response
    hook_server._telegram_bot.send_message.assert_not_called()

    await hook_server.stop()


@pytest.mark.asyncio
async def test_hook_permission_blocks_until_approved(hook_server):
    """POST /hook/permission blocks until user approves via Telegram callback."""
    await hook_server.start()
    port = hook_server.port

    async def simulate_approval():
        """Wait a bit, then simulate user clicking Approve."""
        await asyncio.sleep(0.1)
        # Find the pending request and approve it
        for req_id in list(hook_server._pending_permissions):
            hook_server.resolve_permission(req_id, allow=True)

    approval_task = asyncio.create_task(simulate_approval())

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    body = json.dumps({
        "session_id": "abc123",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /tmp/old"},
    })
    request = (
        f"POST /hook/permission HTTP/1.1\r\n"
        f"Host: localhost:{port}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"\r\n"
        f"{body}"
    )
    writer.write(request.encode())
    await writer.drain()
    response = await asyncio.wait_for(reader.read(4096), timeout=5)
    writer.close()
    await writer.wait_closed()
    await approval_task

    assert b"200" in response
    response_body = response.split(b"\r\n\r\n", 1)[1]
    result = json.loads(response_body)
    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"

    await hook_server.stop()


@pytest.mark.asyncio
async def test_hook_permission_blocks_until_denied(hook_server):
    """POST /hook/permission returns deny when user clicks Deny."""
    await hook_server.start()
    port = hook_server.port

    async def simulate_denial():
        await asyncio.sleep(0.1)
        for req_id in list(hook_server._pending_permissions):
            hook_server.resolve_permission(req_id, allow=False)

    denial_task = asyncio.create_task(simulate_denial())

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    body = json.dumps({
        "session_id": "abc123",
        "tool_name": "Bash",
        "tool_input": {"command": "dangerous command"},
    })
    request = (
        f"POST /hook/permission HTTP/1.1\r\n"
        f"Host: localhost:{port}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"\r\n"
        f"{body}"
    )
    writer.write(request.encode())
    await writer.drain()
    response = await asyncio.wait_for(reader.read(4096), timeout=5)
    writer.close()
    await writer.wait_closed()
    await denial_task

    assert b"200" in response
    response_body = response.split(b"\r\n\r\n", 1)[1]
    result = json.loads(response_body)
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    await hook_server.stop()


@pytest.mark.asyncio
async def test_hook_permission_timeout_denies(hook_server):
    """POST /hook/permission returns deny on timeout."""
    hook_server._permission_timeout = 0.3  # very short timeout for test
    await hook_server.start()
    port = hook_server.port

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    body = json.dumps({
        "session_id": "abc123",
        "tool_name": "Bash",
        "tool_input": {"command": "something"},
    })
    request = (
        f"POST /hook/permission HTTP/1.1\r\n"
        f"Host: localhost:{port}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"\r\n"
        f"{body}"
    )
    writer.write(request.encode())
    await writer.drain()
    response = await asyncio.wait_for(reader.read(4096), timeout=5)
    writer.close()
    await writer.wait_closed()

    assert b"200" in response
    response_body = response.split(b"\r\n\r\n", 1)[1]
    result = json.loads(response_body)
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "timeout" in result["hookSpecificOutput"].get("permissionDecisionReason", "").lower()

    await hook_server.stop()


@pytest.mark.asyncio
async def test_hook_unknown_path(hook_server):
    """Unknown path returns 404."""
    await hook_server.start()
    port = hook_server.port

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    request = (
        f"GET /unknown HTTP/1.1\r\n"
        f"Host: localhost:{port}\r\n"
        f"\r\n"
    )
    writer.write(request.encode())
    await writer.drain()
    response = await reader.read(4096)
    writer.close()
    await writer.wait_closed()

    assert b"404" in response

    await hook_server.stop()
