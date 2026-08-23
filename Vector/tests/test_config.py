#!/usr/bin/env python3
"""
AP_FLAKE8_CLEAN
Tests for the config document itself, as opposed to the geometry it describes.

Two things are checked here. First, that ``vector.schema.json`` and
``vector.json`` agree -- the schema is what gives an editor autocomplete and
inline validation while hand-editing, and a schema that has drifted from the
document is worse than no schema at all. Second, that the loader keeps its
promises about defaults, inheritance and rejection.

Geometry lives in ``test_kinematics.py``; the wire payload lives in
``test_state_contract.py``.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "Vector", "dashboard"))

from server import config as vconfig  # noqa: E402

CONFIG_DIR = os.path.join(REPO_ROOT, "Vector", "config")
SCHEMA_PATH = os.path.join(CONFIG_DIR, "vector.schema.json")


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


class SchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(SCHEMA_PATH)
        cls.document = load_json(vconfig.DEFAULT_CONFIG_PATH)

    def validator(self):
        try:
            import jsonschema
        except ImportError:  # pragma: no cover - depends on the environment
            self.skipTest("jsonschema not installed")
        return jsonschema

    def test_config_matches_its_schema(self) -> None:
        self.validator().validate(self.document, self.schema)

    def test_schema_reference_in_the_document_resolves(self) -> None:
        """The ``$schema`` key has to point at a file that is actually there."""
        declared = self.document.get("$schema")
        self.assertIsNotNone(declared, "config should declare $schema for editor support")
        resolved = os.path.normpath(os.path.join(CONFIG_DIR, declared))
        self.assertTrue(os.path.exists(resolved), f"$schema points at missing {resolved}")

    def test_schema_rejects_an_unknown_key(self) -> None:
        """
        ``additionalProperties: false`` is the point of the schema.

        Without it a typo like ``tilt_limit_de`` validates cleanly and then silently
        falls back to the default, which is the failure this is guarding against.
        """
        jsonschema = self.validator()
        broken = copy.deepcopy(self.document)
        broken["gimbal_defaults"]["tilt_limit_de"] = 22.5
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(broken, self.schema)

    def test_schema_rejects_a_zero_sign(self) -> None:
        """The loader refuses this too; the schema should catch it before saving."""
        jsonschema = self.validator()
        broken = copy.deepcopy(self.document)
        broken["gimbal_defaults"]["outer"]["sign"] = 0
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(broken, self.schema)

    def test_schema_rejects_a_bad_status(self) -> None:
        jsonschema = self.validator()
        broken = copy.deepcopy(self.document)
        broken["arms"][0]["status"] = "enabled"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(broken, self.schema)

    def test_schema_covers_every_key_the_loader_reads(self) -> None:
        """
        A key the loader honours but the schema omits would be flagged as an error
        while hand-editing, which is the most confusing possible outcome.
        """
        expected_sections = {
            "$schema", "schema_version", "vehicle", "gimbal_defaults",
            "arms", "link", "bench_limits", "bench_controller",
        }
        self.assertEqual(set(self.schema["properties"]), expected_sections)

        axis_keys = set(self.schema["definitions"]["axis"]["properties"])
        self.assertEqual(
            axis_keys,
            {
                "channel", "sign", "center_us", "us_per_deg",
                "servo_limit_deg", "min_us", "max_us", "trim_deg",
            },
        )

        motor_keys = set(self.schema["definitions"]["motor"]["properties"])
        self.assertEqual(motor_keys, {"channel", "spin", "reversed", "test_sequence", "function"})


class LoaderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg = vconfig.load()

    def document(self):
        return copy.deepcopy(self.cfg.raw)

    def test_only_live_arms_are_offered_for_command(self) -> None:
        """The channel accessors are what the bench writes to, so they gate safety."""
        live = {arm.id for arm in self.cfg.live_arms()}
        self.assertTrue(live, "at least one arm should be live")
        for arm in self.cfg.arms:
            if arm.id in live:
                continue
            for channel in arm.servo_channels():
                self.assertNotIn(channel, self.cfg.servo_channels())
            for channel in arm.motor_channels():
                self.assertNotIn(channel, self.cfg.motor_channels())

    def test_channel_lookup_finds_the_owning_arm(self) -> None:
        """The UI labels telemetry by owner, so this mapping has to be exact."""
        for arm in self.cfg.arms:
            for name in ("outer", "inner"):
                found = self.cfg.arm_for_servo_channel(arm.gimbal.axis(name).channel)
                self.assertEqual(found, (arm, name))
            for role, motor in arm.motors.items():
                found = self.cfg.arm_for_motor_channel(motor.channel)
                self.assertEqual(found, (arm, role))

    def test_unknown_channel_returns_none_rather_than_guessing(self) -> None:
        claimed = {axis.channel for arm in self.cfg.arms for axis in
                   (arm.gimbal.outer, arm.gimbal.inner)}
        claimed |= {m.channel for arm in self.cfg.arms for m in arm.motors.values()}
        free = next(c for c in range(1, 33) if c not in claimed)
        self.assertIsNone(self.cfg.arm_for_servo_channel(free))
        self.assertIsNone(self.cfg.arm_for_motor_channel(free))

    def test_missing_arms_list_is_rejected(self) -> None:
        with self.assertRaisesRegex(vconfig.ConfigError, "non-empty 'arms'"):
            vconfig.from_dict({"vehicle": {"name": "x"}})

    def test_duplicate_arm_ids_are_rejected(self) -> None:
        broken = self.document()
        broken["arms"][1]["id"] = broken["arms"][0]["id"]
        with self.assertRaisesRegex(vconfig.ConfigError, "unique"):
            vconfig.from_dict(broken)

    def test_out_of_range_channel_is_rejected_with_its_path(self) -> None:
        """The message has to say which arm and axis, or it is useless in a big config."""
        broken = self.document()
        broken["arms"][1]["inner"]["channel"] = 99
        with self.assertRaises(vconfig.ConfigError) as caught:
            vconfig.from_dict(broken)
        message = str(caught.exception)
        self.assertIn("east", message)
        self.assertIn("inner", message)

    def test_inverted_pulse_window_is_rejected(self) -> None:
        broken = self.document()
        broken["gimbal_defaults"]["inner"]["min_us"] = 2600
        with self.assertRaisesRegex(vconfig.ConfigError, "min_us must be below max_us"):
            vconfig.from_dict(broken)

    def test_an_arm_may_override_a_default(self) -> None:
        """Per-arm overrides are how a differently-built arm is described."""
        edited = self.document()
        edited["arms"][0]["gimbal"] = {"tilt_limit_deg": 15.0}
        cfg = vconfig.from_dict(edited)
        self.assertAlmostEqual(cfg.arm("north").gimbal.tilt_limit_deg, 15.0)
        # Every other arm keeps the default, and the override does not leak.
        default = edited["gimbal_defaults"]["tilt_limit_deg"]
        self.assertAlmostEqual(cfg.arm("east").gimbal.tilt_limit_deg, default)

    def test_save_validates_before_writing(self) -> None:
        """A rejected edit must leave the file on disk exactly as it was."""
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "vector.json")
            saved = vconfig.save(self.cfg, {}, path=target)
            before = load_json(target)

            with self.assertRaises(vconfig.ConfigError):
                vconfig.save(saved, {"arms": [{"id": "bad", "outer": {"channel": 99}}]},
                             path=target)

            self.assertEqual(load_json(target), before)

    def test_save_replaces_the_arms_list_rather_than_merging_it(self) -> None:
        """
        Arms are positional, so a deep merge would splice entries together.

        Dropping an arm has to actually drop it, not leave a half-merged ghost.
        """
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "vector.json")
            vconfig.save(self.cfg, {}, path=target)
            trimmed = [copy.deepcopy(self.cfg.raw["arms"][0])]
            result = vconfig.save(self.cfg, {"arms": trimmed}, path=target)
            self.assertEqual([arm.id for arm in result.arms], [trimmed[0]["id"]])
            self.assertEqual(len(load_json(target)["arms"]), 1)

    def test_unknown_keys_survive_a_save(self) -> None:
        """
        The Setup page submits fragments, so keys it does not model must round trip.

        Losing a field the UI has not been taught about would be silent data loss.
        """
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "vector.json")
            seeded = copy.deepcopy(self.cfg.raw)
            seeded["notes"] = {"built_by": "bench"}
            cfg = vconfig.from_dict(seeded, path=target)
            vconfig.save(cfg, {"bench_limits": {"motor_percent": 25.0}}, path=target)
            written = load_json(target)
            self.assertEqual(written["notes"], {"built_by": "bench"})
            self.assertAlmostEqual(written["bench_limits"]["motor_percent"], 25.0)


if __name__ == "__main__":
    unittest.main()
