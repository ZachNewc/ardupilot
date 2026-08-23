#!/usr/bin/env python3
"""
AP_FLAKE8_CLEAN
Vehicle configuration for the Vector dashboard.

The whole vehicle is described by one JSON document (``Vector/config/vector.json``).
Adding an arm means appending one entry to ``arms``; nothing in the server or the
web UI enumerates arms any other way.

Per-arm entries stay short because every gimbal field falls back to
``gimbal_defaults``. An arm only spells out what makes it different: where it
points, how its gimbal is bolted on, and which output channels it owns.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
DEFAULT_CONFIG_PATH = os.path.join(REPO_ROOT, "Vector", "config", "vector.json")

# An arm is either wired up and safe to command, or described but not built yet.
ARM_STATUS = ("live", "planned", "disabled")

AXIS_NAMES = ("outer", "inner")
MOTOR_NAMES = ("bottom", "top")


class ConfigError(ValueError):
    """Raised when a config document cannot be turned into a usable vehicle."""


@dataclass(frozen=True)
class AxisConfig:
    """One gimbal axis: the servo that drives it and the geometry of that drive."""

    channel: int
    sign: float
    center_us: int
    us_per_deg: float
    servo_limit_deg: float
    min_us: int
    max_us: int
    trim_deg: float

    def deg_to_us(self, deg: float) -> int:
        return int(round(self.center_us + deg * self.us_per_deg))

    def us_to_deg(self, us: float) -> float:
        return (us - self.center_us) / self.us_per_deg

    def clamp_us(self, us: int) -> int:
        """Clamp to whichever is tighter: the configured pulse window or the servo travel."""
        travel_lo = self.deg_to_us(-self.servo_limit_deg)
        travel_hi = self.deg_to_us(self.servo_limit_deg)
        lo = max(self.min_us, min(travel_lo, travel_hi))
        hi = min(self.max_us, max(travel_lo, travel_hi))
        return int(max(lo, min(hi, int(us))))


@dataclass(frozen=True)
class GimbalConfig:
    """
    Two-axis gimbal geometry.

    ``gear_ratio`` is servo degrees per gimbal degree. ``coupling`` is how much
    of the outer servo's travel leaks into the inner axis and therefore has to be
    added back to the inner command. See ``docs/03-kinematics.md``.
    """

    gear_ratio: float
    tilt_limit_deg: float
    coupling: float
    outer: AxisConfig
    inner: AxisConfig

    def axis(self, name: str) -> AxisConfig:
        return self.outer if name == "outer" else self.inner


@dataclass(frozen=True)
class MotorConfig:
    """
    One motor on one arm.

    ``test_sequence`` is the number MAV_CMD_DO_MOTOR_TEST expects. ArduPilot
    addresses motors by their position in the frame's test order, not by output
    channel, so it has to be recorded per motor rather than derived. ``None`` means
    it has not been established yet and the dashboard will refuse to spin that motor.

    ``function`` is the ``SERVOn_FUNCTION`` value that makes the channel a motor
    output: 33-40 for Motor1-Motor8. Without it the output stays Disabled and emits
    nothing at all, so it cannot be derived or defaulted -- which motor number a given
    arm and position maps to depends on the frame class, and guessing spins the wrong
    motor. ``None`` means unestablished, and the dashboard reports it rather than
    picking a value.
    """

    channel: int
    spin: str
    reversed: bool
    test_sequence: Optional[int] = None
    function: Optional[int] = None


@dataclass(frozen=True)
class ArmConfig:
    """One arm: where it points, how its gimbal is oriented, and what it drives."""

    id: str
    label: str
    status: str
    azimuth_deg: float
    mount_yaw_deg: float
    gimbal: GimbalConfig
    motors: Dict[str, MotorConfig]

    @property
    def live(self) -> bool:
        return self.status == "live"

    def servo_channels(self) -> Tuple[int, int]:
        return self.gimbal.outer.channel, self.gimbal.inner.channel

    def motor_channels(self) -> Tuple[int, ...]:
        return tuple(motor.channel for motor in self.motors.values())


@dataclass(frozen=True)
class FrameConfig:
    layout: str
    nose_arm: str
    rotor_diagonal_m: float
    arm_length_m: float


@dataclass(frozen=True)
class LinkConfig:
    device: str
    baud: int
    esc_telemetry_serial: Optional[int]


@dataclass(frozen=True)
class BenchLimits:
    motor_percent: float
    motor_seconds: float
    servo_speed_deg_s: float
    default_servo_speed_deg_s: float
    command_rate_hz: float


@dataclass(frozen=True)
class ControllerConfig:
    """
    Settings for the host-side bench levelling demo. Not the flight controller.

    ``level_gain`` blends between leaving the gimbals with the airframe (0) and
    holding thrust at true world vertical (1). ``lead_time_s`` extrapolates the
    attitude forward to offset servo lag, so it is a measurable time rather than an
    abstract gain.
    """

    mode: str
    level_gain: float
    lead_time_s: float
    max_tilt_fraction: float
    invert_roll: bool
    invert_pitch: bool


@dataclass(frozen=True)
class VehicleConfig:
    name: str
    summary: str
    controller: str
    frame: FrameConfig
    arms: List[ArmConfig]
    link: LinkConfig
    bench_limits: BenchLimits
    bench_controller: ControllerConfig
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)
    path: str = ""

    def arm(self, arm_id: str) -> ArmConfig:
        for entry in self.arms:
            if entry.id == arm_id:
                return entry
        raise KeyError(f"no arm '{arm_id}'")

    def live_arms(self) -> List[ArmConfig]:
        return [entry for entry in self.arms if entry.live]

    def servo_channels(self) -> List[int]:
        out: List[int] = []
        for entry in self.arms:
            if entry.live:
                out.extend(entry.servo_channels())
        return sorted(set(out))

    def motor_channels(self) -> List[int]:
        out: List[int] = []
        for entry in self.arms:
            if entry.live:
                out.extend(entry.motor_channels())
        return sorted(set(out))

    def arm_for_servo_channel(self, channel: int) -> Optional[Tuple[ArmConfig, str]]:
        for entry in self.arms:
            for name in AXIS_NAMES:
                if entry.gimbal.axis(name).channel == channel:
                    return entry, name
        return None

    def arm_for_motor_channel(self, channel: int) -> Optional[Tuple[ArmConfig, str]]:
        for entry in self.arms:
            for name, motor in entry.motors.items():
                if motor.channel == channel:
                    return entry, name
        return None


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay wins, except that nested dicts merge key by key."""
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _require(mapping: Dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"{where}: missing required key '{key}'")
    return mapping[key]


def _axis_from_dict(data: Dict[str, Any], where: str) -> AxisConfig:
    sign = float(data.get("sign", 1.0))
    if sign == 0.0:
        raise ConfigError(f"{where}: sign must be +1 or -1, not 0")
    axis = AxisConfig(
        channel=int(_require(data, "channel", where)),
        sign=sign,
        center_us=int(data.get("center_us", 1500)),
        us_per_deg=float(data.get("us_per_deg", 11.11111)),
        servo_limit_deg=float(data.get("servo_limit_deg", 45.0)),
        min_us=int(data.get("min_us", 1000)),
        max_us=int(data.get("max_us", 2000)),
        trim_deg=float(data.get("trim_deg", 0.0)),
    )
    if axis.channel < 1 or axis.channel > 32:
        raise ConfigError(f"{where}: channel {axis.channel} outside SERVO1..SERVO32")
    if axis.us_per_deg <= 0.0:
        raise ConfigError(f"{where}: us_per_deg must be positive")
    if axis.min_us >= axis.max_us:
        raise ConfigError(f"{where}: min_us must be below max_us")
    if axis.servo_limit_deg <= 0.0:
        raise ConfigError(f"{where}: servo_limit_deg must be positive")
    return axis


def _arm_from_dict(data: Dict[str, Any], defaults: Dict[str, Any], index: int) -> ArmConfig:
    arm_id = str(data.get("id") or f"arm{index + 1}")
    where = f"arms[{index}] ({arm_id})"

    status = str(data.get("status", "live"))
    if status not in ARM_STATUS:
        raise ConfigError(f"{where}: status must be one of {ARM_STATUS}")

    # Axis overrides may sit either at the arm root (the short form) or under
    # "gimbal". Both are folded onto gimbal_defaults.
    overlay: Dict[str, Any] = copy.deepcopy(data.get("gimbal", {}) or {})
    for name in AXIS_NAMES:
        if name in data:
            overlay[name] = _deep_merge(overlay.get(name, {}), data[name])
    merged = _deep_merge(defaults, overlay)

    gear_ratio = float(merged.get("gear_ratio", 1.0))
    if gear_ratio <= 0.0:
        raise ConfigError(f"{where}: gear_ratio must be positive")
    tilt_limit = float(merged.get("tilt_limit_deg", 22.5))
    if tilt_limit <= 0.0:
        raise ConfigError(f"{where}: tilt_limit_deg must be positive")

    gimbal = GimbalConfig(
        gear_ratio=gear_ratio,
        tilt_limit_deg=tilt_limit,
        coupling=float(merged.get("coupling", 0.0)),
        outer=_axis_from_dict(merged.get("outer", {}), f"{where}.outer"),
        inner=_axis_from_dict(merged.get("inner", {}), f"{where}.inner"),
    )

    motors: Dict[str, MotorConfig] = {}
    for name in MOTOR_NAMES:
        entry = (data.get("motors") or {}).get(name)
        if entry is None:
            continue
        channel = int(_require(entry, "channel", f"{where}.motors.{name}"))
        if channel < 1 or channel > 32:
            raise ConfigError(f"{where}.motors.{name}: channel {channel} outside SERVO1..SERVO32")
        sequence = entry.get("test_sequence")
        function = entry.get("function")
        if function is not None and not 33 <= int(function) <= 40:
            raise ConfigError(
                f"{where}.motors.{name}: function {function} is not a motor output; "
                "Motor1..Motor8 are 33..40"
            )
        motors[name] = MotorConfig(
            channel=channel,
            spin=str(entry.get("spin", "ccw")),
            reversed=bool(entry.get("reversed", False)),
            test_sequence=None if sequence is None else int(sequence),
            function=None if function is None else int(function),
        )

    return ArmConfig(
        id=arm_id,
        label=str(data.get("label") or arm_id.title()),
        status=status,
        azimuth_deg=float(data.get("azimuth_deg", 0.0)) % 360.0,
        mount_yaw_deg=float(data.get("mount_yaw_deg", 0.0)) % 360.0,
        gimbal=gimbal,
        motors=motors,
    )


def _check_channel_conflicts(arms: List[ArmConfig]) -> None:
    """Two outputs on one pad would silently fight each other, so refuse to load."""
    owners: Dict[int, str] = {}
    for arm in arms:
        claims = [(arm.gimbal.axis(name).channel, f"{arm.id}.{name}") for name in AXIS_NAMES]
        claims += [(motor.channel, f"{arm.id}.motor.{name}") for name, motor in arm.motors.items()]
        for channel, who in claims:
            if channel in owners:
                raise ConfigError(f"SERVO{channel} claimed by both {owners[channel]} and {who}")
            owners[channel] = who


def from_dict(data: Dict[str, Any], path: str = "") -> VehicleConfig:
    """Validate a config document and build the immutable model the server uses."""
    vehicle = data.get("vehicle") or {}
    frame_raw = vehicle.get("frame") or {}
    defaults = data.get("gimbal_defaults") or {}

    arms_raw = data.get("arms")
    if not isinstance(arms_raw, list) or not arms_raw:
        raise ConfigError("config must define a non-empty 'arms' list")

    arms = [_arm_from_dict(entry, defaults, i) for i, entry in enumerate(arms_raw)]
    if len({arm.id for arm in arms}) != len(arms):
        raise ConfigError("arm ids must be unique")
    _check_channel_conflicts(arms)

    link_raw = data.get("link") or {}
    limits_raw = data.get("bench_limits") or {}
    ctrl_raw = data.get("bench_controller") or {}

    return VehicleConfig(
        name=str(vehicle.get("name", "Vector")),
        summary=str(vehicle.get("summary", "")),
        controller=str(vehicle.get("controller", "")),
        frame=FrameConfig(
            layout=str(frame_raw.get("layout", "plus")),
            nose_arm=str(frame_raw.get("nose_arm", arms[0].id)),
            rotor_diagonal_m=float(frame_raw.get("rotor_diagonal_m", 0.0)),
            arm_length_m=float(frame_raw.get("arm_length_m", 0.0)),
        ),
        arms=arms,
        link=LinkConfig(
            device=str(link_raw.get("device", "/dev/ttyACM0")),
            baud=int(link_raw.get("baud", 115200)),
            esc_telemetry_serial=link_raw.get("esc_telemetry_serial"),
        ),
        bench_limits=BenchLimits(
            motor_percent=float(limits_raw.get("motor_percent", 50.0)),
            motor_seconds=float(limits_raw.get("motor_seconds", 10.0)),
            servo_speed_deg_s=float(limits_raw.get("servo_speed_deg_s", 180.0)),
            default_servo_speed_deg_s=float(limits_raw.get("default_servo_speed_deg_s", 45.0)),
            command_rate_hz=float(limits_raw.get("command_rate_hz", 25.0)),
        ),
        bench_controller=ControllerConfig(
            mode=str(ctrl_raw.get("mode", "off")),
            level_gain=float(ctrl_raw.get("level_gain", 1.0)),
            lead_time_s=float(ctrl_raw.get("lead_time_s", 0.06)),
            max_tilt_fraction=float(ctrl_raw.get("max_tilt_fraction", 1.0)),
            invert_roll=bool(ctrl_raw.get("invert_roll", False)),
            invert_pitch=bool(ctrl_raw.get("invert_pitch", False)),
        ),
        raw=copy.deepcopy(data),
        path=path,
    )


def load(path: str = DEFAULT_CONFIG_PATH) -> VehicleConfig:
    with open(path, "r", encoding="utf-8") as handle:
        return from_dict(json.load(handle), path=path)


def save(cfg: VehicleConfig, updates: Dict[str, Any], path: str = "") -> VehicleConfig:
    """
    Merge ``updates`` into the document and write it back atomically.

    The merged document is validated before anything touches the disk, so a bad
    edit from the Setup page leaves the previous config in place.
    """
    target = path or cfg.path or DEFAULT_CONFIG_PATH
    merged = _deep_merge(cfg.raw, updates)
    if "arms" in updates:
        # Arms are positional; a merge would splice entries together instead of
        # replacing the list the operator actually submitted.
        merged["arms"] = copy.deepcopy(updates["arms"])
    validated = from_dict(merged, path=target)

    directory = os.path.dirname(os.path.abspath(target))
    os.makedirs(directory, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, prefix=".vector-", suffix=".json", delete=False
    )
    try:
        with handle:
            json.dump(merged, handle, indent=2)
            handle.write("\n")
        os.replace(handle.name, target)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    return validated


def axis_to_dict(axis: AxisConfig) -> Dict[str, Any]:
    return {
        "channel": axis.channel,
        "sign": axis.sign,
        "centerUs": axis.center_us,
        "usPerDeg": round(axis.us_per_deg, 6),
        "servoLimitDeg": axis.servo_limit_deg,
        "minUs": axis.min_us,
        "maxUs": axis.max_us,
        "trimDeg": axis.trim_deg,
    }


def arm_to_dict(arm: ArmConfig) -> Dict[str, Any]:
    """Shape an arm for the web UI. Keys are camelCase to match the TypeScript side."""
    return {
        "id": arm.id,
        "label": arm.label,
        "status": arm.status,
        "live": arm.live,
        "azimuthDeg": arm.azimuth_deg,
        "mountYawDeg": arm.mount_yaw_deg,
        "gimbal": {
            "gearRatio": arm.gimbal.gear_ratio,
            "tiltLimitDeg": arm.gimbal.tilt_limit_deg,
            "coupling": arm.gimbal.coupling,
            "outer": axis_to_dict(arm.gimbal.outer),
            "inner": axis_to_dict(arm.gimbal.inner),
        },
        "motors": {
            name: {
                "channel": motor.channel,
                "spin": motor.spin,
                "reversed": motor.reversed,
                "testSequence": motor.test_sequence,
                "function": motor.function,
            }
            for name, motor in arm.motors.items()
        },
    }


def to_dict(cfg: VehicleConfig) -> Dict[str, Any]:
    """Full vehicle description sent to a browser once on connect."""
    return {
        "name": cfg.name,
        "summary": cfg.summary,
        "controller": cfg.controller,
        "frame": {
            "layout": cfg.frame.layout,
            "noseArm": cfg.frame.nose_arm,
            "rotorDiagonalM": cfg.frame.rotor_diagonal_m,
            "armLengthM": cfg.frame.arm_length_m,
        },
        "arms": [arm_to_dict(arm) for arm in cfg.arms],
        "link": {
            "device": cfg.link.device,
            "baud": cfg.link.baud,
            "escTelemetrySerial": cfg.link.esc_telemetry_serial,
        },
        "benchLimits": {
            "motorPercent": cfg.bench_limits.motor_percent,
            "motorSeconds": cfg.bench_limits.motor_seconds,
            "servoSpeedDegS": cfg.bench_limits.servo_speed_deg_s,
            "defaultServoSpeedDegS": cfg.bench_limits.default_servo_speed_deg_s,
            "commandRateHz": cfg.bench_limits.command_rate_hz,
        },
        "benchController": {
            "mode": cfg.bench_controller.mode,
            "levelGain": cfg.bench_controller.level_gain,
            "leadTimeS": cfg.bench_controller.lead_time_s,
            "maxTiltFraction": cfg.bench_controller.max_tilt_fraction,
            "invertRoll": cfg.bench_controller.invert_roll,
            "invertPitch": cfg.bench_controller.invert_pitch,
        },
        "path": cfg.path,
    }


def with_controller(cfg: VehicleConfig, **changes: Any) -> VehicleConfig:
    """Return a copy with bench controller gains changed, without touching disk."""
    return replace(cfg, bench_controller=replace(cfg.bench_controller, **changes))
