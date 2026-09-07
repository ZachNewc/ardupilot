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

import copy
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
from server.config import channel_assigned  # noqa: E402
from server import board  # noqa: E402
from server import frames  # noqa: E402
from server import kinematics as kin  # noqa: E402
from server.bench import MOTOR_STOP_US, Bench  # noqa: E402
from server.controller import LevelController  # noqa: E402
from server.outputs import OutputArbiter, link_budget  # noqa: E402


def make_bench() -> Bench:
    """
    Load the on-disk config, filling any channel-0 slots so tests still exercise
    the mapping and spin paths while the bench is mid-discovery.
    """
    raw = copy.deepcopy(vconfig.load().raw)
    used = set()
    for arm in raw["arms"]:
        for key in ("outer", "inner"):
            channel = (arm.get(key) or {}).get("channel") or 0
            if channel >= 1:
                used.add(channel)
        for motor in (arm.get("motors") or {}).values():
            channel = (motor or {}).get("channel") or 0
            if channel >= 1:
                used.add(channel)
    free = (channel for channel in range(1, 33) if channel not in used)
    for arm in raw["arms"]:
        for key in ("outer", "inner"):
            arm.setdefault(key, {})
            if not arm[key].get("channel"):
                arm[key]["channel"] = next(free)
        for name in ("bottom", "top"):
            arm.setdefault("motors", {}).setdefault(name, {})
            if not arm["motors"][name].get("channel"):
                arm["motors"][name]["channel"] = next(free)
    return Bench(vconfig.from_dict(raw), link=FakeLink())


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
        self.bench.shutdown()

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
            if axis.channel >= 1
        }
        self.assertEqual(channels, expected)
        self.assertTrue(all(pwm == 1500 for _, pwm in self.bench.link.writes))

    def test_planned_arms_are_refused(self) -> None:
        planned = next((arm.id for arm in self.bench.config.arms if not arm.live), None)
        if planned is None:
            edited = copy.deepcopy(self.bench.config.raw)
            edited["arms"][-1]["status"] = "planned"
            self.bench.replace_config(vconfig.from_dict(edited))
            planned = edited["arms"][-1]["id"]
        with self.assertRaisesRegex(ValueError, "marked planned"):
            self.bench.resolve_arms([planned])

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

    def await_spin(self, timeout: float = 8.0) -> None:
        """Wait for the spin worker to finish stopping and releasing the outputs."""
        deadline = time.time() + timeout
        while time.time() < deadline and self.bench.motor_test_active:
            time.sleep(0.01)
        self.bench._halt_spin()

    def live_test_sequences(self) -> set:
        return {
            motor.test_sequence
            for arm in self.bench.config.live_arms()
            for motor in arm.motors.values()
            if channel_assigned(motor.channel) and motor.test_sequence is not None
        }

    def test_one_spin_reaches_every_selected_motor(self) -> None:
        """
        One click starts every selected motor test before any of them is zeroed.

        output_test_seq only updates the named slot, so the earlier wait-then-zero
        between commands is what made the set run one at a time.
        """
        self.bench.spin_motors(None, ["top", "bottom"], 5.0, 0.2)
        self.await_spin()
        tests = self.bench.link.motor_tests
        first_zero = next(i for i, test in enumerate(tests) if test[1] == 0.0)
        started = [seq for seq, percent, _ in tests[:first_zero] if percent > 0]
        self.assertEqual(set(started), self.live_test_sequences())
        for sequence in self.live_test_sequences():
            self.assertGreaterEqual(
                started.count(sequence), 2,
                f"sequence {sequence} was not latched twice",
            )
        self.assertFalse({ch for ch, _ in self.bench.link.writes if board.is_can(ch)})

    def test_spin_drives_every_motor_to_the_same_percent(self) -> None:
        """Equal power means one motor-test percent, not one pulse per motor."""
        self.bench.spin_motors(None, ["top", "bottom"], 5.0, 0.2)
        self.await_spin()
        percent = min(5.0, self.bench.config.bench_limits.motor_percent)
        started = {pct for _, pct, _ in self.bench.link.motor_tests if pct > 0}
        self.assertEqual(started, {percent})

    def test_spin_does_not_start_unselected_motors(self) -> None:
        other = self.bench.config.arm("east").motors["top"]
        self.bench.spin_motors(["north"], ["top"], 5.0, 0.2)
        self.await_spin()
        started = {seq for seq, percent, _ in self.bench.link.motor_tests if percent > 0}
        self.assertNotIn(other.test_sequence, started)
        north = self.bench.config.arm("north").motors["top"]
        self.assertIn(north.test_sequence, started)

    def test_a_dshot_motor_uses_the_motor_test_not_a_pwm_pulse(self) -> None:
        """S3-S6 are TIM5 DShot; a PWM pulse on those pads does nothing."""
        north = self.bench.config.arm("north")
        motor = north.motors["bottom"]
        if not board.uses_motor_test(motor.channel, self.bench.config.motor_channels()):
            self.skipTest("north bottom is not on a DShot group")
        self.bench.spin_motors(["north"], ["bottom"], 5.0, 0.2)
        self.await_spin()
        tested = [seq for seq, percent, _ in self.bench.link.motor_tests if percent > 0]
        self.assertTrue(tested)
        self.assertEqual(set(tested), {motor.test_sequence})
        self.assertFalse([w for w in self.bench.link.writes if w[0] == motor.channel])

    def deafen(self, channel: int, to_function: float) -> None:
        """Silently drop one SERVOn_FUNCTION write, the way a lost PARAM_SET would."""
        real = self.bench.link.set_servo_function

        def drop(ch: int, function: float) -> None:
            if ch == channel and function == to_function:
                return
            real(ch, function)

        self.bench.link.set_servo_function = drop

    def test_a_motor_function_that_will_not_come_back_is_reported(self) -> None:
        """An output left Disabled is a motor that will not fly, so it cannot pass quietly."""
        motor = self.bench.config.arm("east").motors["top"]
        self.deafen(motor.channel, float(motor.function))
        self.bench.spin_motors(["north"], ["top"], 5.0, 0.2)
        self.await_spin()
        self.assertTrue(
            any("will not fly" in error for error in self.bench.link.errors),
            self.bench.link.errors,
        )

    def test_spin_ends_with_a_zero_motor_test(self) -> None:
        self.bench.spin_motors(None, ["top", "bottom"], 5.0, 0.2)
        self.await_spin()
        self.assertEqual(self.bench.link.motor_tests[-1][1], 0.0)
        self.assertFalse(self.bench.link.telemetry().armed)

    def test_motor_spin_is_refused_without_a_motor_function(self) -> None:
        """
        An output with no function is one this cannot hand back to the mixer, and per
        ``assert_output_mapping`` it is Disabled and emits nothing anyway. Cleared here
        rather than assuming the shipped config leaves it unset.
        """
        raw = json.loads(json.dumps(self.bench.config.raw))
        raw["arms"][0]["motors"]["top"]["function"] = None
        self.bench.replace_config(vconfig.from_dict(raw))
        with self.assertRaisesRegex(ValueError, "no motor function configured"):
            self.bench.spin_motors(None, ["top"], 5.0, 1.0)

    def test_spin_disarms_a_leftover_arm_then_runs(self) -> None:
        """A stuck force-arm from the mixer path must not leave Spin refusing forever."""
        self.bench.link.telemetry().armed = True
        self.bench.spin_motors(["north"], ["top"], 5.0, 0.2)
        self.await_spin()
        self.assertIn("disarm", self.bench.link.armed_ops)
        self.assertTrue([test for test in self.bench.link.motor_tests if test[1] > 0])

    def test_motor_spin_is_refused_while_armed(self) -> None:
        """If the board will not disarm, do not start a motor test under it."""
        self.bench.link.telemetry().armed = True
        self.bench.link.force_disarm = lambda: None
        self.bench.link.wait_armed = lambda want, timeout=3.0: False
        with self.assertRaisesRegex(ValueError, "armed"):
            self.bench.spin_motors(None, ["top"], 5.0, 1.0)
        self.assertFalse([test for test in self.bench.link.motor_tests if test[1] > 0])

    def test_motor_spin_is_clamped_to_the_bench_limits(self) -> None:
        limits = self.bench.config.bench_limits
        note = self.bench.spin_motors(["north"], ["top"], 500.0, 900.0)
        # Stopped rather than waited out: the clamp is visible in what was accepted,
        # and the duration limit is half a minute.
        self.bench.stop_motors()
        self.assertIn(f"{limits.motor_percent:.1f}%", note)
        self.assertIn(f"{limits.motor_seconds:.1f}s", note)

    def test_stop_motors_ends_a_running_spin(self) -> None:
        self.bench.spin_motors(None, ["top", "bottom"], 5.0, 30.0)
        self.bench.stop_motors()
        self.assertFalse(self.bench.motor_test_active)
        self.assertEqual(self.bench.link.motor_tests[-1][1], 0.0)

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
                if motor.reversed and channel_assigned(motor.channel) and not board.is_can(motor.channel):
                    expected |= 1 << (motor.channel - 1)
        if expected == 0:
            self.skipTest("no reversed motor is assigned a channel")

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

        Planned motors have to occupy those slots on the DShot group (TIM5), even
        though nothing is plugged in there, or Motor2 lands on S2 and the North
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

    def test_motor_outputs_are_never_left_reversed(self) -> None:
        """
        SERVOn_REVERSED on a motor is not just the wrong place for direction.

        It inverts a range output, so get_limit_pwm(MIN) returns servo_max -- and motors
        are driven to MIN when disarmed. A reversed motor output idles at full throttle.
        """
        for arm in self.bench.config.arms:
            for motor in arm.motors.values():
                self.bench.link.params[f"SERVO{motor.channel}_REVERSED"] = 1.0

        self.bench.assert_output_mapping()

        for arm in self.bench.config.arms:
            for name, motor in arm.motors.items():
                if motor.function is None:
                    continue
                with self.subTest(arm=arm.id, motor=name):
                    self.assertEqual(
                        self.bench.link.params[f"SERVO{motor.channel}_REVERSED"], 0.0
                    )

    def test_stale_motor_function_on_an_unclaimed_channel_is_cleared(self) -> None:
        """
        Moving a motor leaves its function behind on the old pin.

        Two outputs then answer to one mixer slot and the abandoned pin keeps driving
        an ESC, which looks like the wrong propeller spinning rather than a mapping bug.
        """
        claimed = set()
        for arm in self.bench.config.arms:
            for name in ("outer", "inner"):
                claimed.add(arm.gimbal.axis(name).channel)
            for motor in arm.motors.values():
                claimed.add(motor.channel)
        spare = next((ch for ch in range(1, 14) if ch not in claimed), None)
        if spare is None:
            self.skipTest("every onboard channel is claimed in this config")

        self.bench.link.params[f"SERVO{spare}_FUNCTION"] = 33.0
        text = self.bench.assert_output_mapping()

        self.assertEqual(self.bench.link.functions[spare], 0.0)
        self.assertIn(f"S{spare}", text)
        self.assertTrue(
            any("reboot" in err.lower() for err in self.bench.link.errors),
            "clearing a stale motor needs a reboot before the timer mode is right",
        )

    def test_a_claimed_channel_is_never_cleared(self) -> None:
        """The cleanup must not disable an output the config is actively using."""
        self.bench.assert_output_mapping()
        for arm in self.bench.config.arms:
            for name, motor in arm.motors.items():
                if motor.function is None:
                    continue
                with self.subTest(arm=arm.id, motor=name):
                    self.assertEqual(
                        self.bench.link.functions[motor.channel], float(motor.function)
                    )

    def test_an_unreadable_function_is_left_alone(self) -> None:
        """An unanswered read is not evidence that a channel is stale."""
        self.bench.assert_output_mapping()
        cleared = [ch for ch, fn in self.bench.link.functions.items() if fn == 0.0]
        gimbals = {
            arm.gimbal.axis(name).channel
            for arm in self.bench.config.live_arms()
            for name in ("outer", "inner")
        }
        # With no params seeded, nothing is readable, so only gimbals should read 0.
        self.assertEqual(set(cleared), gimbals)

    def test_reverse_mask_change_warns_that_a_reboot_is_needed(self) -> None:
        """
        SERVO_BLH_RVMASK is @RebootRequired, and the HAL only ORs into its reversed
        mask, so a cleared bit does not take effect until boot either.

        Writing it and saying nothing is what made editing `reversed` look applied
        while the ESC kept spinning the old way.
        """
        expected = 0
        for arm in self.bench.config.arms:
            for motor in arm.motors.values():
                if motor.reversed and not board.is_can(motor.channel):
                    expected |= 1 << (motor.channel - 1)
        self.bench.link.params["SERVO_BLH_RVMASK"] = float(expected ^ 0b1)

        self.bench.assert_output_mapping()
        self.assertEqual(self.bench.link.params["SERVO_BLH_RVMASK"], float(expected))
        self.assertTrue(
            any("RVMASK" in err for err in self.bench.link.errors),
            "a direction change the board has not adopted must be reported",
        )

    def test_reverse_mask_already_correct_stays_quiet(self) -> None:
        """No warning when the board already agrees, or it becomes noise to ignore."""
        expected = 0
        for arm in self.bench.config.arms:
            for motor in arm.motors.values():
                if motor.reversed and not board.is_can(motor.channel):
                    expected |= 1 << (motor.channel - 1)
        self.bench.link.params["SERVO_BLH_RVMASK"] = float(expected)

        self.bench.assert_output_mapping()
        self.assertFalse(
            any("RVMASK" in err for err in self.bench.link.errors),
            "an agreeing board must not be reported as needing a reboot",
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
        self.assertEqual(
            [err for err in self.bench.link.errors if "FRAME_" in err], []
        )

    def test_a_motor_recorded_turning_the_wrong_way_is_reported(self) -> None:
        """
        The frame's yaw factors fix each propeller's direction, so a motor recorded
        turning the other way is a real fault: it subtracts from yaw authority and its
        coaxial partner stops cancelling its torque. Nothing on the vehicle says so.
        """
        report = self.bench.check_motor_directions()
        arm = self.bench.config.live_arms()[0]
        role, motor = next(
            (name, m) for name, m in arm.motors.items() if m.function is not None
        )
        expected_wrong = motor.spin != frames.slot_for_function(
            self.bench.config.raw["vehicle"]["frame"]["frame_class"],
            self.bench.config.raw["vehicle"]["frame"]["frame_type"],
            motor.function,
        ).spin
        if expected_wrong:
            self.assertIsNotNone(report)
            self.assertIn(f"{arm.id}.{role}", report)
        # Whatever the current config says, the report must name every disagreement.
        for a in self.bench.config.live_arms():
            for name, m in a.motors.items():
                if m.function is None:
                    continue
                slot = frames.slot_for_function(
                    self.bench.config.raw["vehicle"]["frame"]["frame_class"],
                    self.bench.config.raw["vehicle"]["frame"]["frame_type"],
                    m.function,
                )
                if slot.spin != m.spin:
                    self.assertIn(f"{a.id}.{name}", report or "")

    def test_direction_check_needs_a_frame_it_knows(self) -> None:
        """An unknown frame cannot say which direction is right, so it must not guess."""
        self.bench.config.raw["vehicle"]["frame"]["frame_class"] = 99
        self.assertIsNone(self.bench.check_motor_directions())

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
        shared = {channel: who for channel, who in seen.items() if channel >= 1 and len(who) > 1}
        self.assertEqual(
            shared, {},
            "channels claimed more than once: "
            + "; ".join(f"S{c}: {', '.join(who)}" for c, who in sorted(shared.items())),
        )

    def test_channel_zero_is_not_written_to_the_board(self) -> None:
        """
        Channel 0 means unused. Writing SERVO0_* would be nonsense, and a reverse-mask
        bit of 1 << -1 would throw rather than disable the pin.
        """
        edited = copy.deepcopy(self.bench.config.raw)
        for arm in edited["arms"]:
            if arm["id"] == "north":
                arm["motors"]["top"]["channel"] = 0
                arm["outer"]["channel"] = 0
        self.bench.replace_config(vconfig.from_dict(edited))
        self.bench.assert_output_mapping()

        self.assertNotIn(0, self.bench.link.functions)
        self.assertNotIn("SERVO0_FUNCTION", self.bench.link.params)
        self.assertNotIn("SERVO0_REVERSED", self.bench.link.params)
        bottom = self.bench.config.arm("north").motors["bottom"]
        self.assertEqual(self.bench.link.functions[bottom.channel], float(bottom.function))

    def test_spinning_a_disabled_channel_is_refused(self) -> None:
        edited = copy.deepcopy(self.bench.config.raw)
        for arm in edited["arms"]:
            if arm["id"] == "north":
                arm["motors"]["top"]["channel"] = 0
        self.bench.replace_config(vconfig.from_dict(edited))
        with self.assertRaisesRegex(ValueError, "disabled"):
            self.bench.spin_motors(["north"], ["top"], 5.0, 0.2)

    def test_output_mapping_enables_the_can_node(self) -> None:
        """
        S14+ have no local timer. Without the right DroneCAN bitmask nothing leaves
        the flight controller for the node, and both masks default to 0. Motors go in
        ESC_BM, and only there: a channel in SRV_BM as well would have its throttle sent
        to the node as a servo position too.
        """
        self.bench.link.params["CAN_P1_DRIVER"] = 0.0
        self.bench.link.params["CAN_D1_PROTOCOL"] = 0.0
        self.bench.link.params["CAN_D1_UC_SRV_BM"] = 0.0
        self.bench.link.params["CAN_D1_UC_ESC_BM"] = 0.0
        self.bench.link.params["CAN_D1_UC_OPTION"] = 0.0
        self.bench.link.params["BRD_SAFETY_MASK"] = 0.0

        text = self.bench.assert_output_mapping()
        self.assertIn("CAN ESC mask", text)
        self.assertEqual(self.bench.link.params["CAN_P1_DRIVER"], 1.0)
        self.assertEqual(self.bench.link.params["CAN_D1_PROTOCOL"], 1.0)

        esc_mask = int(self.bench.link.params["CAN_D1_UC_ESC_BM"])
        srv_mask = int(self.bench.link.params["CAN_D1_UC_SRV_BM"])
        for channel in board.can_esc_channels(self.bench.config):
            bit = 1 << (channel - 1)
            self.assertTrue(esc_mask & bit, f"S{channel} must be on the CAN ESC mask")
            self.assertFalse(srv_mask & bit, f"S{channel} is an ESC, not a CAN servo")
        for channel in board.can_servo_channels(self.bench.config):
            self.assertTrue(srv_mask & (1 << (channel - 1)))
        self.assertFalse((esc_mask | srv_mask) & 0x1FFF, "onboard pins never go over CAN")

        # S14 packs down to RawCommand slot 0, so the node's first output is Motor1.
        self.assertEqual(self.bench.link.params["CAN_D1_UC_ESC_OF"], float(board.CAN_ESC_OFFSET))
        self.assertEqual(self.bench.link.params["SERVO_32_ENABLE"], 1.0)
        self.assertTrue(int(self.bench.link.params["CAN_D1_UC_OPTION"]) & 16)
        self.assertEqual(int(self.bench.link.params["BRD_SAFETY_MASK"]) & esc_mask, esc_mask)
        self.assertTrue(
            any("DroneCAN" in err for err in self.bench.link.errors),
            "a bus that was off must say a reboot is required",
        )

    def test_can_motors_are_left_out_of_the_reverse_mask(self) -> None:
        """A bit for a CAN ESC in SERVO_BLH_RVMASK only makes the direction look applied."""
        self.bench.assert_output_mapping()
        mask = int(self.bench.link.params["SERVO_BLH_RVMASK"])
        for channel in board.can_esc_channels(self.bench.config):
            self.assertFalse(mask & (1 << (channel - 1)), f"S{channel} must not be in RVMASK")
        # But the config still says so, and the operator is told where it applies.
        reversed_on_can = [
            motor for arm in self.bench.config.arms for motor in arm.motors.values()
            if motor.reversed and board.is_can(motor.channel)
        ]
        if reversed_on_can:
            self.assertTrue(any("motor wires" in err for err in self.bench.link.errors))

    def test_a_can_motor_probe_goes_through_the_motor_test(self) -> None:
        """
        AP_DroneCAN::SRV_send_esc sends zero unless soft-armed, and only the motor
        test soft-arms while disarmed, so a direct pin write would be silently ignored.
        """
        channels = board.can_esc_channels(self.bench.config)
        if not channels:
            self.skipTest("no motor on the CAN node")
        channel = channels[0]
        found = self.bench.config.arm_for_motor_channel(channel)
        sequence = found[0].motors[found[1]].test_sequence

        note = self.bench.probe_output(channel, "motor", hold_s=0.1)
        self.assertIn(f"sequence {sequence}", note)
        self.assertIn(f"OUT{board.can_output(channel)}_FUNCTION", note)
        self.assertEqual([(sequence, self.bench.config.bench_limits.motor_percent, 0.1)],
                         self.bench.link.motor_tests)
        self.assertFalse([w for w in self.bench.link.writes if w[0] == channel])

    def test_a_pin_probe_follows_the_function_on_the_board(self) -> None:
        """
        Remapping the JSON and probing S3 again used to run a new test sequence on
        the old pad, which looked like the signal moved. The pin tester has to use
        whatever SERVOn_FUNCTION the board actually has.
        """
        channel = 3
        self.bench.link.params[f"SERVO{channel}_FUNCTION"] = 33.0
        note = self.bench.probe_output(channel, "motor", hold_s=0.1)
        self.assertIn("sequence 1", note)
        self.assertIn("on the board", note)
        self.assertEqual(self.bench.link.motor_tests[0][0], 1)

    def test_servo_probe_on_a_can_channel_opens_the_can_mask(self) -> None:
        """Probing a free CAN pin with SRV_BM still at 0 is the failure this exists to stop."""
        self.bench.link.params["CAN_D1_UC_SRV_BM"] = 0.0
        self.bench.link.params["CAN_D1_PROTOCOL"] = 1.0
        self.bench.link.params["CAN_P1_DRIVER"] = 1.0
        # S14 is a motor in the shipped map; a free CAN pin still needs the mask.
        self.bench.probe_output(18, "servo", hold_s=0.0)
        mask = int(self.bench.link.params["CAN_D1_UC_SRV_BM"])
        self.assertTrue(mask & (1 << 17))
        pulses = [pwm for ch, pwm in self.bench.link.writes if ch == 18]
        self.assertGreaterEqual(len(pulses), 2)

    def test_servo_probe_refuses_a_motor_channel(self) -> None:
        """A servo sweep on an ESC is ~1800 us, which looks like a hard throttle burst."""
        channel = board.can_esc_channels(self.bench.config)[0]
        with self.assertRaisesRegex(ValueError, "motor pin"):
            self.bench.probe_output(channel, "servo", hold_s=0.0)

    def test_probe_refuses_channel_zero(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a SERVO"):
            self.bench.probe_output(0, "servo")

    def test_probe_refuses_an_unknown_kind(self) -> None:
        with self.assertRaisesRegex(ValueError, "motor"):
            self.bench.probe_output(1, "esc")

    def test_servo_probe_deflects_then_centres(self) -> None:
        """A mapping check has to move the horn and put it back, or it proves nothing."""
        north = self.bench.config.arm("north")
        channel = north.gimbal.inner.channel
        self.bench.probe_output(channel, "servo", hold_s=0.0)
        pulses = [pwm for ch, pwm in self.bench.link.writes if ch == channel]
        self.assertGreaterEqual(len(pulses), 2)
        self.assertNotEqual(pulses[0], pulses[-1])
        self.assertEqual(pulses[-1], north.gimbal.inner.center_us)
        self.assertEqual(self.bench.link.functions[channel], 0.0)

    def test_motor_probe_spins_the_named_pin(self) -> None:
        """The whole point is to name a pin, not an arm, so a free channel must work."""
        north = self.bench.config.arm("north")
        channel = north.motors["bottom"].channel
        self.bench.link.params[f"SERVO{channel}_FUNCTION"] = float(north.motors["bottom"].function)
        self.bench.probe_output(channel, "motor", hold_s=0.15)
        time.sleep(0.08)
        # motor_test_active clears before the worker finishes restoring the function,
        # so wait on the thread itself.
        self.bench._halt_spin(timeout=2.0)
        if board.uses_motor_test(channel, self.bench.config.motor_channels()):
            tested = [seq for seq, percent, _ in self.bench.link.motor_tests if percent > 0]
            self.assertEqual(tested, [north.motors["bottom"].test_sequence])
            return
        spun = [pwm for ch, pwm in self.bench.link.writes if ch == channel and pwm > MOTOR_STOP_US]
        self.assertTrue(spun, "the named pin never left idle")
        self.assertEqual(
            self.bench.link.functions[channel],
            float(north.motors["bottom"].function),
        )

    def test_each_coaxial_pair_has_a_reversed_motor(self) -> None:
        """
        Both motors in a pair have to push up, so at least one ESC is reversed.

        Skip when the config has not established any reverse bits yet — that is a
        bench step, not a pin-map error.
        """
        if not any(
            motor.reversed
            for arm in self.bench.config.arms
            for motor in arm.motors.values()
        ):
            self.skipTest("no motor is marked reversed yet")
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


class OutputMapReportingTest(unittest.TestCase):
    """
    Asserting the mapping has to say when the map cannot work.

    Every parameter write in ``assert_output_mapping`` succeeds against a board that
    will not honour the result, so success there is not evidence of anything. The
    session this covers: eight motors at ``channel: 0``, gimbal servos on S5-S10, every
    write acknowledged, and only the two servos on TIM15 moved.
    """

    def _bench(self, mutate) -> Bench:
        raw = copy.deepcopy(vconfig.load().raw)
        mutate(raw)
        bench = Bench(vconfig.from_dict(raw), link=FakeLink())
        self.addCleanup(bench.shutdown)
        return bench

    def test_assert_output_mapping_opens_both_esc_telemetry_uarts(self) -> None:
        bench = self._bench(lambda raw: None)
        bench.assert_output_mapping()
        self.assertEqual(16.0, bench.link.params["SERIAL4_PROTOCOL"])
        self.assertEqual(16.0, bench.link.params["SERIAL6_PROTOCOL"])

    def test_wrong_esc_telem_protocol_demands_a_reboot(self) -> None:
        bench = self._bench(lambda raw: None)
        bench.link.params["SERIAL4_PROTOCOL"] = 1.0
        bench.assert_output_mapping()
        self.assertTrue(
            any("SERIAL4_PROTOCOL" in error for error in bench.link.errors),
            f"no SERIAL4 reboot warning; got {bench.link.errors}",
        )

    def test_a_clean_map_reports_nothing(self) -> None:
        bench = self._bench(lambda raw: None)
        bench.assert_output_mapping()
        map_errors = [e for e in bench.link.errors if "timer" in e or "mixer" in e]
        self.assertEqual([], map_errors)

    def test_unplaced_motor_slots_are_reported_by_name(self) -> None:
        def mutate(raw):
            for arm in raw["arms"]:
                arm["outer"]["channel"], arm["inner"]["channel"] = 0, 0
                for motor in arm["motors"].values():
                    motor["channel"] = 0
            north = next(a for a in raw["arms"] if a["id"] == "north")
            north["outer"]["channel"], north["inner"]["channel"] = 5, 6

        bench = self._bench(mutate)
        bench.assert_output_mapping()
        text = " ".join(bench.link.errors)
        self.assertIn("mixer", text)
        self.assertIn("S5", text)
        self.assertIn("S6", text)

    def test_a_servo_on_a_dshot_group_is_reported(self) -> None:
        def mutate(raw):
            north = next(a for a in raw["arms"] if a["id"] == "north")
            east = next(a for a in raw["arms"] if a["id"] == "east")
            north["outer"]["channel"] = 0
            east["outer"]["channel"] = 0                # shipped map also has a servo on S2
            east["motors"]["bottom"]["channel"] = 2     # TIM8, with the remaining TIM8 servo

        bench = self._bench(mutate)
        bench.assert_output_mapping()
        self.assertTrue(
            any("TIM8" in error for error in bench.link.errors),
            f"no timer-group conflict reported; got {bench.link.errors}",
        )

    def test_a_gimbal_pin_holding_a_motor_function_demands_a_reboot(self) -> None:
        """
        The board as it is found after the mixer has already taken the pin.

        Writing Disabled fixes the parameter at once, but the timer group's output mode
        was decided during init, so the pin stays DShot and silent until a restart.
        Without this the operator sees "mapping asserted" and a servo that still will
        not move, which is the loop this whole path exists to break.
        """
        bench = self._bench(lambda raw: None)
        north = bench.config.arm("north")
        seized = north.gimbal.outer.channel
        bench.link.params[f"SERVO{seized}_FUNCTION"] = 33.0

        bench.assert_output_mapping()
        reboot = [e for e in bench.link.errors if "reboot" in e.lower()]
        self.assertTrue(reboot, f"no reboot warning; got {bench.link.errors}")
        self.assertTrue(any(f"S{seized}" in e for e in reboot))
        # And the parameter itself is corrected, not merely reported.
        self.assertEqual(0.0, bench.link.params[f"SERVO{seized}_FUNCTION"])

    def test_probing_a_can_channel_names_the_node_output_and_its_function(self) -> None:
        """
        The one thing the flight controller cannot fix for itself.

        ``OUTx_FUNCTION`` lives on the node. If it is wrong the channel is silent and no
        flight-controller parameter explains why, so the probe has to say so out loud.
        """
        bench = self._bench(lambda raw: None)
        note = bench.probe_output(14, "motor", hold_s=0.0)
        self.assertIn("OUT1_FUNCTION", note)
        self.assertIn("33", note)
        self.assertIn("east", note)


if __name__ == "__main__":
    unittest.main()
