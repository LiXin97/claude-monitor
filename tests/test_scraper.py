from unittest.mock import patch, MagicMock
import subprocess
import pytest
from claude_monitor.scraper import TmuxPane, discover_panes, capture_pane


PANE_LIST_OUTPUT = (
    "copilot-api:1.0 claude 21873\n"
    "opd-idea:0.0 claude 3665260\n"
    "copilot-api:0.0 node 367127\n"
    "mywork:0.0 vim 12345\n"
)


def test_discover_panes_filters_claude():
    with patch("claude_monitor.scraper.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout=PANE_LIST_OUTPUT, returncode=0
        )
        panes = discover_panes()
        assert len(panes) == 2
        assert panes[0].pane_id == "copilot-api:1.0"
        assert panes[0].command == "claude"
        assert panes[1].pane_id == "opd-idea:0.0"


def test_discover_panes_with_session_filter():
    with patch("claude_monitor.scraper.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout=PANE_LIST_OUTPUT, returncode=0
        )
        panes = discover_panes(sessions=["opd-idea:0.0"])
        assert len(panes) == 1
        assert panes[0].pane_id == "opd-idea:0.0"


def test_discover_panes_no_tmux():
    with patch("claude_monitor.scraper.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError("tmux not found")
        panes = discover_panes()
        assert panes == []


def test_capture_pane_returns_content():
    fake_output = "● Working on something\n❯ \n───\n"
    with patch("claude_monitor.scraper.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=fake_output, returncode=0)
        content = capture_pane("copilot-api:1.0", context_lines=30)
        assert "● Working" in content
        assert "❯" in content


def test_capture_pane_failure_returns_empty():
    with patch("claude_monitor.scraper.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", returncode=1)
        content = capture_pane("bad:0.0", context_lines=30)
        assert content == ""
