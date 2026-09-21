#!/usr/bin/env python3
"""
AP_FLAKE8_CLEAN
Contract test for the command channel.

The browser and the server agree on a wire format that nothing at runtime checks:
the browser writes ``{"command": name, ...}`` and reads back ``{"type": "ack",
"command": name, ...}``. Get either name wrong and the dashboard goes quiet -- no
exception, no log line, just buttons that never re-enable. These tests assert the
agreement in both directions, and that every command the UI actually calls exists.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import unittest

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "Vector", "dashboard"))

from fakes import FakeLink  # noqa: E402
from server import config as vconfig  # noqa: E402
from server.bench import Bench  # noqa: E402
from server.controller import LevelController  # noqa: E402
from server.protocol import COMMANDS, UNACKED, Session, command_catalogue, dispatch  # noqa: E402

WEB_SRC = os.path.join(REPO_ROOT, "Vector", "dashboard", "web", "src")


def build_session(connected: bool = False) -> Session:
    cfg = vconfig.load()
    bench = Bench(cfg, link=FakeLink(connected=connected))
    return Session(bench=bench, controller=LevelController(bench))


class DispatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session = build_session()

    def tearDown(self) -> None:
        self.session.controller.stop()
        self.session.bench.stop_oscillate()
        self.session.bench.outputs.stop()

    def run_command(self, msg):
        return asyncio.run(dispatch(self.session, msg))

    def test_command_name_is_read_from_the_command_field(self) -> None:
        """
        The field the browser writes is the field the server reads.

        This is the exact mismatch that once made every button silently no-op, so it
        gets its own test rather than being implied by the others.
        """
        reply = self.run_command({"command": "level", "active": False})
        self.assertTrue(reply["ok"], reply)

    def test_ack_echoes_the_command_name(self) -> None:
        """Without the echo the browser cannot tell which control to re-enable."""
        for name in ("level", "get_config", "ports"):
            with self.subTest(name):
                reply = self.run_command({"command": name})
                self.assertEqual(reply.get("command"), name)

    def test_ack_echoes_the_request_id(self) -> None:
        reply = self.run_command({"command": "level", "active": False, "id": 77})
        self.assertEqual(reply["id"], 77)

    def test_failures_still_carry_the_command_name(self) -> None:
        """A failed command must clear its pending flag too, so it needs the echo."""
        reply = self.run_command({"command": "aim", "forward": 0, "right": 0})
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["command"], "aim")

    def test_unknown_command_fails_without_raising(self) -> None:
        reply = self.run_command({"command": "explode"})
        self.assertFalse(reply["ok"])
        self.assertIn("explode", reply["message"])

    def test_missing_command_field_fails_cleanly(self) -> None:
        reply = self.run_command({})
        self.assertFalse(reply["ok"])

    def test_link_commands_refuse_when_there_is_no_link(self) -> None:
        """
        Commands that touch the vehicle must fail loudly rather than pretend.

        Every command declaring ``needs_link`` is checked, so a new one cannot be
        added without this gate applying to it.
        """
        for name, command in sorted(COMMANDS.items()):
            if not command.needs_link or name in UNACKED:
                continue
            with self.subTest(name):
                reply = self.run_command({"command": name})
                self.assertFalse(reply["ok"], f"{name} should have refused")
                self.assertIn("Not connected", reply["message"])

    def test_streamed_commands_are_answered_with_silence(self) -> None:
        """An ack per pointer move would flood the feed and slow the drag."""
        session = build_session(connected=True)
        try:
            reply = asyncio.run(
                dispatch(session, {"command": "live_aim", "forward": 1.0, "right": 0.0})
            )
            self.assertEqual(reply, {})
        finally:
            session.bench.outputs.stop()

    def test_bad_arguments_are_reported_as_operator_error(self) -> None:
        session = build_session(connected=True)
        try:
            reply = asyncio.run(dispatch(session, {"command": "aim", "right": 0.0}))
            self.assertFalse(reply["ok"])
            self.assertIn("forward", reply["message"])
        finally:
            session.bench.outputs.stop()

    def test_accel_mode_starts_the_hold_loop(self) -> None:
        session = build_session(connected=True)
        try:
            reply = asyncio.run(
                dispatch(session, {"command": "level", "active": True, "mode": "accel"})
            )
            self.assertTrue(reply["ok"], reply)
            self.assertIn("Accel hold", reply["message"])
            self.assertEqual(session.controller.status().mode, "accel")
            self.assertTrue(session.controller.active)
        finally:
            session.controller.stop()
            session.bench.outputs.stop()

    def test_retuning_the_idle_demo_does_not_stop_the_running_one(self) -> None:
        """Stabilize sliders must not kill accel hold, and the other way around."""
        session = build_session(connected=True)
        try:
            start = asyncio.run(
                dispatch(session, {"command": "level", "active": True, "mode": "accel"})
            )
            self.assertTrue(start["ok"], start)
            reply = asyncio.run(
                dispatch(
                    session,
                    {
                        "command": "level",
                        "active": False,
                        "mode": "level",
                        "levelGain": 0.5,
                    },
                )
            )
            self.assertTrue(reply["ok"], reply)
            self.assertIn("Left accel running", reply["message"])
            self.assertEqual(session.controller.status().mode, "accel")
            self.assertTrue(session.controller.active)
        finally:
            session.controller.stop()
            session.bench.outputs.stop()

    def test_config_edits_ask_every_tab_to_reload(self) -> None:
        """Two open tabs must not disagree about the config after one of them saves."""
        reply = self.run_command({"command": "reload_config"})
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(reply.get("reloadConfig"))


class CatalogueTest(unittest.TestCase):
    def test_catalogue_covers_every_command(self) -> None:
        names = {entry["name"] for entry in command_catalogue()}
        self.assertEqual(names, set(COMMANDS))

    def test_every_command_has_a_summary(self) -> None:
        """The catalogue is shown in the UI, so an empty summary is a visible hole."""
        for entry in command_catalogue():
            self.assertTrue(entry["summary"].strip(), entry["name"])


class FrontendCallsTest(unittest.TestCase):
    """
    Every command the web app calls must exist on the server.

    A typo in a `send()` call is invisible until someone clicks that button, and the
    only symptom is a failed ack in a feed nobody is watching.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.calls = {}
        for root, _dirs, files in os.walk(WEB_SRC):
            for name in files:
                if not name.endswith((".vue", ".ts")):
                    continue
                path = os.path.join(root, name)
                with open(path, "r", encoding="utf-8") as handle:
                    body = handle.read()
                for match in re.finditer(r"send\(\s*'([a-z_]+)'", body):
                    cls.calls.setdefault(match.group(1), set()).add(
                        os.path.relpath(path, WEB_SRC)
                    )

    def test_frontend_calls_are_all_real_commands(self) -> None:
        unknown = {
            name: sorted(files)
            for name, files in self.calls.items()
            if name not in COMMANDS
        }
        self.assertEqual(unknown, {}, f"web app calls commands the server lacks: {unknown}")

    def test_the_scan_found_something(self) -> None:
        """Guards against the regex silently matching nothing after a refactor."""
        self.assertGreater(len(self.calls), 8, f"only found {sorted(self.calls)}")


if __name__ == "__main__":
    unittest.main()
