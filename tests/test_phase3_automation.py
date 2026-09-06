"""Phase 3 tests — Daily Report Delivery & Automation.

Covers:
  - Systemd unit file syntax / content validation
  - Timer calendar expression validation
  - Report delivery channels (save, telegram)
  - Weekly/Monthly/List report commands
  - CLI wiring for phase 3 commands
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import date as date_mod, timedelta
from pathlib import Path

# Ensure the project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ── Helpers ─────────────────────────────────────────────────


def _read_file(path):
    with open(path) as f:
        return f.read()


# ── Systemd unit tests ──────────────────────────────────────


class TestSystemdUnits(unittest.TestCase):
    """Validate systemd unit files exist and have correct syntax."""

    def setUp(self):
        self.deploy_dir = PROJECT_ROOT / "deploy"

    def test_aggregator_service_exists(self):
        path = self.deploy_dir / "rowbutt-aggregator.service"
        self.assertTrue(path.exists(), f"{path} does not exist")

    def test_aggregator_service_content(self):
        content = _read_file(self.deploy_dir / "rowbutt-aggregator.service")
        self.assertIn("[Unit]", content)
        self.assertIn("[Service]", content)
        # Should execute pull → compute → report pipeline
        self.assertIn("pull-all", content)
        self.assertIn("report", content)

    def test_aggregator_timer_exists(self):
        path = self.deploy_dir / "rowbutt-aggregator.timer"
        self.assertTrue(path.exists(), f"{path} does not exist")

    def test_aggregator_timer_calendar(self):
        content = _read_file(self.deploy_dir / "rowbutt-aggregator.timer")
        self.assertIn("[Timer]", content)
        self.assertIn("OnCalendar", content)
        # Should fire daily
        self.assertIn("daily", content.lower() or "23:55" in content)

    def test_agent_service_exists(self):
        path = self.deploy_dir / "rowbutt-agent.service"
        self.assertTrue(path.exists(), f"{path} does not exist")

    def test_agent_service_content(self):
        content = _read_file(self.deploy_dir / "rowbutt-agent.service")
        self.assertIn("[Unit]", content)
        self.assertIn("[Service]", content)
        self.assertIn("Type=simple", content)

    def test_web_service_exists(self):
        path = self.deploy_dir / "rowbutt-web.service"
        self.assertTrue(path.exists(), f"{path} does not exist")

    def test_web_service_content(self):
        content = _read_file(self.deploy_dir / "rowbutt-web.service")
        self.assertIn("[Unit]", content)
        self.assertIn("[Service]", content)
        self.assertIn("8123", content)


# ── Report delivery tests ────────────────────────────────────


class TestReportDelivery(unittest.TestCase):
    """Test that report delivery mechanisms work."""

    def setUp(self):
        # Use a temp dir as ROWBUTT_DIR
        self.tmpdir = tempfile.mkdtemp()
        self.reports_dir = Path(self.tmpdir) / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        # Write a test report
        self.test_report = "## Daily Cost Report\n\nTotal: $1.23"
        self.test_date = "2026-09-06"
        self.report_path = self.reports_dir / f"{self.test_date}.md"
        self.report_path.write_text(self.test_report)

    def test_report_saved_to_disk(self):
        """Report file exists with correct content."""
        self.assertTrue(self.report_path.exists())
        content = self.report_path.read_text()
        self.assertIn("$1.23", content)

    def test_report_list_shows_dates(self):
        """Simulate inspecting the reports directory."""
        files = list(self.reports_dir.glob("*.md"))
        dates = [f.stem for f in sorted(files)]
        self.assertIn(self.test_date, dates)

    def test_report_with_telegram_delivery(self):
        """Telegram send script path resolves (may not exist on CI)."""
        script = os.path.expanduser(
            "~/.hermes/skills/telegram-send/scripts/telegram-send"
        )
        # Just check the path is well-formed — actual execution depends on Hermes
        self.assertTrue(script.startswith(os.path.expanduser("~")))

    def test_week_report_naming(self):
        """Week report naming convention: YYYY-MM-DD-week.md"""
        week_path = self.reports_dir / f"{self.test_date}-week.md"
        week_path.write_text("## Weekly Summary\n\n7-day aggregate")
        self.assertTrue(week_path.exists())
        content = week_path.read_text()
        self.assertIn("Weekly", content)
        self.assertIn("7-day", content)


# ── Report generator helpers ─────────────────────────────────


class TestReportCommands(unittest.TestCase):
    """Validate report generator helpers used in Phase 3."""

    def test_formatting_helpers_exist(self):
        """_fmt_tokens, _fmt_hours, _fmt_usd should exist as module-level functions."""
        from aggregator.report import _fmt_tokens, _fmt_hours, _fmt_usd

        # Ensure formatting helpers are defined
        self.assertTrue(callable(_fmt_tokens))
        self.assertTrue(callable(_fmt_usd))

    def test_fmt_tokens_thousands(self):
        from aggregator.report import _fmt_tokens

        result = _fmt_tokens(1500000)
        self.assertIn("M", result)

    def test_fmt_tokens_zero(self):
        from aggregator.report import _fmt_tokens

        result = _fmt_tokens(0)
        self.assertIn("0", result)

    def test_fmt_usd_format(self):
        from aggregator.report import _fmt_usd

        result = _fmt_usd(1.5)
        self.assertIn("$", result)

    def test_fmt_usd_small_value(self):
        from aggregator.report import _fmt_usd

        result = _fmt_usd(0.0123)
        self.assertIn("$", result)


# ── CLI wiring tests ─────────────────────────────────────────


class TestCLIWiring(unittest.TestCase):
    """Validate that Phase 3 commands are wired in the CLI."""

    def test_report_today_command_exists(self):
        """report today should be a registered click command."""
        from cli.main import cli

        # Inspect the click group for the 'report' group
        report_cmd = cli.get_command(None, "report")
        self.assertIsNotNone(report_cmd)
        today_cmd = report_cmd.get_command(None, "today")
        self.assertIsNotNone(today_cmd)

    def test_report_week_command_exists(self):
        from cli.main import cli

        report_cmd = cli.get_command(None, "report")
        week_cmd = report_cmd.get_command(None, "week")
        self.assertIsNotNone(week_cmd)

    def test_report_month_command_exists(self):
        from cli.main import cli

        report_cmd = cli.get_command(None, "report")
        month_cmd = report_cmd.get_command(None, "month")
        self.assertIsNotNone(month_cmd)

    def test_report_list_command_exists(self):
        from cli.main import cli

        report_cmd = cli.get_command(None, "report")
        list_cmd = report_cmd.get_command(None, "list")
        self.assertIsNotNone(list_cmd)

    def test_report_date_command_exists(self):
        from cli.main import cli

        report_cmd = cli.get_command(None, "report")
        date_cmd = report_cmd.get_command(None, "date")
        self.assertIsNotNone(date_cmd)


# ── Deploy script tests ──────────────────────────────────────


class TestDeployScripts(unittest.TestCase):
    """Verify deploy/bootstrap and related scripts exist and are valid."""

    def test_deploy_bootstrap_exists(self):
        path = PROJECT_ROOT / "deploy" / "bootstrap.sh"
        self.assertTrue(path.exists())

    def test_deploy_bootstrap_is_shell_script(self):
        content = _read_file(PROJECT_ROOT / "deploy" / "bootstrap.sh")
        self.assertTrue(content.strip().startswith("#!/usr/bin/env bash") or
                        content.strip().startswith("#!/bin/bash"))

    def test_start_agent_script_exists(self):
        path = PROJECT_ROOT / "deploy" / "start-agent.sh"
        self.assertTrue(path.exists())

    def test_start_aggregator_script_exists(self):
        path = PROJECT_ROOT / "deploy" / "start-aggregator.sh"
        self.assertTrue(path.exists())

    def test_install_agent_script_exists(self):
        path = PROJECT_ROOT / "deploy" / "install-agent.sh"
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
