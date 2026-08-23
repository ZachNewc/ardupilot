#!/usr/bin/env python3
"""
AP_FLAKE8_CLEAN
Arm-level operations for the Vector bench.

Sits between the raw MAVLink link and the WebSocket protocol. Everything here is
expressed in terms of arms and thrust directions; nothing here knows about JSON or
sockets, and nothing here hardcodes a channel number.

Every command names an owner when it takes the outputs, so a live pointer drag, a
timed interpolation and the stabiliser can never end up sending to the same servo at
once. See ``outputs.OutputArbiter``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import kinematics as kin
from .config import ArmConfig, VehicleConfig
from .link import FUNC_DISABLED, MavlinkLink
from .outputs import LinkBudget, OutputArbiter, link_budget

OWNER_MANUAL = "manual"
OWNER_LIVE = "live"
OWNER_STABILIZE = "stabilize"

# DShot reverse direction is asserted through BLHeli passthrough.
DSHOT_ESC_BLHELI = 1


@dataclass(frozen=True)
class ArmState:
    """Where one arm's gimbal actually is, read back from SERVO_OUTPUT_RAW."""

    arm_id: str
    outer_us: int
    inner_us: int
    tilt_outer: float
    tilt_inner: float
    forward_deg: float
    right_deg: float
    outer_at_limit: bool
    inner_at_limit: bool


class Bench:
    """Vehicle operations built on a config, a link, and one output arbiter."""

    def __init__(self, cfg: VehicleConfig, link: Optional[MavlinkLink] = None) -> None:
        self._cfg = cfg
        self.link = link or MavlinkLink()
        self._lock = threading.Lock()
        self._motor_test_until = 0.0
        self.outputs = OutputArbiter(
            send=self.link.set_servo_pwm_fast,
            prepare=self._prepare_channels,
            on_error=self.link.record_error,
            rate_hz=cfg.bench_limits.command_rate_hz,
        )

    # ------------------------------------------------------------------
    # config
    # ------------------------------------------------------------------
    @property
    def config(self) -> VehicleConfig:
        return self._cfg

    def replace_config(self, cfg: VehicleConfig) -> None:
        """Swap in an edited config. Outputs are released so nothing keeps driving stale channels."""
        self.outputs.acquire("config-reload")
        self.outputs.release("config-reload")
        self.outputs.forget()
        self.link.forget_servo_functions()
        with self._lock:
            self._cfg = cfg

    def resolve_arms(self, arm_ids: Optional[Sequence[str]]) -> List[ArmConfig]:
        """
        Turn a request's arm list into arms that are safe to drive.

        No list, or ``["all"]``, means every live arm. Naming a planned arm is an
        error rather than a silent no-op, so a typo in the config is visible.
        """
        if not arm_ids or list(arm_ids) == ["all"]:
            arms = self._cfg.live_arms()
            if not arms:
                raise ValueError("no arms are marked live in the config")
            return arms

        arms = []
        for arm_id in arm_ids:
            arm = self._cfg.arm(arm_id)
            if not arm.live:
                raise ValueError(f"arm '{arm_id}' is marked {arm.status}, not live")
            arms.append(arm)
        return arms

    # ------------------------------------------------------------------
    # output plumbing
    # ------------------------------------------------------------------
    def _prepare_channels(self, channels: Iterable[int]) -> None:
        """DO_SET_SERVO is refused unless the output's function is Disabled."""
        for channel in channels:
            self.link.set_servo_function(channel, FUNC_DISABLED)

    def _current_pwm(self, arms: Sequence[ArmConfig]) -> Dict[int, int]:
        values: Dict[int, int] = {}
        for arm in arms:
            for name in ("outer", "inner"):
                axis = arm.gimbal.axis(name)
                values[axis.channel] = self.link.servo_pwm(axis.channel, axis.center_us)
        return values

    def _apply(
        self,
        solutions: Sequence[Tuple[ArmConfig, kin.GimbalSolution]],
        speed_deg_s: float,
        owner: str,
    ) -> Tuple[float, bool]:
        """
        Send a set of gimbal solutions, optionally interpolated.

        The axis with the most travel sets the duration and every other axis is
        slowed to match, so all arms and both axes arrive together instead of the
        thrust vector sweeping through directions nobody asked for.
        """
        generation = self.outputs.acquire(owner)
        targets: Dict[int, int] = {}
        for arm, answer in solutions:
            for channel, pwm in answer.pwm():
                targets[channel] = pwm

        speed = max(0.0, min(self._cfg.bench_limits.servo_speed_deg_s, float(speed_deg_s)))
        if speed <= 0.0:
            self.outputs.write(targets, generation)
            return 0.0, any(answer.clamped for _, answer in solutions)

        starts = self._current_pwm([arm for arm, _ in solutions])
        travel_deg = 0.0
        for arm, answer in solutions:
            for name in ("outer", "inner"):
                axis = arm.gimbal.axis(name)
                target_us = answer.outer.pwm_us if name == "outer" else answer.inner.pwm_us
                start_us = starts.get(axis.channel, axis.center_us)
                travel_deg = max(travel_deg, abs(target_us - start_us) / axis.us_per_deg)

        duration_s = travel_deg / speed if travel_deg > 0.05 else 0.0
        if duration_s <= 0.0:
            self.outputs.write(targets, generation)
        else:
            self.outputs.ramp(targets, starts, duration_s, generation)
        return duration_s, any(answer.clamped for _, answer in solutions)

    # ------------------------------------------------------------------
    # reading back
    # ------------------------------------------------------------------
    def arm_state(self, arm: ArmConfig) -> ArmState:
        outer_us = self.link.servo_pwm(arm.gimbal.outer.channel, arm.gimbal.outer.center_us)
        inner_us = self.link.servo_pwm(arm.gimbal.inner.channel, arm.gimbal.inner.center_us)
        tilt_outer, tilt_inner = kin.tilt_for_pwm(arm.gimbal, outer_us, inner_us)
        forward, right = kin.thrust_lean(arm.mount_yaw_deg, tilt_outer, tilt_inner)

        outer_window = kin.servo_deg_window(arm.gimbal.outer)
        inner_window = kin.servo_deg_window(arm.gimbal.inner)
        outer_deg = arm.gimbal.outer.us_to_deg(outer_us)
        inner_deg = arm.gimbal.inner.us_to_deg(inner_us)

        return ArmState(
            arm_id=arm.id,
            outer_us=outer_us,
            inner_us=inner_us,
            tilt_outer=tilt_outer,
            tilt_inner=tilt_inner,
            forward_deg=forward,
            right_deg=right,
            outer_at_limit=outer_deg <= outer_window[0] + 0.2 or outer_deg >= outer_window[1] - 0.2,
            inner_at_limit=inner_deg <= inner_window[0] + 0.2 or inner_deg >= inner_window[1] - 0.2,
        )

    def link_budget(self) -> LinkBudget:
        channels = 2 * len(self._cfg.live_arms())
        usb = "ttyACM" in (self.link.telemetry().port or self._cfg.link.device)
        return link_budget(channels, self._cfg.bench_limits.command_rate_hz, self._cfg.link.baud, usb)

    # ------------------------------------------------------------------
    # aiming
    # ------------------------------------------------------------------
    def aim(
        self,
        arm_ids: Optional[Sequence[str]],
        forward_deg: float,
        right_deg: float,
        speed_deg_s: float = 0.0,
    ) -> str:
        """Point the named arms' thrust at a body-frame lean."""
        self.link.require()
        arms = self.resolve_arms(arm_ids)
        solutions = [(arm, kin.solve_thrust_lean(arm, forward_deg, right_deg)) for arm in arms]
        duration_s, clamped = self._apply(solutions, speed_deg_s, OWNER_MANUAL)

        who = "all arms" if len(arms) == len(self._cfg.live_arms()) else ", ".join(a.id for a in arms)
        detail = f"thrust {forward_deg:+.1f} fwd / {right_deg:+.1f} right"
        if duration_s > 0.0:
            detail += f" over {duration_s:.1f}s"
        if clamped:
            detail += " (clamped to the mechanical envelope)"
        return self.link.note(f"{who}: {detail}")

    def live_aim(self, arm_ids: Optional[Sequence[str]], forward_deg: float, right_deg: float) -> None:
        """
        Highest-rate path, used while a pointer is being dragged.

        Deliberately unacked and unlogged: at 30 events a second an ack per update
        would swamp both the link and the activity feed.
        """
        self.link.require()
        arms = self.resolve_arms(arm_ids)
        generation = self.outputs.acquire(OWNER_LIVE)
        targets: Dict[int, int] = {}
        for arm in arms:
            answer = kin.solve_thrust_lean(arm, forward_deg, right_deg)
            for channel, pwm in answer.pwm():
                targets[channel] = pwm
        self.outputs.write(targets, generation)

    def end_live_aim(self) -> None:
        self.outputs.release(OWNER_LIVE)

    def set_axis(
        self,
        arm_id: str,
        axis: str,
        tilt_deg: float,
        speed_deg_s: float = 0.0,
    ) -> str:
        """Drive one gimbal axis directly, leaving the other where it is."""
        self.link.require()
        if axis not in ("outer", "inner"):
            raise ValueError("axis must be 'outer' or 'inner'")
        arm = self.resolve_arms([arm_id])[0]

        state = self.arm_state(arm)
        tilt_outer = tilt_deg if axis == "outer" else state.tilt_outer
        tilt_inner = tilt_deg if axis == "inner" else state.tilt_inner
        answer = kin.solve(arm.gimbal, tilt_outer, tilt_inner)
        duration_s, clamped = self._apply([(arm, answer)], speed_deg_s, OWNER_MANUAL)

        achieved = answer.outer.tilt_deg if axis == "outer" else answer.inner.tilt_deg
        detail = f"{arm.id} {axis} -> {achieved:+.2f} deg"
        if duration_s > 0.0:
            detail += f" over {duration_s:.1f}s"
        if clamped:
            detail += " (clamped)"
        return self.link.note(detail)

    def set_tilt(
        self,
        arm_id: str,
        tilt_outer: float,
        tilt_inner: float,
        speed_deg_s: float = 0.0,
    ) -> str:
        """Drive both gimbal axes of one arm to explicit angles."""
        self.link.require()
        arm = self.resolve_arms([arm_id])[0]
        answer = kin.solve(arm.gimbal, tilt_outer, tilt_inner)
        duration_s, clamped = self._apply([(arm, answer)], speed_deg_s, OWNER_MANUAL)
        detail = f"{arm.id} tilt -> outer {answer.outer.tilt_deg:+.2f}, inner {answer.inner.tilt_deg:+.2f} deg"
        if duration_s > 0.0:
            detail += f" over {duration_s:.1f}s"
        if clamped:
            detail += " (clamped)"
        return self.link.note(detail)

    def center(self, arm_ids: Optional[Sequence[str]] = None, speed_deg_s: float = 0.0) -> str:
        self.link.require()
        arms = self.resolve_arms(arm_ids)
        solutions = [(arm, kin.centered(arm.gimbal)) for arm in arms]
        self._apply(solutions, speed_deg_s, OWNER_MANUAL)
        who = "All arms" if len(arms) == len(self._cfg.live_arms()) else ", ".join(a.id for a in arms)
        return self.link.note(f"{who} centered")

    # ------------------------------------------------------------------
    # outputs and mapping
    # ------------------------------------------------------------------
    def assert_output_mapping(self) -> str:
        """
        Push the config's output mapping onto the flight controller.

        Three things have to agree or an output silently does nothing:

        * **Gimbal channels stay Disabled.** DO_SET_SERVO is only honoured on a
          disabled output, so that is correct here and wrong for flight.
        * **Gimbal pulse limits.** DO_SET_SERVO is clamped to SERVOn_MIN..MAX, so a
          board left at the 1000-2000 default throws away the ends of a 500-2500
          gimbal window without reporting anything. SERVOn_REVERSED is forced to 0
          because the axis signs live in the config, so there is one place to look.
        * **Motor channels need a motor function.** A Disabled output emits nothing,
          so a motor whose ``function`` is unset cannot spin no matter what else is
          configured. That is reported rather than guessed.
        * **Planned motors are placed too.** An octaquad mixer has eight slots.
          Unclaimed ones are auto-assigned to S1, S2, ... on the next boot, which
          puts DShot on the North gimbal timer. Planned East/West motors occupy
          those slots on the DShot groups so the servo pins stay PWM.
        """
        self.link.require()
        self.link.forget_servo_functions()

        reverse_mask = 0
        motors_mapped: List[int] = []
        motors_unmapped: List[str] = []

        # Motor functions for every arm, including planned. An octaquad mixer has
        # eight motor slots; any slot we do not place gets auto-assigned to the
        # default pin on the next boot. That is how Motor2 kept landing on S2 and
        # putting TIM8 (the North gimbals) into DShot. Planned motors live on the
        # DShot timer groups, so empty pins there are harmless; a motor function on
        # a servo pin is not.
        for arm in self._cfg.arms:
            for name, motor in arm.motors.items():
                if motor.function is None:
                    motors_unmapped.append(f"{arm.id}.{name} (S{motor.channel})")
                    continue
                self.link.set_servo_function(motor.channel, float(motor.function))
                motors_mapped.append(motor.channel)
                if motor.reversed:
                    # SERVO_BLH_RVMASK bit N is SERVO(N+1).
                    reverse_mask |= 1 << (motor.channel - 1)

        for arm in self._cfg.live_arms():
            for name in ("outer", "inner"):
                axis = arm.gimbal.axis(name)
                self.link.set_servo_function(axis.channel, FUNC_DISABLED)
                self.link.set_param(f"SERVO{axis.channel}_MIN", float(axis.min_us))
                self.link.set_param(f"SERVO{axis.channel}_MAX", float(axis.max_us))
                self.link.set_param(f"SERVO{axis.channel}_TRIM", float(axis.center_us))
                self.link.set_param(f"SERVO{axis.channel}_REVERSED", 0.0)

        self.link.set_param("SERVO_DSHOT_ESC", DSHOT_ESC_BLHELI)
        self.link.set_param("SERVO_BLH_RVMASK", float(reverse_mask))

        text = (
            f"Output mapping asserted: {len(motors_mapped)} motor channels, "
            f"reverse mask 0x{reverse_mask:X}"
        )
        if motors_unmapped:
            text += (
                f". No motor function set for {', '.join(motors_unmapped)} -- "
                "those outputs stay Disabled and will not spin"
            )
        note = self.link.note(text)

        mismatch = self.check_frame()
        if mismatch:
            self.link.record_error(mismatch)
        return note

    def check_frame(self) -> Optional[str]:
        """
        Compare the board's frame against the one the config's motor numbers assume.

        Returns a description of the mismatch, or None. This is a read, not a write:
        FRAME_CLASS only takes effect after a reboot, and changing it rearranges which
        physical motor answers to which mixer slot, so it is not something to assert
        behind the operator's back.

        Worth checking on every connect because the failure is silent. A motor whose
        number does not exist on the board's frame is simply never driven -- no refusal,
        no message, just an output that stays at zero.
        """
        frame = (self._cfg.raw.get("vehicle") or {}).get("frame") or {}
        wrong = []
        for key, param in (("frame_class", "FRAME_CLASS"), ("frame_type", "FRAME_TYPE")):
            want = frame.get(key)
            if want is None:
                continue
            got = self.link.get_param(param)
            if got is not None and int(got) != int(want):
                wrong.append(f"{param} is {int(got)}, config expects {int(want)}")
        if not wrong:
            return None
        return (
            "; ".join(wrong)
            + ". The motor numbers in the config belong to another frame, so motors that "
            "do not exist on this one will never spin. Set these by hand and reboot."
        )

    def spin_motors(
        self,
        arm_ids: Optional[Sequence[str]],
        which: Sequence[str],
        percent: float,
        seconds: float,
    ) -> str:
        """
        Spin selected motors with DO_MOTOR_TEST.

        ArduPilot addresses motors by frame test order, so each motor's sequence
        number comes from the config. A motor without one is refused rather than
        guessed at, because guessing here spins the wrong propeller.
        """
        self.link.require()
        arms = self.resolve_arms(arm_ids)
        roles = [role for role in which if role in ("top", "bottom")]
        if not roles:
            raise ValueError("select at least one of 'top' or 'bottom'")

        limits = self._cfg.bench_limits
        percent = max(0.0, min(limits.motor_percent, float(percent)))
        seconds = max(0.1, min(limits.motor_seconds, float(seconds)))

        targets: List[Tuple[str, str, int]] = []
        missing: List[str] = []
        for arm in arms:
            for role in roles:
                motor = arm.motors.get(role)
                if motor is None:
                    continue
                if motor.test_sequence is None:
                    missing.append(f"{arm.id}.{role}")
                else:
                    targets.append((arm.id, role, motor.test_sequence))

        if missing:
            raise ValueError(
                "no test_sequence configured for " + ", ".join(missing) +
                " - set it on the Setup page before spinning these motors"
            )
        if not targets:
            raise ValueError("no matching motors on the selected arms")

        for _, _, sequence in targets:
            self.link.motor_test(sequence, percent, seconds)

        with self._lock:
            self._motor_test_until = time.time() + seconds
        listing = ", ".join(f"{arm_id}.{role}" for arm_id, role, _ in targets)
        return self.link.note(f"Motor test {listing} at {percent:.1f}% for {seconds:.1f}s")

    def stop_motors(self) -> str:
        """Cancel any running motor test on every configured sequence number."""
        self.link.require()
        sequences = {
            motor.test_sequence
            for arm in self._cfg.arms
            for motor in arm.motors.values()
            if motor.test_sequence is not None
        }
        for sequence in sorted(sequences):
            self.link.motor_test(sequence, 0.0, 0.0)
        with self._lock:
            self._motor_test_until = 0.0
        return self.link.note("Motor test stopped")

    @property
    def motor_test_active(self) -> bool:
        with self._lock:
            return time.time() < self._motor_test_until

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        self.outputs.stop()
        self.link.disconnect()

    def describe_arms(self) -> List[Dict[str, Any]]:
        """Derived geometry per arm, for the Setup page."""
        return [
            {"id": arm.id, "workspace": kin.workspace_payload(arm)}
            for arm in self._cfg.arms
        ]
