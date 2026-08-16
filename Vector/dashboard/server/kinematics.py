#!/usr/bin/env python3
"""
AP_FLAKE8_CLEAN
Gimbal kinematics for a Vector arm.

Everything here is pure geometry plus per-arm hardware constants; nothing talks to
a link or holds state. ``docs/03-kinematics.md`` carries the full derivation, but
the three relationships that matter are:

Geometric tilt -> servo angle (this is what we command)::

    servo_outer = sign_o * gear * tilt_outer + trim_o
    servo_inner = sign_i * gear * tilt_inner + coupling * (sign_o * gear * tilt_outer) + trim_i

The ``coupling`` term is the whole reason the inner servo needs twice the travel of
the outer one: the inner axis is driven from the airframe, not from the outer ring,
so rotating the outer ring drags the inner axis with it and the inner command has to
put that back.

Geometric tilt -> thrust direction (exact, not small-angle)::

    n0 = (-sin b,  cos b * sin a,  -cos b * cos a)
    n  = Rz(mount_yaw) * n0

where ``a`` is tilt_outer, ``b`` is tilt_inner, and ``n`` is a unit vector in
ArduPilot body frame (X forward, Y right, Z down). A centred gimbal gives
``n = (0, 0, -1)``: thrust straight up.

Angles are degrees everywhere in this module's public surface.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .config import ArmConfig, AxisConfig, GimbalConfig

# Ray count used when tracing the reachable workspace outline. 180 gives a
# 2-degree azimuth step, which is finer than any UI can show.
WORKSPACE_RAYS = 180


@dataclass(frozen=True)
class AxisSolution:
    """One axis of a resolved gimbal command."""

    name: str
    channel: int
    tilt_deg: float
    servo_deg: float
    pwm_us: int
    at_limit: bool


@dataclass(frozen=True)
class GimbalSolution:
    """
    A gimbal command that has been made reachable.

    ``scale`` is how much the request had to be pulled back to fit inside the
    mechanical envelope. It is 1.0 when the request was already reachable. Scaling
    both axes together preserves the *direction* of the thrust vector, which
    matters far more than its magnitude: clipping one axis alone would swing the
    thrust somewhere the caller never asked for.
    """

    outer: AxisSolution
    inner: AxisSolution
    requested: Tuple[float, float]
    scale: float

    @property
    def clamped(self) -> bool:
        return self.scale < 0.999

    @property
    def tilt(self) -> Tuple[float, float]:
        return self.outer.tilt_deg, self.inner.tilt_deg

    def pwm(self) -> Tuple[Tuple[int, int], Tuple[int, int]]:
        """((outer_channel, outer_us), (inner_channel, inner_us))."""
        return (self.outer.channel, self.outer.pwm_us), (self.inner.channel, self.inner.pwm_us)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ----------------------------------------------------------------------------
# servo window
# ----------------------------------------------------------------------------
def servo_deg_window(axis: AxisConfig) -> Tuple[float, float]:
    """
    Usable servo travel in degrees, as the tighter of the pulse window and the
    declared mechanical travel.
    """
    low = max(-axis.servo_limit_deg, axis.us_to_deg(axis.min_us))
    high = min(axis.servo_limit_deg, axis.us_to_deg(axis.max_us))
    return low, high


# ----------------------------------------------------------------------------
# tilt <-> servo
# ----------------------------------------------------------------------------
def outer_drive_deg(gimbal: GimbalConfig, tilt_outer: float) -> float:
    """Mechanical part of the outer servo command, before trim. Also the coupling term."""
    return gimbal.outer.sign * gimbal.gear_ratio * tilt_outer


def servo_deg_for_tilt(gimbal: GimbalConfig, tilt_outer: float, tilt_inner: float) -> Tuple[float, float]:
    """Inverse kinematics: geometric tilt in, servo angles out."""
    drive_outer = outer_drive_deg(gimbal, tilt_outer)
    outer = drive_outer + gimbal.outer.trim_deg
    inner = gimbal.inner.sign * gimbal.gear_ratio * tilt_inner + gimbal.coupling * drive_outer + gimbal.inner.trim_deg
    return outer, inner


def tilt_for_servo_deg(gimbal: GimbalConfig, outer_deg: float, inner_deg: float) -> Tuple[float, float]:
    """Forward kinematics: servo angles in, geometric tilt out."""
    drive_outer = outer_deg - gimbal.outer.trim_deg
    tilt_outer = drive_outer / (gimbal.outer.sign * gimbal.gear_ratio)
    tilt_inner = (inner_deg - gimbal.inner.trim_deg - gimbal.coupling * drive_outer) / (
        gimbal.inner.sign * gimbal.gear_ratio
    )
    return tilt_outer, tilt_inner


def tilt_for_pwm(gimbal: GimbalConfig, outer_us: float, inner_us: float) -> Tuple[float, float]:
    return tilt_for_servo_deg(
        gimbal, gimbal.outer.us_to_deg(outer_us), gimbal.inner.us_to_deg(inner_us)
    )


# ----------------------------------------------------------------------------
# reachability
# ----------------------------------------------------------------------------
def _max_scale_for_bound(coeff: float, offset: float, low: float, high: float) -> float:
    """
    Largest ``s`` in [0, 1] with ``low <= coeff * s + offset <= high``.

    Returns 0.0 when the offset alone already violates the bound, which is how a
    bad trim shows up rather than as a silent clip.
    """
    if coeff == 0.0:
        return 1.0 if low <= offset <= high else 0.0
    at_low = (low - offset) / coeff
    at_high = (high - offset) / coeff
    lower, upper = min(at_low, at_high), max(at_low, at_high)
    if lower > 0.0 or upper < 0.0:
        # Zero scale is itself out of bounds, so the offset — a trim — is the
        # problem and pulling the request back cannot fix it.
        return 0.0
    return min(1.0, upper)


def max_scale(gimbal: GimbalConfig, tilt_outer: float, tilt_inner: float) -> float:
    """
    How much of the requested tilt is reachable, as a fraction in [0, 1].

    Every constraint is linear in the scale factor, so the answer is just the
    tightest of them.
    """
    limit = gimbal.tilt_limit_deg
    outer_low, outer_high = servo_deg_window(gimbal.outer)
    inner_low, inner_high = servo_deg_window(gimbal.inner)

    drive_outer = outer_drive_deg(gimbal, tilt_outer)
    inner_coeff = gimbal.inner.sign * gimbal.gear_ratio * tilt_inner + gimbal.coupling * drive_outer

    return min(
        _max_scale_for_bound(tilt_outer, 0.0, -limit, limit),
        _max_scale_for_bound(tilt_inner, 0.0, -limit, limit),
        _max_scale_for_bound(drive_outer, gimbal.outer.trim_deg, outer_low, outer_high),
        _max_scale_for_bound(inner_coeff, gimbal.inner.trim_deg, inner_low, inner_high),
    )


def solve(gimbal: GimbalConfig, tilt_outer: float, tilt_inner: float) -> GimbalSolution:
    """Resolve a tilt request into pulse widths, pulling it inside the envelope if needed."""
    scale = max_scale(gimbal, tilt_outer, tilt_inner)
    achieved_outer = tilt_outer * scale
    achieved_inner = tilt_inner * scale
    outer_deg, inner_deg = servo_deg_for_tilt(gimbal, achieved_outer, achieved_inner)

    def axis_solution(name: str, axis: AxisConfig, tilt: float, servo: float) -> AxisSolution:
        low, high = servo_deg_window(axis)
        margin = 0.05
        return AxisSolution(
            name=name,
            channel=axis.channel,
            tilt_deg=tilt,
            servo_deg=servo,
            pwm_us=axis.clamp_us(axis.deg_to_us(servo)),
            at_limit=servo <= low + margin or servo >= high - margin,
        )

    return GimbalSolution(
        outer=axis_solution("outer", gimbal.outer, achieved_outer, outer_deg),
        inner=axis_solution("inner", gimbal.inner, achieved_inner, inner_deg),
        requested=(tilt_outer, tilt_inner),
        scale=scale,
    )


def centered(gimbal: GimbalConfig) -> GimbalSolution:
    return solve(gimbal, 0.0, 0.0)


# ----------------------------------------------------------------------------
# body frame <-> gimbal axes
# ----------------------------------------------------------------------------
def thrust_unit_vector(mount_yaw_deg: float, tilt_outer: float, tilt_inner: float) -> Tuple[float, float, float]:
    """
    Exact thrust direction in ArduPilot body frame (X forward, Y right, Z down).

    Centred gimbal gives (0, 0, -1). The inner rotation is applied first because
    the inner ring is carried by the outer one.
    """
    a = math.radians(tilt_outer)
    b = math.radians(tilt_inner)
    x0 = -math.sin(b)
    y0 = math.cos(b) * math.sin(a)
    z0 = -math.cos(b) * math.cos(a)

    m = math.radians(mount_yaw_deg)
    cos_m, sin_m = math.cos(m), math.sin(m)
    return (x0 * cos_m - y0 * sin_m, x0 * sin_m + y0 * cos_m, z0)


def gimbal_to_body_tilt(mount_yaw_deg: float, tilt_outer: float, tilt_inner: float) -> Tuple[float, float]:
    """
    Linearised body tilt: how far the thrust leans forward (+X) and right (+Y).

    This is the map the mixer uses, because it is a plain rotation and therefore
    cheap and trivially invertible. It is exact only to first order: the two gimbal
    rotations do not commute, so at full deflection it disagrees with the exact
    geometry by a few hundredths of a degree. See :func:`thrust_lean` for the exact
    version and ``docs/03-kinematics.md`` for the measured error.
    """
    m = math.radians(mount_yaw_deg)
    cos_m, sin_m = math.cos(m), math.sin(m)
    return (
        -tilt_inner * cos_m - tilt_outer * sin_m,
        -tilt_inner * sin_m + tilt_outer * cos_m,
    )


def body_tilt_to_gimbal(mount_yaw_deg: float, forward_deg: float, right_deg: float) -> Tuple[float, float]:
    """Inverse of :func:`gimbal_to_body_tilt`. Returns (tilt_outer, tilt_inner)."""
    m = math.radians(mount_yaw_deg)
    cos_m, sin_m = math.cos(m), math.sin(m)
    tilt_outer = -forward_deg * sin_m + right_deg * cos_m
    tilt_inner = -(forward_deg * cos_m + right_deg * sin_m)
    return tilt_outer, tilt_inner


def thrust_lean(mount_yaw_deg: float, tilt_outer: float, tilt_inner: float) -> Tuple[float, float]:
    """
    Exact body-frame lean of the thrust vector, in degrees.

    ``forward`` and ``right`` are the angles the thrust axis makes out of vertical
    toward body +X and +Y. Unlike :func:`gimbal_to_body_tilt` this carries no
    small-angle assumption, so it is what the UI and the calibration pages use.
    """
    x, y, _ = thrust_unit_vector(mount_yaw_deg, tilt_outer, tilt_inner)
    return math.degrees(math.asin(clamp(x, -1.0, 1.0))), math.degrees(math.asin(clamp(y, -1.0, 1.0)))


def gimbal_for_thrust_lean(mount_yaw_deg: float, forward_deg: float, right_deg: float) -> Tuple[float, float]:
    """
    Exact inverse of :func:`thrust_lean`. Returns (tilt_outer, tilt_inner).

    Builds the thrust unit vector the caller asked for, rotates it back out of the
    mount yaw, then reads the two axis angles off it directly.
    """
    x = math.sin(math.radians(forward_deg))
    y = math.sin(math.radians(right_deg))
    horizontal = x * x + y * y
    if horizontal > 1.0:
        # Beyond horizontal is not a thrust direction; pull it back onto the sphere.
        norm = math.sqrt(horizontal)
        x, y, horizontal = x / norm, y / norm, 1.0
    z = -math.sqrt(max(0.0, 1.0 - horizontal))

    m = math.radians(mount_yaw_deg)
    cos_m, sin_m = math.cos(m), math.sin(m)
    # Undo the mount rotation about body Z.
    x0 = x * cos_m + y * sin_m
    y0 = -x * sin_m + y * cos_m

    tilt_inner = -math.asin(clamp(x0, -1.0, 1.0))
    tilt_outer = math.atan2(y0, -z)
    return math.degrees(tilt_outer), math.degrees(tilt_inner)


def gimbal_for_thrust_vector(
    mount_yaw_deg: float, vector: Tuple[float, float, float]
) -> Tuple[float, float]:
    """
    Axis angles that point the thrust along ``vector`` (body frame, need not be unit).

    Used by the levelling controller, which builds a desired thrust direction
    directly and never goes near a small-angle approximation.
    """
    x, y, z = vector
    norm = math.sqrt(x * x + y * y + z * z)
    if norm <= 0.0:
        return 0.0, 0.0
    x, y, z = x / norm, y / norm, z / norm

    m = math.radians(mount_yaw_deg)
    cos_m, sin_m = math.cos(m), math.sin(m)
    x0 = x * cos_m + y * sin_m
    y0 = -x * sin_m + y * cos_m

    tilt_inner = -math.asin(clamp(x0, -1.0, 1.0))
    tilt_outer = math.atan2(y0, -z)
    return math.degrees(tilt_outer), math.degrees(tilt_inner)


def lean_of_vector(vector: Tuple[float, float, float]) -> Tuple[float, float]:
    """Body-frame lean angles of a thrust direction, in degrees. Inverse-free."""
    x, y, z = vector
    norm = math.sqrt(x * x + y * y + z * z)
    if norm <= 0.0:
        return 0.0, 0.0
    return (
        math.degrees(math.asin(clamp(x / norm, -1.0, 1.0))),
        math.degrees(math.asin(clamp(y / norm, -1.0, 1.0))),
    )


def world_up_in_body(roll_deg: float, pitch_deg: float) -> Tuple[float, float, float]:
    """
    The world's up direction, expressed in body frame.

    This is the direction a thrust-vectoring arm has to point to hold its thrust
    vertical while the airframe moves underneath it, and it is the entire levelling
    law. Derived from the third row of the body-to-world rotation:

        up_body = (sin(pitch), -sin(roll) * cos(pitch), -cos(roll) * cos(pitch))

    A level airframe gives (0, 0, -1). Nose up leans the required thrust forward;
    right side down leans it left.
    """
    roll = math.radians(roll_deg)
    pitch = math.radians(pitch_deg)
    return (
        math.sin(pitch),
        -math.sin(roll) * math.cos(pitch),
        -math.cos(roll) * math.cos(pitch),
    )


def solve_thrust_lean(arm: ArmConfig, forward_deg: float, right_deg: float) -> GimbalSolution:
    """Aim an arm by where its thrust should point, using the exact geometry."""
    tilt_outer, tilt_inner = gimbal_for_thrust_lean(arm.mount_yaw_deg, forward_deg, right_deg)
    return solve(arm.gimbal, tilt_outer, tilt_inner)


def solve_thrust_vector(arm: ArmConfig, vector: Tuple[float, float, float]) -> GimbalSolution:
    """Aim an arm's thrust along a body-frame direction."""
    tilt_outer, tilt_inner = gimbal_for_thrust_vector(arm.mount_yaw_deg, vector)
    return solve(arm.gimbal, tilt_outer, tilt_inner)


def solve_body_tilt(arm: ArmConfig, forward_deg: float, right_deg: float) -> GimbalSolution:
    """Aim an arm with the linearised mixer map, for parity with the firmware."""
    tilt_outer, tilt_inner = body_tilt_to_gimbal(arm.mount_yaw_deg, forward_deg, right_deg)
    return solve(arm.gimbal, tilt_outer, tilt_inner)


# ----------------------------------------------------------------------------
# workspace
# ----------------------------------------------------------------------------
def workspace_outline(gimbal: GimbalConfig, rays: int = WORKSPACE_RAYS) -> List[Tuple[float, float]]:
    """
    Reachable (tilt_outer, tilt_inner) boundary, as a closed polygon.

    The constraint set is convex, so casting a ray per azimuth and asking how far
    it reaches traces the outline exactly. The UI draws this instead of assuming a
    circle, because the real envelope is a square clipped by the inner servo's travel.
    """
    reach = math.hypot(gimbal.tilt_limit_deg, gimbal.tilt_limit_deg) * 2.0
    points: List[Tuple[float, float]] = []
    for i in range(rays):
        theta = 2.0 * math.pi * i / rays
        direction = (reach * math.cos(theta), reach * math.sin(theta))
        scale = max_scale(gimbal, direction[0], direction[1])
        points.append((direction[0] * scale, direction[1] * scale))
    return points


def uniform_tilt_limit(gimbal: GimbalConfig, rays: int = WORKSPACE_RAYS) -> float:
    """
    Largest tilt magnitude available in *every* direction.

    This is the honest number to budget control authority against: anything larger
    is only reachable along some azimuths, so a controller that assumes it will
    saturate asymmetrically and pull the thrust vector off-axis.
    """
    outline = workspace_outline(gimbal, rays)
    if not outline:
        return 0.0
    return min(math.hypot(x, y) for x, y in outline)


# ----------------------------------------------------------------------------
# whole-vehicle helpers
# ----------------------------------------------------------------------------
def arm_position(arm: ArmConfig, arm_length_m: float) -> Tuple[float, float, float]:
    """Rotor hub position in body frame, metres. Azimuth is measured from body +X toward +Y."""
    psi = math.radians(arm.azimuth_deg)
    return (arm_length_m * math.cos(psi), arm_length_m * math.sin(psi), 0.0)


def yaw_tilt_pattern(arms: Sequence[ArmConfig]) -> List[Tuple[str, float, float]]:
    """
    Body tilt per arm that produces pure yaw with no net force or roll/pitch moment.

    Each arm's thrust is leaned tangentially, so the four side forces cancel while
    their moments about body Z add. Returns (arm_id, forward_deg, right_deg) for one
    degree of tilt, which the caller scales by the yaw demand.
    """
    pattern: List[Tuple[str, float, float]] = []
    for arm in arms:
        psi = math.radians(arm.azimuth_deg)
        # Tangential direction at this arm, counter-clockwise about body Z.
        pattern.append((arm.id, -math.sin(psi), math.cos(psi)))
    return pattern


def describe_gimbal(gimbal: GimbalConfig) -> dict:
    """Derived geometry the UI shows so an operator can sanity-check a config."""
    outer_low, outer_high = servo_deg_window(gimbal.outer)
    inner_low, inner_high = servo_deg_window(gimbal.inner)
    limit = gimbal.tilt_limit_deg

    # Worst-case inner servo travel: own motion plus the coupling term at full outer tilt.
    inner_needed = gimbal.gear_ratio * limit * (1.0 + abs(gimbal.coupling))
    outer_needed = gimbal.gear_ratio * limit

    return {
        "tiltLimitDeg": limit,
        "gearRatio": gimbal.gear_ratio,
        "coupling": gimbal.coupling,
        "uniformTiltDeg": round(uniform_tilt_limit(gimbal), 3),
        "outerServoNeededDeg": round(outer_needed, 2),
        "innerServoNeededDeg": round(inner_needed, 2),
        "outerServoWindowDeg": [round(outer_low, 2), round(outer_high, 2)],
        "innerServoWindowDeg": [round(inner_low, 2), round(inner_high, 2)],
        "outerHeadroomDeg": round(min(abs(outer_low), abs(outer_high)) - outer_needed, 2),
        "innerHeadroomDeg": round(min(abs(inner_low), abs(inner_high)) - inner_needed, 2),
    }


def workspace_payload(arm: ArmConfig, rays: int = 72) -> dict:
    """Outline plus derived limits, in both gimbal and body axes, for the UI."""
    outline = workspace_outline(arm.gimbal, rays)
    return {
        "gimbal": [[round(a, 3), round(b, 3)] for a, b in outline],
        "body": [
            [round(fwd, 3), round(right, 3)]
            for fwd, right in (gimbal_to_body_tilt(arm.mount_yaw_deg, a, b) for a, b in outline)
        ],
        **describe_gimbal(arm.gimbal),
    }


def golden_vectors(arm: ArmConfig, samples: Optional[Sequence[Tuple[float, float]]] = None) -> List[dict]:
    """
    Reference results used to keep the TypeScript and C++ ports honest.

    ``Vector/tests/test_kinematics.py`` writes these out and the web build asserts
    against the same file, so a sign flip cannot land in one language only.
    """
    if samples is None:
        limit = arm.gimbal.tilt_limit_deg
        steps = (-limit, -limit / 2.0, 0.0, limit / 3.0, limit)
        samples = [(a, b) for a in steps for b in steps]

    out: List[dict] = []
    for tilt_outer, tilt_inner in samples:
        answer = solve(arm.gimbal, tilt_outer, tilt_inner)
        forward, right = gimbal_to_body_tilt(arm.mount_yaw_deg, *answer.tilt)
        thrust = thrust_unit_vector(arm.mount_yaw_deg, *answer.tilt)
        out.append({
            "request": [tilt_outer, tilt_inner],
            "scale": round(answer.scale, 9),
            "tilt": [round(v, 9) for v in answer.tilt],
            "servoDeg": [round(answer.outer.servo_deg, 9), round(answer.inner.servo_deg, 9)],
            "pwmUs": [answer.outer.pwm_us, answer.inner.pwm_us],
            "bodyTilt": [round(forward, 9), round(right, 9)],
            "thrust": [round(v, 9) for v in thrust],
        })
    return out
