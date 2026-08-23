#!/usr/bin/env python3
"""
AP_FLAKE8_CLEAN
Tests for the bench levelling law and the output arbiter.

Run with::

    python3 -m unittest discover -s Vector/tests -v

The sign tests matter: getting one backwards drives the gimbals the wrong way and
turns a levelling demo into a divergent one, which is exactly the failure a bench is
supposed to catch before a flight does.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "Vector", "dashboard"))

from fakes import FakeLink  # noqa: E402
from server import config as vconfig  # noqa: E402
from server import kinematics as kin  # noqa: E402
from server.bench import Bench  # noqa: E402
from server.controller import LevelController  # noqa: E402
from server.outputs import OutputArbiter, link_budget  # noqa: E402


def make_bench() -> Bench:
    return Bench(vconfig.load(), link=FakeLink())


class LevellingLawTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bench = make_bench()
        self.controller = LevelController(self.bench)

    def tearDown(self) -> None:
        self.bench.outputs.stop()

    def lean(self, roll: float, pitch: float, roll_rate: float = 0.0, pitch_rate: float = 0.0):
        return self.controller.desired_lean(roll, pitch, roll_rate, pitch_rate)

    def test_level_airframe_needs_no_lean(self) -> None:
        forward, right, saturated = self.lean(0.0, 0.0)
        self.assertAlmostEqual(forward, 0.0, delta=1e-9)
        self.assertAlmostEqual(right, 0.0, delta=1e-9)
        self.assertFalse(saturated)

    def test_nose_up_leans_thrust_forward(self) -> None:
        """
        Positive pitch is nose up, which tips the airframe's own up vector aft, so the
        gimbals must lean the thrust forward to keep it pointing at world vertical.
        """
        forward, right, _ = self.lean(roll=0.0, pitch=10.0)
        self.assertAlmostEqual(forward, 10.0, delta=1e-6)
        self.assertAlmostEqual(right, 0.0, delta=1e-9)

    def test_right_side_down_leans_thrust_left(self) -> None:
        """
        Positive roll puts the right side down, which tips the airframe's up vector to
        the right, so the correction goes left. This is the sign that is easiest to get
        wrong because it is opposite to the pitch case.
        """
        forward, right, _ = self.lean(roll=10.0, pitch=0.0)
        self.assertAlmostEqual(right, -10.0, delta=1e-6)
        self.assertAlmostEqual(forward, 0.0, delta=1e-9)

    def test_law_matches_world_up_exactly(self) -> None:
        """
        At unity gain the commanded lean must be the true world-up direction.

        Cases stay inside the 22.5 degree envelope so the cap does not participate;
        saturation is covered separately below.
        """
        for roll, pitch in ((0.0, 0.0), (8.0, 0.0), (0.0, -12.0), (5.0, 7.0), (-12.0, 14.0)):
            with self.subTest(roll=roll, pitch=pitch):
                forward, right, _ = self.lean(roll, pitch)
                expected = kin.lean_of_vector(kin.world_up_in_body(roll, pitch))
                self.assertAlmostEqual(forward, expected[0], delta=1e-6)
                self.assertAlmostEqual(right, expected[1], delta=1e-6)

    def test_zero_gain_holds_the_gimbals_with_the_airframe(self) -> None:
        self.controller.configure(level_gain=0.0)
        forward, right, _ = self.lean(roll=15.0, pitch=15.0)
        self.assertAlmostEqual(forward, 0.0, delta=1e-9)
        self.assertAlmostEqual(right, 0.0, delta=1e-9)

    def test_half_gain_gives_partial_correction(self) -> None:
        self.controller.configure(level_gain=0.5)
        half, _, _ = self.lean(roll=0.0, pitch=10.0)
        self.controller.configure(level_gain=1.0)
        full, _, _ = self.lean(roll=0.0, pitch=10.0)
        self.assertGreater(half, 0.0)
        self.assertLess(half, full)

    def test_lead_time_extrapolates_the_attitude(self) -> None:
        """A 100 ms lead on 50 deg/s should anticipate roughly 5 degrees of motion."""
        self.controller.configure(lead_time_s=0.1)
        forward, _, _ = self.lean(roll=0.0, pitch=0.0, pitch_rate=50.0)
        self.assertAlmostEqual(forward, 5.0, delta=0.1)

    def test_zero_lead_ignores_rates(self) -> None:
        self.controller.configure(lead_time_s=0.0)
        forward, right, _ = self.lean(0.0, 0.0, roll_rate=120.0, pitch_rate=-90.0)
        self.assertAlmostEqual(forward, 0.0, delta=1e-9)
        self.assertAlmostEqual(right, 0.0, delta=1e-9)

    def test_inversion_flips_the_correction(self) -> None:
        base, _, _ = self.lean(roll=0.0, pitch=10.0)
        self.controller.configure(invert_pitch=True)
        flipped, _, _ = self.lean(roll=0.0, pitch=10.0)
        self.assertAlmostEqual(flipped, -base, delta=1e-6)

    def test_lean_is_capped_at_the_mechanical_limit(self) -> None:
        """A 60 degree tilt cannot be corrected; the command must stop at the envelope."""
        forward, right, saturated = self.lean(roll=0.0, pitch=60.0)
        self.assertTrue(saturated)
        self.assertLessEqual(abs(forward), self.controller.tilt_cap_deg() + 1e-6)
        self.assertAlmostEqual(abs(forward), 22.5, delta=1e-6)
        self.assertAlmostEqual(right, 0.0, delta=1e-9)

    def test_cap_fraction_reduces_the_limit(self) -> None:
        self.controller.configure(max_tilt_fraction=0.5)
        self.assertAlmostEqual(self.controller.tilt_cap_deg(), 11.25, delta=1e-6)
        forward, _, saturated = self.lean(roll=0.0, pitch=45.0)
        self.assertTrue(saturated)
        self.assertAlmostEqual(abs(forward), 11.25, delta=1e-6)

    def test_capping_preserves_direction(self) -> None:
        """Over the limit, the lean must shrink without rotating."""
        forward, right, saturated = self.lean(roll=-40.0, pitch=40.0)
        self.assertTrue(saturated)
        uncapped = kin.lean_of_vector(kin.world_up_in_body(-40.0, 40.0))
        self.assertAlmostEqual(
            forward / right, uncapped[0] / uncapped[1], delta=1e-6
        )


class BenchOperationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bench = make_bench()

    def tearDown(self) -> None:
        self.bench.outputs.stop()

    def drain(self, timeout: float = 1.0) -> None:
        """Wait for the arbiter's worker to flush pending writes."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.bench.link.writes and not self.bench.outputs.ramp_active():
                return
            time.sleep(0.01)

    def test_centering_writes_both_axes_of_the_live_arm(self) -> None:
        self.bench.center()
        self.drain()
        channels = {channel for channel, _ in self.bench.link.writes}
        expected = {
            axis.channel
            for arm in self.bench.config.live_arms()
            for axis in (arm.gimbal.outer, arm.gimbal.inner)
        }
        self.assertEqual(channels, expected)
        self.assertTrue(all(pwm == 1500 for _, pwm in self.bench.link.writes))

    def test_planned_arms_are_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "marked planned"):
            self.bench.resolve_arms(["east"])

    def test_all_resolves_to_live_arms_only(self) -> None:
        live = [arm.id for arm in self.bench.config.live_arms()]
        self.assertEqual([arm.id for arm in self.bench.resolve_arms(None)], live)
        self.assertEqual([arm.id for arm in self.bench.resolve_arms(["all"])], live)

    def test_aim_disables_the_servo_function_first(self) -> None:
        """DO_SET_SERVO is ignored unless the output function is Disabled."""
        self.bench.aim(None, 5.0, 0.0)
        self.drain()
        expected = {
            axis.channel: 0
            for arm in self.bench.config.live_arms()
            for axis in (arm.gimbal.outer, arm.gimbal.inner)
        }
        self.assertEqual(self.bench.link.functions, expected)

    def test_aim_produces_the_expected_pulse_widths(self) -> None:
        # A pure forward lean on the north arm is the inner axis' job. Aimed at
        # north only so a second live arm cannot muddy the channel map.
        self.bench.aim(["north"], 10.0, 0.0)
        self.drain()
        sent = dict(self.bench.link.writes)
        arm = self.bench.config.arm("north")
        outer_ch = arm.gimbal.outer.channel
        inner_ch = arm.gimbal.inner.channel
        self.assertEqual(sent[outer_ch], 1500)
        self.assertNotEqual(sent[inner_ch], 1500)

        tilt = kin.tilt_for_pwm(arm.gimbal, sent[outer_ch], sent[inner_ch])
        lean = kin.thrust_lean(arm.mount_yaw_deg, *tilt)
        self.assertAlmostEqual(lean[0], 10.0, delta=0.2)
        self.assertAlmostEqual(lean[1], 0.0, delta=0.2)

    def test_motor_spin_is_refused_without_a_test_sequence(self) -> None:
        """
        An unset sequence must refuse rather than guess.

        The number is frame-dependent, so guessing spins some other motor. Cleared
        here rather than assuming the shipped config leaves it unset.
        """
        raw = json.loads(json.dumps(self.bench.config.raw))
        raw["arms"][0]["motors"]["top"]["test_sequence"] = None
        self.bench.replace_config(vconfig.from_dict(raw))
        with self.assertRaisesRegex(ValueError, "no test_sequence configured"):
            self.bench.spin_motors(None, ["top"], 5.0, 1.0)

    def test_motor_spin_is_clamped_to_the_bench_limits(self) -> None:
        raw = json.loads(json.dumps(self.bench.config.raw))
        raw["arms"][0]["motors"]["top"]["test_sequence"] = 3
        self.bench.replace_config(vconfig.from_dict(raw))
        self.bench.spin_motors(["north"], ["top"], 500.0, 900.0)
        sequence, percent, seconds = self.bench.link.motor_tests[-1]
        self.assertEqual(sequence, 3)
        self.assertAlmostEqual(percent, self.bench.config.bench_limits.motor_percent)
        self.assertAlmostEqual(seconds, self.bench.config.bench_limits.motor_seconds)

    def test_output_mapping_builds_the_reverse_mask_from_config(self) -> None:
        """
        The reverse mask must be derived from the config, not hard-coded anywhere.

        Built here from the same source so that moving a motor to another output
        channel does not need this test edited -- what is asserted is the rule
        (bit N is SERVO N+1, set only for reversed motors), not one vehicle's answer.
        """
        expected = 0
        for arm in self.bench.config.arms:
            for motor in arm.motors.values():
                if motor.reversed:
                    expected |= 1 << (motor.channel - 1)
        self.assertNotEqual(expected, 0, "config should have at least one reversed motor")

        self.bench.assert_output_mapping()
        self.assertEqual(self.bench.link.params["SERVO_BLH_RVMASK"], float(expected))
        self.assertEqual(self.bench.link.params["SERVO_DSHOT_ESC"], 1)

    def test_output_mapping_pushes_the_gimbal_pulse_window_to_the_board(self) -> None:
        """
        DO_SET_SERVO is clamped by SERVOn_MIN/MAX on the flight controller.

        A board left at the 1000-2000 default silently discards the ends of a 500-2500
        gimbal window, which looks exactly like a servo that will not reach its limit.
        The config's window therefore has to be asserted, not assumed.
        """
        self.bench.assert_output_mapping()
        params = self.bench.link.params
        for arm in self.bench.config.live_arms():
            for name in ("outer", "inner"):
                axis = arm.gimbal.axis(name)
                with self.subTest(arm=arm.id, axis=name):
                    self.assertEqual(params[f"SERVO{axis.channel}_MIN"], float(axis.min_us))
                    self.assertEqual(params[f"SERVO{axis.channel}_MAX"], float(axis.max_us))
                    self.assertEqual(params[f"SERVO{axis.channel}_TRIM"], float(axis.center_us))
                    # Axis direction lives in the config, so the board must not add its own.
                    self.assertEqual(params[f"SERVO{axis.channel}_REVERSED"], 0.0)

    def test_planned_motor_functions_are_placed_so_the_mixer_cannot_take_servo_pins(self) -> None:
        """
        An octaquad mixer has eight motor slots. Unclaimed ones get defaulted onto
        S1, S2, ... on the next boot, which is TIM8 -- the North gimbals.

        Planned East/West motors have to occupy those slots on the DShot groups, even
        though the dashboard will not spin them, or Motor2 lands on S2 and the North
        servos come up as DShot.
        """
        self.bench.assert_output_mapping()
        for arm in self.bench.config.arms:
            for name, motor in arm.motors.items():
                if motor.function is None:
                    continue
                with self.subTest(arm=arm.id, motor=name):
                    self.assertEqual(
                        self.bench.link.functions[motor.channel], float(motor.function)
                    )
        north = self.bench.config.arm("north")
        self.assertEqual(self.bench.link.functions[north.gimbal.inner.channel], 0.0)
        self.assertEqual(self.bench.link.functions[north.gimbal.outer.channel], 0.0)

    def test_output_mapping_assigns_motor_functions(self) -> None:
        """
        A motor channel left Disabled emits nothing, so the function has to be written.

        Moving a gimbal onto a channel that used to carry a motor erases that motor's
        function, because the gimbal needs the output Disabled and PARAM_SET persists.
        Functions are assigned here rather than read from the config so the test proves
        the rule even while the vehicle's own motor numbers are still unestablished.
        """
        expected = {}
        for index, arm in enumerate(self.bench.config.live_arms()):
            for offset, motor in enumerate(arm.motors.values()):
                function = 33 + index * 2 + offset
                object.__setattr__(motor, "function", function)
                expected[motor.channel] = float(function)
        self.assertTrue(expected, "config should have live motors")

        self.bench.assert_output_mapping()
        for channel, function in expected.items():
            with self.subTest(channel=channel):
                self.assertEqual(self.bench.link.functions[channel], function)

    def test_output_mapping_reports_motors_with_no_function(self) -> None:
        """An unassigned motor is called out, because it cannot spin and looks like a fault."""
        arm = self.bench.config.live_arms()[0]
        motor = next(iter(arm.motors.values()))
        object.__setattr__(motor, "function", None)

        text = self.bench.assert_output_mapping()
        self.assertIn("No motor function set", text)
        self.assertIn(f"S{motor.channel}", text)
        # An unmapped motor must be left alone, not disabled on a guess.
        self.assertNotIn(motor.channel, self.bench.link.functions)

    def test_gimbal_channels_stay_disabled(self) -> None:
        """DO_SET_SERVO is only honoured on a disabled output, so this must not regress."""
        self.bench.assert_output_mapping()
        for arm in self.bench.config.live_arms():
            for name in ("outer", "inner"):
                channel = arm.gimbal.axis(name).channel
                with self.subTest(arm=arm.id, axis=name):
                    self.assertEqual(self.bench.link.functions[channel], 0.0)

    def test_no_channel_is_both_a_gimbal_and_a_motor(self) -> None:
        """
        The two roles want opposite functions, so a shared channel can only be wrong.

        This is the failure that broke the bench after the pin remap: South's servos
        took S3/S4, which had been carrying North's motors.
        """
        gimbals = {}
        motors = {}
        for arm in self.bench.config.arms:
            for name in ("outer", "inner"):
                gimbals[arm.gimbal.axis(name).channel] = f"{arm.id}.{name}"
            for name, motor in arm.motors.items():
                motors[motor.channel] = f"{arm.id}.{name}"
        clash = set(gimbals) & set(motors)
        self.assertEqual(
            clash, set(),
            "channels serve both a gimbal and a motor: "
            + ", ".join(f"S{c}: {gimbals[c]} vs {motors[c]}" for c in sorted(clash)),
        )

    def test_frame_mismatch_is_reported_as_an_error(self) -> None:
        """
        A board on the wrong frame makes the config's motor numbers meaningless.

        This is the failure that looked like broken wiring: on a 4-motor frame, Motor6
        and Motor8 do not exist, so those outputs are never driven and nothing refuses
        the command. It has to be surfaced loudly because it produces no other symptom.
        """
        self.bench.link.params["FRAME_CLASS"] = 1.0
        self.bench.link.params["FRAME_TYPE"] = 1.0

        mismatch = self.bench.check_frame()
        self.assertIsNotNone(mismatch)
        self.assertIn("FRAME_CLASS is 1", mismatch)
        self.assertIn("reboot", mismatch)

        self.bench.assert_output_mapping()
        self.assertTrue(
            any("FRAME_CLASS" in err for err in self.bench.link.errors),
            "a frame mismatch must reach the operator, not just the return value",
        )

    def test_frame_match_reports_nothing(self) -> None:
        """A correct board must stay quiet, or the warning becomes noise to ignore."""
        frame = self.bench.config.raw["vehicle"]["frame"]
        self.bench.link.params["FRAME_CLASS"] = float(frame["frame_class"])
        self.bench.link.params["FRAME_TYPE"] = float(frame["frame_type"])

        self.assertIsNone(self.bench.check_frame())
        self.bench.assert_output_mapping()
        self.assertEqual(self.bench.link.errors, [])

    def test_frame_check_is_silent_when_the_board_does_not_answer(self) -> None:
        """An unreadable parameter is not evidence of a mismatch, so it must not claim one."""
        self.assertIsNone(self.bench.check_frame())

    def test_motor_functions_and_test_orders_are_unique(self) -> None:
        """
        Two motors sharing a function or a test order is silent but wrong.

        A duplicated SERVOn_FUNCTION makes two outputs mirror one mixer slot, and a
        duplicated test order makes the bench spin a motor the operator did not name.
        Neither reports anything, so it has to be caught here.
        """
        functions = {}
        orders = {}
        for arm in self.bench.config.arms:
            for name, motor in arm.motors.items():
                where = f"{arm.id}.{name}"
                if motor.function is not None:
                    self.assertNotIn(
                        motor.function, functions,
                        f"{where} reuses function {motor.function} from {functions.get(motor.function)}",
                    )
                    functions[motor.function] = where
                if motor.test_sequence is not None:
                    self.assertNotIn(
                        motor.test_sequence, orders,
                        f"{where} reuses test order {motor.test_sequence} "
                        f"from {orders.get(motor.test_sequence)}",
                    )
                    orders[motor.test_sequence] = where

    def test_every_output_channel_is_used_once(self) -> None:
        """Two roles on one channel means at least one of them is not driving anything."""
        seen = {}
        for arm in self.bench.config.arms:
            for name in ("outer", "inner"):
                seen.setdefault(arm.gimbal.axis(name).channel, []).append(f"{arm.id}.{name}")
            for name, motor in arm.motors.items():
                seen.setdefault(motor.channel, []).append(f"{arm.id}.{name}")
        shared = {channel: who for channel, who in seen.items() if len(who) > 1}
        self.assertEqual(
            shared, {},
            "channels claimed more than once: "
            + "; ".join(f"S{c}: {', '.join(who)}" for c, who in sorted(shared.items())),
        )

    def test_each_coaxial_pair_has_a_reversed_motor(self) -> None:
        """
        Both motors in a pair have to push up, so at least one ESC is reversed.

        North currently has both reversed — that is as-wired, not a pin-map error —
        so this asserts the safety property (not both unreversed) rather than a
        particular role.
        """
        for arm in self.bench.config.arms:
            with self.subTest(arm=arm.id):
                self.assertTrue(
                    any(motor.reversed for motor in arm.motors.values()),
                    f"{arm.id} has no reversed motor",
                )


class ArbiterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sent: list = []
        self.prepared: list = []
        self.arbiter = OutputArbiter(
            send=lambda channel, pwm: self.sent.append((channel, pwm)),
            prepare=lambda channels: self.prepared.extend(channels),
            on_error=lambda message: None,
            rate_hz=200.0,
        )

    def tearDown(self) -> None:
        self.arbiter.stop()

    def wait_for(self, predicate, timeout: float = 1.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.005)
        return False

    def test_writes_reach_the_link(self) -> None:
        generation = self.arbiter.acquire("test")
        self.arbiter.write({1: 1600, 2: 1400}, generation)
        self.assertTrue(self.wait_for(lambda: len(self.sent) >= 2))
        self.assertEqual(dict(self.sent), {1: 1600, 2: 1400})

    def test_unchanged_values_are_not_resent(self) -> None:
        generation = self.arbiter.acquire("test")
        self.arbiter.write({1: 1600}, generation)
        self.assertTrue(self.wait_for(lambda: len(self.sent) == 1))
        self.arbiter.write({1: 1600}, generation)
        time.sleep(0.05)
        self.assertEqual(len(self.sent), 1)

    def test_acquiring_supersedes_the_previous_owner(self) -> None:
        first = self.arbiter.acquire("live")
        second = self.arbiter.acquire("stabilize")
        self.assertNotEqual(first, second)
        self.assertFalse(self.arbiter.write({1: 1700}, first))
        self.assertTrue(self.arbiter.write({1: 1700}, second))
        self.assertEqual(self.arbiter.owner, "stabilize")

    def test_release_only_affects_the_current_owner(self) -> None:
        self.arbiter.acquire("live")
        self.arbiter.release("stabilize")
        self.assertEqual(self.arbiter.owner, "live")
        self.arbiter.release("live")
        self.assertIsNone(self.arbiter.owner)

    def test_ramp_interpolates_then_lands_on_target(self) -> None:
        generation = self.arbiter.acquire("test")
        self.arbiter.ramp({1: 2000}, {1: 1000}, duration_s=0.25, generation=generation)
        self.assertTrue(self.wait_for(lambda: any(pwm == 2000 for _, pwm in self.sent), timeout=2.0))
        values = [pwm for channel, pwm in self.sent if channel == 1]
        self.assertGreater(len(values), 3, "a ramp should produce intermediate steps")
        self.assertEqual(values[-1], 2000)
        self.assertEqual(values, sorted(values), "a ramp must be monotonic")

    def test_a_new_owner_cancels_an_in_flight_ramp(self) -> None:
        generation = self.arbiter.acquire("live")
        self.arbiter.ramp({1: 2000}, {1: 1000}, duration_s=5.0, generation=generation)
        self.assertTrue(self.wait_for(lambda: len(self.sent) > 0))
        self.arbiter.acquire("stabilize")
        self.assertTrue(self.wait_for(lambda: not self.arbiter.ramp_active()))
        count = len(self.sent)
        time.sleep(0.05)
        self.assertEqual(len(self.sent), count, "the cancelled ramp must stop writing")

    def test_worker_stops_when_idle_and_restarts_on_demand(self) -> None:
        generation = self.arbiter.acquire("test")
        self.arbiter.write({1: 1600}, generation)
        self.assertTrue(self.wait_for(lambda: len(self.sent) == 1))
        # Worker retires after the idle timeout, then a later write must revive it.
        self.assertTrue(
            self.wait_for(
                lambda: not any(t.name == "vector-outputs" for t in threading.enumerate()),
                timeout=6.0,
            )
        )
        self.arbiter.write({1: 1700}, generation)
        self.assertTrue(self.wait_for(lambda: len(self.sent) == 2, timeout=2.0))


class LinkBudgetTest(unittest.TestCase):
    def test_usb_has_ample_headroom(self) -> None:
        budget = link_budget(channels=8, rate_hz=25.0, baud=115200, usb=True)
        self.assertFalse(budget.over_budget)
        self.assertLess(budget.utilisation, 0.05)

    def test_a_radio_link_cannot_carry_eight_servos_at_speed(self) -> None:
        """The number that justifies keeping the command rate configurable."""
        budget = link_budget(channels=8, rate_hz=25.0, baud=115200, usb=False)
        self.assertTrue(budget.over_budget)

    def test_dropping_the_rate_brings_a_radio_link_back_in_budget(self) -> None:
        budget = link_budget(channels=8, rate_hz=5.0, baud=115200, usb=False)
        self.assertFalse(budget.over_budget)


if __name__ == "__main__":
    unittest.main()
