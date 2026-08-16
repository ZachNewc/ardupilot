#!/usr/bin/env python3
"""
AP_FLAKE8_CLEAN
Contract test for the state snapshot the browser consumes.

The web UI reads these keys by name from TypeScript interfaces in
``dashboard/web/src/lib/types.ts``. Nothing at runtime checks that the two agree, so
a rename on either side produces a UI full of ``undefined`` rather than an error --
which is exactly the failure this file exists to turn into a red test.

It also pins two conventions that are easy to lose:

* every angle, rate and duration key carries its unit, because the same quantity
  appears in this codebase as degrees, radians and microseconds
* ``events`` is a flat, time-ordered list, because the browser merges it with the
  commands it sent itself and that only works if both sides carry timestamps
"""

from __future__ import annotations

import os
import re
import sys
import unittest

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "Vector", "dashboard"))

from fakes import FakeLink  # noqa: E402
from server import config as vconfig  # noqa: E402
from server import state as vstate  # noqa: E402
from server.bench import Bench  # noqa: E402
from server.controller import LevelController  # noqa: E402

TYPES_TS = os.path.join(
    REPO_ROOT, "Vector", "dashboard", "web", "src", "lib", "types.ts"
)


def build_snapshot():
    """Shape a snapshot with nothing connected, which is the harder case to get right."""
    cfg = vconfig.load()
    bench = Bench(cfg, link=FakeLink(connected=False))
    try:
        return vstate.snapshot(bench, LevelController(bench))
    finally:
        bench.outputs.stop()


class StateShapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.snap = build_snapshot()

    def test_top_level_sections(self) -> None:
        self.assertEqual(
            set(self.snap),
            {"link", "vehicle", "arms", "escs", "controller", "outputs", "events", "messageCounts"},
        )

    def test_link_keys(self) -> None:
        self.assertEqual(
            set(self.snap["link"]),
            {
                "connected", "device", "baud", "systemId", "componentId",
                "heartbeatAgeS", "readerAlive", "updatedAt", "lastError", "budget",
            },
        )
        self.assertEqual(
            set(self.snap["link"]["budget"]),
            {
                "channels", "rateHz", "bytesPerSecond",
                "capacityBytesPerSecond", "utilisation", "overBudget",
            },
        )

    def test_heartbeat_age_is_null_when_disconnected(self) -> None:
        """"Never seen" and "seen a long time ago" are different facts."""
        self.assertIsNone(self.snap["link"]["heartbeatAgeS"])

    def test_vehicle_keys(self) -> None:
        self.assertEqual(
            set(self.snap["vehicle"]),
            {
                "armed", "mode",
                "rollDeg", "pitchDeg", "yawDeg",
                "rollRateDegS", "pitchRateDegS", "yawRateDegS", "rateMagnitudeDegS",
                "voltage", "current", "batteryRemaining", "loadPercent",
                "gpsFix", "gpsSats", "throttle", "ageS",
            },
        )

    def test_arm_keys(self) -> None:
        arm = self.snap["arms"][0]
        self.assertEqual(
            set(arm),
            {"id", "label", "status", "live", "azimuthDeg", "mountYawDeg", "axes", "motors", "thrust"},
        )
        self.assertEqual(set(arm["axes"]), {"outer", "inner"})
        self.assertEqual(
            set(arm["axes"]["outer"]),
            {"channel", "pwm", "servoDeg", "tiltDeg", "limitDeg", "servoLimitDeg", "atLimit"},
        )
        self.assertEqual(set(arm["thrust"]), {"forwardDeg", "rightDeg", "vector"})
        self.assertEqual(len(arm["thrust"]["vector"]), 3)

    def test_motor_keys(self) -> None:
        motors = self.snap["arms"][0]["motors"]
        self.assertTrue(motors, "the north arm should have motors configured")
        for motor in motors.values():
            self.assertEqual(
                set(motor),
                {
                    "channel", "pwm", "percent", "spin", "reversed", "testSequence",
                    "rpm", "voltage", "current", "temperatureC",
                },
            )

    def test_every_arm_is_reported_not_just_live_ones(self) -> None:
        """The UI shows planned arms too; that is how the build state stays visible."""
        self.assertEqual(len(self.snap["arms"]), 4)
        self.assertEqual([arm["live"] for arm in self.snap["arms"]], [True, False, False, False])

    def test_controller_keys(self) -> None:
        self.assertEqual(
            set(self.snap["controller"]),
            {
                "active", "mode", "levelGain", "leadTimeS", "maxTiltFraction",
                "invertRoll", "invertPitch", "tiltCapDeg", "target", "preview",
                "saturated", "loopHz", "updates", "lastError",
            },
        )
        self.assertEqual(set(self.snap["controller"]["target"]), {"forward", "right", "magnitude"})
        self.assertEqual(
            set(self.snap["controller"]["preview"]),
            {"forward", "right", "magnitude", "saturated"},
        )

    def test_outputs_keys(self) -> None:
        self.assertEqual(
            set(self.snap["outputs"]), {"owner", "rampActive", "motorTestActive"}
        )

    def test_events_is_a_time_ordered_list(self) -> None:
        self.assertIsInstance(self.snap["events"], list)

    def test_event_entries_carry_a_timestamp(self) -> None:
        from server.link import EVENT_COMMAND, EVENT_ERROR, Event, MavlinkLink

        link = MavlinkLink()
        link.note("first")
        link.record_error("second")
        events = link.telemetry().events

        self.assertEqual(len(events), 2)
        for event in events:
            self.assertIsInstance(event, Event)
            self.assertGreater(event.at, 0.0)
        self.assertEqual(events[0].kind, EVENT_COMMAND)
        self.assertEqual(events[1].kind, EVENT_ERROR)
        # Sequence numbers must increase so the browser can key on them.
        self.assertLess(events[0].seq, events[1].seq)

    def test_escs_are_absent_until_they_report(self) -> None:
        """An empty list, not placeholder rows: the UI says "nothing reporting"."""
        self.assertEqual(self.snap["escs"], [])

    def test_snapshot_is_json_serialisable(self) -> None:
        import json

        json.dumps(self.snap)


class TypeScriptParityTest(unittest.TestCase):
    """
    Cross-checks the payload against the TypeScript interfaces.

    Not a parser -- it just asserts that every key the server emits appears somewhere
    in types.ts. That is enough to catch a rename on either side, which is the
    failure mode that actually happens.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.snap = build_snapshot()
        with open(TYPES_TS, "r", encoding="utf-8") as handle:
            cls.source = handle.read()

    def declared_names(self):
        """
        Every identifier that could name a field.

        Field declarations (``foo: number``) whether on their own line or inline in
        an object type, plus string literals, which is how union-keyed records such
        as ``Record<MotorName, ...>`` spell their keys.
        """
        fields = set(re.findall(r"(\w+)\??\s*:", self.source))
        literals = set(re.findall(r"'([\w-]+)'", self.source))
        return fields | literals

    def test_every_emitted_key_is_declared(self) -> None:
        declared = self.declared_names()

        def walk(node, path=""):
            missing = []
            if isinstance(node, dict):
                for key, value in node.items():
                    # ESC and channel maps are keyed by number, not by field name.
                    if not key.isdigit() and key not in declared:
                        missing.append(f"{path}.{key}".lstrip("."))
                    missing.extend(walk(value, f"{path}.{key}"))
            elif isinstance(node, list) and node:
                missing.extend(walk(node[0], f"{path}[]"))
            return missing

        missing = walk(self.snap)
        self.assertEqual(
            missing,
            [],
            "keys emitted by state.py but absent from types.ts: "
            f"{missing}. Add them to the TypeScript interface or stop sending them.",
        )


if __name__ == "__main__":
    unittest.main()
