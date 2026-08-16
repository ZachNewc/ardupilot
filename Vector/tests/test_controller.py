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
        self.assertEqual(channels, {1, 2})
        self.assertTrue(all(pwm == 1500 for _, pwm in self.bench.link.writes))

    def test_planned_arms_are_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "marked planned"):
            self.bench.resolve_arms(["east"])

    def test_all_resolves_to_live_arms_only(self) -> None:
        self.assertEqual([arm.id for arm in self.bench.resolve_arms(None)], ["north"])
        self.assertEqual([arm.id for arm in self.bench.resolve_arms(["all"])], ["north"])

    def test_aim_disables_the_servo_function_first(self) -> None:
        """DO_SET_SERVO is ignored unless the output function is Disabled."""
        self.bench.aim(None, 5.0, 0.0)
        self.drain()
        self.assertEqual(self.bench.link.functions, {1: 0, 2: 0})

    def test_aim_produces_the_expected_pulse_widths(self) -> None:
        # A pure forward lean on the north arm is the inner axis' job.
        self.bench.aim(None, 10.0, 0.0)
        self.drain()
        sent = dict(self.bench.link.writes)
        self.assertEqual(sent[1], 1500)
        self.assertNotEqual(sent[2], 1500)

        arm = self.bench.config.arm("north")
        tilt = kin.tilt_for_pwm(arm.gimbal, sent[1], sent[2])
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
        self.bench.spin_motors(None, ["top"], 500.0, 900.0)
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
        for arm in self.bench.config.live_arms():
            for motor in arm.motors.values():
                if motor.reversed:
                    expected |= 1 << (motor.channel - 1)
        self.assertNotEqual(expected, 0, "config should have at least one reversed motor")

        self.bench.assert_output_mapping()
        self.assertEqual(self.bench.link.params["SERVO_BLH_RVMASK"], float(expected))
        self.assertEqual(self.bench.link.params["SERVO_DSHOT_ESC"], 1)

    def test_only_top_motors_are_reversed(self) -> None:
        """Both motors in a coaxial pair must push up, so exactly one is reversed."""
        for arm in self.bench.config.arms:
            with self.subTest(arm=arm.id):
                reversed_roles = {
                    role for role, motor in arm.motors.items() if motor.reversed
                }
                self.assertEqual(reversed_roles, {"top"})


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
