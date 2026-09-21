#!/usr/bin/env python3
"""
AP_FLAKE8_CLEAN
Host-side levelling controller for the Vector bench.

WHAT THIS IS FOR

Tilt the rig by hand and the gimbals counter-rotate so the motors keep pointing at
world vertical. That is the defining behaviour of a thrust-vectoring airframe and it
is worth being able to see and tune on a bench with the propellers off.

The same loop can instead read the IMU and lean motor thrust against measured linear
acceleration, so a shove is opposed by the translation channel rather than by
trying to square the airframe.

WHAT THIS IS NOT

This is not flight stabilisation, and it cannot become flight stabilisation, for two
separate reasons.

The first is mechanical. Tilting the thrust on a plus-layout airframe produces a
lateral force and a yaw moment, but essentially no roll or pitch moment, because a
horizontal force applied at a rotor sitting in the centre-of-mass plane has no lever
arm about the roll or pitch axes. Roll and pitch moments come from differential
thrust between opposite arms, which is ArduPilot's job, not this file's. Vectoring
buys the ability to translate while staying level; it does not buy attitude authority.

The second is latency. This loop runs in Python, over MAVLink, over a serial link,
at tens of hertz. Attitude control needs hundreds of hertz with a deterministic
budget. ``docs/04-control.md`` covers where the real controller belongs.

So: a demonstrator and a tuning aid for the levelling law, the accel-hold law, and
the servo signs. Nothing in this module should ever be relied on to keep an aircraft
in the air.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from . import kinematics as kin
from .bench import OWNER_STABILIZE, Bench

MODE_OFF = "off"
MODE_LEVEL = "level"
MODE_ACCEL = "accel"
MODES = (MODE_OFF, MODE_LEVEL, MODE_ACCEL)

# Rolling window used to report the loop rate actually achieved, so the gap between
# this and a real flight controller stays visible rather than implied.
RATE_WINDOW = 40

# First-order filter on linear accel so IMU noise does not chatter the servos.
ACCEL_FILTER_S = 0.08
DEFAULT_ACCEL_GAIN_DEG_G = 40.0
DEFAULT_ACCEL_DEADBAND_G = 0.05


@dataclass(frozen=True)
class ControllerStatus:
    active: bool
    mode: str
    level_gain: float
    lead_time_s: float
    max_tilt_fraction: float
    invert_roll: bool
    invert_pitch: bool
    accel_gain_deg_g: float
    accel_deadband_g: float
    invert_accel_x: bool
    invert_accel_y: bool
    tilt_cap_deg: float
    target_forward_deg: float
    target_right_deg: float
    target_magnitude_deg: float
    saturated: bool
    loop_hz: float
    updates: int
    last_error: str


class LevelController:
    """Holds thrust at world vertical while the airframe is moved by hand."""

    def __init__(self, bench: Bench) -> None:
        self._bench = bench
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._generation = 0

        settings = bench.config.bench_controller
        self._mode = MODE_OFF
        self._level_gain = settings.level_gain
        self._lead_time_s = settings.lead_time_s
        self._max_tilt_fraction = settings.max_tilt_fraction
        self._invert_roll = settings.invert_roll
        self._invert_pitch = settings.invert_pitch
        self._accel_gain_deg_g = DEFAULT_ACCEL_GAIN_DEG_G
        self._accel_deadband_g = DEFAULT_ACCEL_DEADBAND_G
        self._invert_accel_x = False
        self._invert_accel_y = False
        self._filt_lin = (0.0, 0.0)

        self._target = (0.0, 0.0)
        self._saturated = False
        self._updates = 0
        self._intervals: List[float] = []
        self._last_error = ""

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    @property
    def active(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive() and not self._stop.is_set())

    def tilt_cap_deg(self) -> float:
        """
        Largest lean the controller will command, in degrees.

        Uses the tightest uniform limit across live arms so one arm with a trimmed
        servo cannot be asked for a lean it can only reach in some directions.
        """
        arms = self._bench.config.live_arms() or self._bench.config.arms
        available = min(kin.uniform_tilt_limit(arm.gimbal) for arm in arms)
        return available * max(0.0, min(1.0, self._max_tilt_fraction))

    def status(self) -> ControllerStatus:
        with self._lock:
            intervals = list(self._intervals)
            target = self._target
            saturated = self._saturated
            updates = self._updates
            last_error = self._last_error
        mean = sum(intervals) / len(intervals) if intervals else 0.0
        return ControllerStatus(
            active=self.active,
            mode=self._mode,
            level_gain=self._level_gain,
            lead_time_s=self._lead_time_s,
            max_tilt_fraction=self._max_tilt_fraction,
            invert_roll=self._invert_roll,
            invert_pitch=self._invert_pitch,
            accel_gain_deg_g=self._accel_gain_deg_g,
            accel_deadband_g=self._accel_deadband_g,
            invert_accel_x=self._invert_accel_x,
            invert_accel_y=self._invert_accel_y,
            tilt_cap_deg=round(self.tilt_cap_deg(), 2),
            target_forward_deg=round(target[0], 2),
            target_right_deg=round(target[1], 2),
            target_magnitude_deg=round(math.hypot(*target), 2),
            saturated=saturated,
            loop_hz=round(1.0 / mean, 1) if mean > 0.0 else 0.0,
            updates=updates,
            last_error=last_error,
        )

    # ------------------------------------------------------------------
    # tuning
    # ------------------------------------------------------------------
    def configure(
        self,
        level_gain: Optional[float] = None,
        lead_time_s: Optional[float] = None,
        max_tilt_fraction: Optional[float] = None,
        invert_roll: Optional[bool] = None,
        invert_pitch: Optional[bool] = None,
        accel_gain_deg_g: Optional[float] = None,
        accel_deadband_g: Optional[float] = None,
        invert_accel_x: Optional[bool] = None,
        invert_accel_y: Optional[bool] = None,
    ) -> None:
        if level_gain is not None:
            self._level_gain = max(0.0, min(1.5, float(level_gain)))
        if lead_time_s is not None:
            self._lead_time_s = max(0.0, min(0.5, float(lead_time_s)))
        if max_tilt_fraction is not None:
            self._max_tilt_fraction = max(0.05, min(1.0, float(max_tilt_fraction)))
        if invert_roll is not None:
            self._invert_roll = bool(invert_roll)
        if invert_pitch is not None:
            self._invert_pitch = bool(invert_pitch)
        if accel_gain_deg_g is not None:
            self._accel_gain_deg_g = max(0.0, min(90.0, float(accel_gain_deg_g)))
        if accel_deadband_g is not None:
            self._accel_deadband_g = max(0.0, min(0.5, float(accel_deadband_g)))
        if invert_accel_x is not None:
            self._invert_accel_x = bool(invert_accel_x)
        if invert_accel_y is not None:
            self._invert_accel_y = bool(invert_accel_y)

    # ------------------------------------------------------------------
    # control law
    # ------------------------------------------------------------------
    def desired_lean(
        self, roll_deg: float, pitch_deg: float, roll_rate: float, pitch_rate: float
    ) -> Tuple[float, float, bool]:
        """
        The whole levelling law, as a pure function so the UI can mirror it exactly.

        Returns (forward_deg, right_deg, saturated).

        The lead term extrapolates the attitude forward by ``lead_time_s`` to offset
        servo lag; it is a time, not an abstract gain, so it can be set from a measured
        step response. ``level_gain`` blends between leaving the gimbals with the
        airframe (0) and holding thrust at true world vertical (1).
        """
        sign_roll = -1.0 if self._invert_roll else 1.0
        sign_pitch = -1.0 if self._invert_pitch else 1.0

        lead_roll = sign_roll * (roll_deg + self._lead_time_s * roll_rate)
        lead_pitch = sign_pitch * (pitch_deg + self._lead_time_s * pitch_rate)

        up = kin.world_up_in_body(lead_roll, lead_pitch)
        gain = self._level_gain
        # Blend from body up (no compensation) toward world up (full compensation).
        target = (up[0] * gain, up[1] * gain, -1.0 + (up[2] + 1.0) * gain)

        forward, right = kin.lean_of_vector(target)

        cap = self.tilt_cap_deg()
        magnitude = math.hypot(forward, right)
        if magnitude > cap and magnitude > 0.0:
            scale = cap / magnitude
            return forward * scale, right * scale, True
        return forward, right, False

    def desired_accel_lean(
        self,
        accel_x_g: float,
        accel_y_g: float,
        accel_z_g: float,
        roll_deg: float,
        pitch_deg: float,
    ) -> Tuple[float, float, bool]:
        """
        Point motor thrust against measured linear acceleration.

        Gravity is removed using attitude, so holding the frame at an angle does
        not count as a shove. Returns (forward_deg, right_deg, saturated).
        """
        lin_x, lin_y, _lin_z = kin.linear_accel_g(
            (accel_x_g, accel_y_g, accel_z_g), roll_deg, pitch_deg
        )
        return kin.oppose_horizontal_accel(
            lin_x,
            lin_y,
            self._accel_gain_deg_g,
            self._accel_deadband_g,
            self._invert_accel_x,
            self._invert_accel_y,
            self.tilt_cap_deg(),
        )

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self, mode: str = MODE_LEVEL) -> str:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if mode == MODE_OFF:
            return self.stop()

        self._bench.link.require()
        if not self._bench.config.live_arms():
            raise ValueError("no arms are marked live in the config")

        self._mode = mode
        if self.active:
            return self._bench.link.note(self._running_note("retuned"))

        leftover = self._thread
        if leftover is not None and leftover.is_alive():
            leftover.join(timeout=1.5)

        with self._lock:
            self._intervals.clear()
            self._updates = 0
            self._last_error = ""
            self._filt_lin = (0.0, 0.0)
        self._stop.clear()
        self._generation = self._bench.outputs.acquire(OWNER_STABILIZE)
        self._thread = threading.Thread(target=self._run, name="vector-level", daemon=True)
        self._thread.start()
        return self._bench.link.note(self._running_note("on"))

    def _running_note(self, verb: str) -> str:
        if self._mode == MODE_ACCEL:
            return (
                f"Accel hold {verb}: opposing linear accel at "
                f"{self._accel_gain_deg_g:.0f} deg/g, capped at {self.tilt_cap_deg():.1f} deg"
            )
        return (
            f"Levelling {verb}: holding thrust vertical at gain {self._level_gain:.2f}, "
            f"capped at {self.tilt_cap_deg():.1f} deg"
        )

    def stop(self) -> str:
        was_active = self.active
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        self._thread = None
        stopped = self._mode
        self._mode = MODE_OFF
        with self._lock:
            self._target = (0.0, 0.0)
            self._saturated = False
            self._filt_lin = (0.0, 0.0)
        self._bench.outputs.release(OWNER_STABILIZE)
        if not was_active:
            return "Levelling already off"
        label = "Accel hold off" if stopped == MODE_ACCEL else "Levelling off"
        return self._bench.link.note(label)

    def _accel_command(self, telemetry: Any, period: float) -> Tuple[float, float, bool]:
        """Filter linear accel, then oppose the horizontal part with gimbal lean."""
        if getattr(telemetry, "accel_at", 0.0) <= 0.0:
            with self._lock:
                self._filt_lin = (0.0, 0.0)
            return 0.0, 0.0, False

        lin_x, lin_y, _lin_z = kin.linear_accel_g(
            (telemetry.accel_x_g, telemetry.accel_y_g, telemetry.accel_z_g),
            telemetry.roll_deg,
            telemetry.pitch_deg,
        )
        alpha = period / (ACCEL_FILTER_S + period)
        filt_x, filt_y = self._filt_lin
        filt_x += alpha * (lin_x - filt_x)
        filt_y += alpha * (lin_y - filt_y)
        with self._lock:
            self._filt_lin = (filt_x, filt_y)
        return kin.oppose_horizontal_accel(
            filt_x,
            filt_y,
            self._accel_gain_deg_g,
            self._accel_deadband_g,
            self._invert_accel_x,
            self._invert_accel_y,
            self.tilt_cap_deg(),
        )

    def _run(self) -> None:
        period = 1.0 / max(1.0, self._bench.config.bench_limits.command_rate_hz)
        arbiter = self._bench.outputs
        previous = time.monotonic()

        while not self._stop.is_set():
            if not self._bench.link.connected or not arbiter.holds(self._generation):
                break

            telemetry = self._bench.link.telemetry()
            if self._mode == MODE_ACCEL:
                forward, right, saturated = self._accel_command(telemetry, period)
            else:
                forward, right, saturated = self.desired_lean(
                    telemetry.roll_deg,
                    telemetry.pitch_deg,
                    telemetry.roll_rate_dps,
                    telemetry.pitch_rate_dps,
                )

            targets = {}
            for arm in self._bench.config.live_arms():
                answer = kin.solve_thrust_lean(arm, forward, right)
                saturated = saturated or answer.clamped
                for channel, pwm in answer.pwm():
                    targets[channel] = pwm

            if not arbiter.write(targets, self._generation):
                break

            now = time.monotonic()
            with self._lock:
                self._target = (forward, right)
                self._saturated = saturated
                self._updates += 1
                self._intervals.append(now - previous)
                del self._intervals[:-RATE_WINDOW]
            previous = now

            self._stop.wait(period)

        with self._lock:
            self._target = (0.0, 0.0)
            self._saturated = False
