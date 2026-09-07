"""
AP_FLAKE8_CLEAN
A channel map has to be something the board can actually do.

Every check here covers a failure that is completely silent on the vehicle. The
parameter writes succeed, the flight controller acknowledges every command, and the
servo does not move -- so nothing short of plugging a servo in and watching it sit
still reveals the problem. That is exactly the debugging loop these tests exist to
replace.

The specific failure that motivated them: with every motor's ``channel`` left at 0 the
mixer placed Motor1-Motor8 on S1-S8 at boot, which took TIM4 and TIM5 into DShot and
silenced the gimbal servos sitting on S5-S10. S11 and S12 kept working, because TIM15
was the one group no motor landed in. Nothing in the config, the parameters, or the
old test suite said a word about it.
"""

import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dashboard"))

from server import board                  # noqa: E402
from server import config as vconfig      # noqa: E402


def _document():
    """The shipped config as a plain document, for tests that need to break it."""
    with open(vconfig.DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _with(mutate):
    """Build a config from the shipped document after applying ``mutate`` to it."""
    doc = copy.deepcopy(_document())
    mutate(doc)
    return vconfig.from_dict(doc, path=vconfig.DEFAULT_CONFIG_PATH)


def _arm(doc, arm_id):
    for arm in doc["arms"]:
        if arm["id"] == arm_id:
            return arm
    raise KeyError(arm_id)


class TestTimerGroups(unittest.TestCase):
    """The group table is transcribed from hwdef, so check the shape of it."""

    def test_every_onboard_pin_belongs_to_exactly_one_group(self) -> None:
        seen = []
        for group in board.TIMER_GROUPS:
            seen.extend(group.channels)
        self.assertEqual(sorted(seen), list(range(1, board.BOARD_PWM_CHANNELS + 1)))
        self.assertEqual(len(seen), len(set(seen)), "a pin is in two timer groups")

    def test_can_channels_have_no_timer_group(self) -> None:
        """S14 and up have no local timer at all; that is what makes them CAN-only."""
        self.assertIsNone(board.group_for(board.CAN_SERVO_FIRST))
        self.assertTrue(board.is_can(board.CAN_SERVO_FIRST))
        self.assertFalse(board.is_can(board.BOARD_PWM_CHANNELS))

    def test_can_outputs_are_numbered_from_the_first_can_channel(self) -> None:
        """S14 is the node's first output, and the node needs OUT1_FUNCTION = 64."""
        self.assertEqual(board.can_output(14), 1)
        self.assertEqual(board.can_output(17), 4)
        self.assertEqual(board.can_function(14), 64)
        self.assertIsNone(board.can_output(12))

    def test_a_dshot_timer_group_needs_the_motor_test(self) -> None:
        """TIM5 is DShot when any of S3-S6 is a motor; a PWM pulse there is silent."""
        self.assertTrue(board.uses_motor_test(3, [3, 4, 5, 6]))
        self.assertTrue(board.uses_motor_test(6, [3, 4, 5, 6]))
        self.assertFalse(board.uses_motor_test(1, [3, 4, 5, 6]))
        self.assertTrue(board.uses_motor_test(14, [14]))


class TestShippedConfig(unittest.TestCase):
    """The config in the repo has to be one the hardware will honour."""

    def setUp(self) -> None:
        self.cfg = vconfig.load()

    def test_the_shipped_map_has_no_fatal_problems(self) -> None:
        problems = [p for p in board.check_output_map(self.cfg) if p.fatal]
        self.assertEqual(
            [], problems,
            "vector.json describes a map the board cannot honour:\n"
            + "\n".join(f"  [{p.severity}] {p.text}" for p in problems),
        )

    def test_the_only_warnings_are_directions_the_controller_cannot_set(self) -> None:
        """
        A reversed motor on the CAN node is not a map error, but it is something
        the flight controller cannot do, so that warning stays -- and nothing else
        should be hiding among the warnings.
        """
        warnings = [p for p in board.check_output_map(self.cfg) if not p.fatal]
        for problem in warnings:
            with self.subTest(channel=problem.channel):
                self.assertIn("motor wires", problem.text)
                self.assertTrue(board.is_can(problem.channel))

    def test_no_timer_group_mixes_a_servo_with_a_motor(self) -> None:
        servos = set(self.cfg.servo_channels())
        motors = set(self.cfg.motor_channels())
        for group in board.TIMER_GROUPS:
            with self.subTest(group=group.name):
                self.assertFalse(
                    servos.intersection(group.channels) and motors.intersection(group.channels),
                    f"{group.name} carries both a servo and a motor, so it cannot be "
                    "put into one output mode",
                )

    def test_the_config_places_every_mixer_slot(self) -> None:
        """
        An unplaced slot is not a gap, it is a claim on a low channel at the next boot.

        ``set_aux_channel_default`` treats SERVOn_FUNCTION = 0 as unclaimed, so leaving
        a motor slot out does not leave the pin alone -- it hands it to the mixer.
        """
        unplaced = board.unplaced_motor_slots(self.cfg)
        self.assertEqual(
            {}, unplaced,
            "the firmware would claim "
            + ", ".join(f"S{ch} for Motor{n}" for n, ch in unplaced.items()),
        )


class TestDetection(unittest.TestCase):
    """Break the map in each known way and confirm the check says so."""

    def test_a_servo_sharing_a_timer_with_a_motor_is_fatal(self) -> None:
        # S2 is TIM8, alongside the north servos. The servo is lifted off S2 first
        # because a straight double-claim is already refused at load by
        # _check_channel_conflicts; what this covers is the map that loads cleanly
        # and still cannot work.
        def mutate(doc):
            _arm(doc, "north")["outer"]["channel"] = 0
            _arm(doc, "east")["motors"]["bottom"]["channel"] = 2

        cfg = _with(mutate)
        fatal = [p for p in board.check_output_map(cfg) if p.fatal and p.channel == 1]
        self.assertTrue(fatal, "a servo on a DShot timer group was not reported")
        self.assertIn("TIM8", fatal[0].text)

    def test_the_map_as_first_wired_is_caught(self) -> None:
        """
        East motors on S3/S4 next to North's servos on S5/S6: TIM5 spans all four,
        so that group goes DShot and the North servos go silent. The shipped map
        already keeps those motors on TIM5, so this rebuilds the older mixed group.
        """
        def mutate(doc):
            north, east, west = _arm(doc, "north"), _arm(doc, "east"), _arm(doc, "west")
            north["inner"]["channel"], north["outer"]["channel"] = 5, 6
            north["motors"]["top"]["channel"], north["motors"]["bottom"]["channel"] = 1, 2
            east["motors"]["top"]["channel"], east["motors"]["bottom"]["channel"] = 3, 4
            west["inner"]["channel"], west["outer"]["channel"] = 11, 12

        cfg = _with(mutate)
        dead = {p.channel for p in board.check_output_map(cfg) if p.fatal}
        self.assertEqual({5, 6}, dead, "north's servos share TIM5 with east's motors")

    def test_an_unplaced_motor_slot_names_every_servo_it_silences(self) -> None:
        """
        The pin the mixer takes is rarely the pin that goes quiet.

        Motor1 defaults to S1. What the operator notices is that S2 stopped working
        too, because the whole of TIM8 went to DShot, so the report has to name every
        servo in the group rather than only the output that was overwritten.
        """
        def mutate(doc):
            north = _arm(doc, "north")
            north["motors"]["top"]["channel"] = 0        # frees S1, unplaces Motor6
            north["motors"]["bottom"]["channel"] = 0     # frees S2, unplaces Motor1
            north["inner"]["channel"] = 1
            north["outer"]["channel"] = 2

        cfg = _with(mutate)
        fatal = [p for p in board.check_output_map(cfg) if p.fatal]
        self.assertTrue(fatal)
        text = " ".join(p.text for p in fatal)
        self.assertIn("Motor1", text)
        self.assertIn("S1", text)
        self.assertIn("S2", text)

    def test_a_reversed_motor_on_the_can_node_warns_where_direction_is_set(self) -> None:
        """Nothing the flight controller writes reaches an ESC behind the node."""
        def mutate(doc):
            _arm(doc, "south")["motors"]["top"]["reversed"] = True

        cfg = _with(mutate)
        channel = next(c for c in board.can_esc_channels(cfg)
                       if cfg.arm_for_motor_channel(c)[0].motors[cfg.arm_for_motor_channel(c)[1]].reversed)
        problems = [p for p in board.check_output_map(cfg) if p.channel == channel]
        self.assertEqual(1, len(problems))
        self.assertFalse(problems[0].fatal, "a CAN motor is not a broken map")
        self.assertIn("motor wires", problems[0].text)

    def test_esc_node_functions_pack_down_from_the_first_can_channel(self) -> None:
        """With ESC_OF at 13, S14 is RawCommand slot 0 and the node's OUT1 is Motor1."""
        self.assertEqual(33, board.can_esc_function(board.CAN_SERVO_FIRST))
        self.assertEqual(36, board.can_esc_function(board.CAN_SERVO_FIRST + 3))

    def test_a_servo_past_the_nodes_outputs_is_reported(self) -> None:
        cfg = _with(lambda doc: _arm(doc, "west")["inner"].__setitem__("channel", 30))
        problems = [p for p in board.check_output_map(cfg) if p.channel == 30]
        self.assertTrue(problems, "a servo past the node's outputs was not reported")

    def test_the_led_pin_is_flagged_but_not_fatal(self) -> None:
        cfg = _with(lambda doc: _arm(doc, "north")["outer"].__setitem__("channel", 13))
        problems = [p for p in board.check_output_map(cfg) if p.channel == 13]
        self.assertTrue(problems)
        self.assertFalse(any(p.fatal for p in problems), "the LED pin is usable, not broken")

    def test_disabled_channels_are_not_checked(self) -> None:
        """Channel 0 is 'no pin', so it can never be the wrong pin."""
        cfg = _with(lambda doc: _arm(doc, "west")["inner"].__setitem__("channel", 0))
        self.assertFalse(any(p.channel == 0 for p in board.check_output_map(cfg)))


class TestPayload(unittest.TestCase):
    """The UI reads this, so its shape is a contract."""

    def test_payload_covers_every_group_and_the_can_node(self) -> None:
        payload = board.payload(vconfig.load())
        names = [group["name"] for group in payload["groups"]]
        for group in board.TIMER_GROUPS:
            self.assertIn(group.name, names)
        self.assertIn("CAN-to-PWM", names)
        self.assertFalse([p for p in payload["problems"] if p["severity"] == "fatal"])

    def test_a_group_carrying_a_motor_reports_dshot(self) -> None:
        payload = board.payload(vconfig.load())
        modes = {group["name"]: group["mode"] for group in payload["groups"]}
        self.assertEqual("PWM", modes["TIM8"])
        self.assertEqual("DShot", modes["TIM5"])
        self.assertEqual("PWM", modes["TIM4"])
        self.assertEqual("PWM", modes["TIM15"])
        self.assertEqual("CAN", modes["CAN-to-PWM"])

    def test_can_cells_carry_the_node_output_number(self) -> None:
        payload = board.payload(vconfig.load())
        can = next(g for g in payload["groups"] if g["name"] == "CAN-to-PWM")
        first = min(can["outputs"], key=lambda cell: cell["channel"])
        self.assertEqual(board.CAN_SERVO_FIRST, first["channel"])
        self.assertEqual(1, first["canOutput"])
        # An ESC on the node's first output wants Motor1 there, a servo would want 64.
        self.assertEqual(33 if first["kind"] == "motor" else 64, first["nodeFunction"])


if __name__ == "__main__":
    unittest.main()
