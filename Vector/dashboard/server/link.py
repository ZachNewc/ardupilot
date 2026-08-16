#!/usr/bin/env python3
"""
AP_FLAKE8_CLEAN
MAVLink link for the Vector dashboard.

One background reader thread owns every ``recv_match()`` call. Everything else
reads immutable snapshots and transmits under a lock, so no caller can end up
racing the reader for the serial port.

This module knows about MAVLink and nothing about gimbals. Arm-level operations
live in ``bench.py``.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional

from pymavlink import mavutil

# SRV_Channel function numbers we need by name.
FUNC_DISABLED = 0
FUNC_MOTOR1 = 33

# Telemetry we ask the flight controller to stream, and how often, in microseconds.
STREAM_INTERVALS_US = {
    "HEARTBEAT": 500000,
    "ATTITUDE": 25000,
    "VFR_HUD": 200000,
    "SYS_STATUS": 500000,
    "GPS_RAW_INT": 1000000,
    "SERVO_OUTPUT_RAW": 50000,
    "BATTERY_STATUS": 500000,
    "ESC_TELEMETRY_1_TO_4": 200000,
    "ESC_TELEMETRY_5_TO_8": 200000,
    "ESC_TELEMETRY_9_TO_12": 200000,
}

EVENT_HISTORY = 60
CONNECT_TIMEOUT_S = 10.0

EVENT_VEHICLE = "vehicle"
EVENT_COMMAND = "command"
EVENT_ERROR = "error"

# MAV_SEVERITY values at or below this are errors rather than information.
SEVERITY_ERROR = 3


@dataclass(frozen=True)
class Event:
    """
    One thing that happened, with a wall-clock time.

    Timestamps are epoch seconds rather than preformatted strings so the browser can
    interleave these with the commands it sent itself and render them in the viewer's
    own locale. Without a real timestamp a merged feed cannot be ordered, and the
    ordering is the whole value of merging the two streams: a refused command and the
    vehicle's explanation of why land next to each other.
    """

    seq: int
    at: float
    kind: str
    text: str


@dataclass
class Telemetry:
    """Everything the reader thread has learned from the link."""

    connected: bool = False
    port: str = ""
    system_id: int = 0
    component_id: int = 0
    last_heartbeat_age_s: float = 999.0

    armed: bool = False
    mode: str = "-"

    roll_deg: float = 0.0
    pitch_deg: float = 0.0
    yaw_deg: float = 0.0
    roll_rate_dps: float = 0.0
    pitch_rate_dps: float = 0.0
    yaw_rate_dps: float = 0.0

    alt_m: float = 0.0
    throttle: int = 0
    heading: int = 0

    voltage_v: float = 0.0
    current_a: float = 0.0
    battery_remaining: int = -1
    load_pct: float = 0.0
    gps_fix: int = 0
    gps_sats: int = 0

    servo_pwm: Dict[int, int] = field(default_factory=dict)
    esc_rpm: Dict[int, float] = field(default_factory=dict)
    esc_voltage: Dict[int, float] = field(default_factory=dict)
    esc_current: Dict[int, float] = field(default_factory=dict)
    esc_temp: Dict[int, float] = field(default_factory=dict)

    events: List[Event] = field(default_factory=list)
    msg_counts: Dict[str, int] = field(default_factory=dict)
    # Latest error, kept separately from the feed so the UI can pin it somewhere
    # visible instead of letting it scroll away.
    error: str = ""
    updated_at: float = 0.0

    @property
    def rate_magnitude_dps(self) -> float:
        return math.sqrt(self.roll_rate_dps ** 2 + self.pitch_rate_dps ** 2 + self.yaw_rate_dps ** 2)


class MavlinkLink:
    """Owns the serial connection, the reader thread, and the transmit lock."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tx_lock = threading.Lock()
        self._conn: Any = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._telemetry = Telemetry()
        self._heartbeat_at = 0.0
        self._function_cache: Dict[int, float] = {}
        self._event_seq = 0

    # ------------------------------------------------------------------
    # connection
    # ------------------------------------------------------------------
    def connect(self, device: str, baud: int = 115200) -> str:
        self.disconnect()
        try:
            conn = mavutil.mavlink_connection(device, baud=baud, source_system=255)

            # Some pymavlink builds raise TypeError inside post_message bookkeeping
            # for messages they cannot index; that must not kill the link.
            original_post = conn.post_message

            def safe_post(msg: Any) -> None:
                try:
                    original_post(msg)
                except TypeError:
                    pass

            conn.post_message = safe_post  # type: ignore[method-assign]

            heartbeat = self._await_heartbeat(conn)
            conn.target_system = heartbeat.get_srcSystem()
            conn.target_component = heartbeat.get_srcComponent()

            now = time.time()
            with self._lock:
                self._conn = conn
                self._function_cache.clear()
                self._telemetry = Telemetry(
                    connected=True,
                    port=device,
                    system_id=conn.target_system,
                    component_id=conn.target_component,
                    updated_at=now,
                )
                self._heartbeat_at = now

            self.request_streams()

            self._stop.clear()
            self._thread = threading.Thread(target=self._reader_loop, name="vector-mav-reader", daemon=True)
            self._thread.start()
            return f"Connected {device} (system {conn.target_system})"
        except Exception as exc:
            with self._lock:
                self._telemetry = Telemetry(connected=False, error=str(exc))
            raise

    @staticmethod
    def _await_heartbeat(conn: Any) -> Any:
        """Wait for a heartbeat from something other than ourselves."""
        deadline = time.time() + CONNECT_TIMEOUT_S
        while time.time() < deadline:
            msg = conn.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
            if msg is not None and msg.get_srcSystem() != 255:
                return msg
        conn.close()
        raise TimeoutError(f"No HEARTBEAT within {CONNECT_TIMEOUT_S:.0f}s")

    def disconnect(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

        conn = self._conn
        with self._lock:
            self._conn = None
            self._telemetry.connected = False
            self._telemetry.port = ""
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._telemetry.connected and self._conn is not None

    @property
    def reader_alive(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def require(self) -> Any:
        conn = self._conn
        if conn is None:
            raise RuntimeError("Not connected to a flight controller")
        return conn

    # ------------------------------------------------------------------
    # snapshots
    # ------------------------------------------------------------------
    def telemetry(self) -> Telemetry:
        """A private copy, safe to read without holding the lock."""
        with self._lock:
            snap = replace(
                self._telemetry,
                servo_pwm=dict(self._telemetry.servo_pwm),
                esc_rpm=dict(self._telemetry.esc_rpm),
                esc_voltage=dict(self._telemetry.esc_voltage),
                esc_current=dict(self._telemetry.esc_current),
                esc_temp=dict(self._telemetry.esc_temp),
                events=list(self._telemetry.events),
                msg_counts=dict(self._telemetry.msg_counts),
            )
            if self._heartbeat_at:
                snap.last_heartbeat_age_s = time.time() - self._heartbeat_at
            return snap

    def servo_pwm(self, channel: int, default: int = 1500) -> int:
        with self._lock:
            return int(self._telemetry.servo_pwm.get(channel) or default)

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    def _append_event(self, kind: str, text: str) -> None:
        """Caller must hold the lock."""
        self._event_seq += 1
        self._telemetry.events.append(
            Event(seq=self._event_seq, at=time.time(), kind=kind, text=text)
        )
        del self._telemetry.events[:-EVENT_HISTORY]

    def note(self, message: str) -> str:
        """Record something the dashboard did. Returns the message so callers can chain."""
        with self._lock:
            self._append_event(EVENT_COMMAND, message)
        return message

    def record_error(self, message: str) -> None:
        with self._lock:
            self._telemetry.error = message
            self._append_event(EVENT_ERROR, message)

    # ------------------------------------------------------------------
    # streams
    # ------------------------------------------------------------------
    def request_streams(self) -> str:
        conn = self._conn
        if conn is None:
            raise RuntimeError("Not connected to a flight controller")
        for name, interval_us in STREAM_INTERVALS_US.items():
            msg_id = getattr(mavutil.mavlink, f"MAVLINK_MSG_ID_{name}", None)
            if msg_id is None:
                continue
            self.command_long_quiet(
                mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, float(msg_id), float(interval_us)
            )
        # Older firmware ignores SET_MESSAGE_INTERVAL for some messages.
        with self._tx_lock:
            try:
                conn.mav.request_data_stream_send(
                    conn.target_system, conn.target_component, mavutil.mavlink.MAV_DATA_STREAM_ALL, 10, 1
                )
            except Exception:
                pass
        return self.note("Telemetry streams requested")

    # ------------------------------------------------------------------
    # reader
    # ------------------------------------------------------------------
    def _reader_loop(self) -> None:
        while not self._stop.is_set():
            conn = self._conn
            if conn is None:
                break
            try:
                msg = conn.recv_match(blocking=True, timeout=0.2)
            except Exception as exc:
                with self._lock:
                    self._telemetry.error = f"link lost: {exc}"
                    self._telemetry.connected = False
                break
            if msg is None:
                continue
            try:
                self._handle(msg)
            except Exception as exc:
                # One malformed message must never take telemetry down.
                with self._lock:
                    self._telemetry.error = f"{msg.get_type()}: {exc}"

    def _handle(self, msg: Any) -> None:
        kind = msg.get_type()
        if kind == "BAD_DATA":
            return

        with self._lock:
            snap = self._telemetry
            snap.updated_at = time.time()
            snap.msg_counts[kind] = snap.msg_counts.get(kind, 0) + 1
            handler = self._HANDLERS.get(kind)
            if handler is not None:
                handler(self, snap, msg)

    def _on_heartbeat(self, snap: Telemetry, msg: Any) -> None:
        if msg.get_srcSystem() == 255:
            return
        self._heartbeat_at = time.time()
        snap.armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        try:
            snap.mode = mavutil.mode_string_v10(msg)
        except Exception:
            snap.mode = str(getattr(msg, "custom_mode", "?"))

    def _on_attitude(self, snap: Telemetry, msg: Any) -> None:
        snap.roll_deg = math.degrees(msg.roll)
        snap.pitch_deg = math.degrees(msg.pitch)
        snap.yaw_deg = math.degrees(msg.yaw)
        snap.roll_rate_dps = math.degrees(msg.rollspeed)
        snap.pitch_rate_dps = math.degrees(msg.pitchspeed)
        snap.yaw_rate_dps = math.degrees(msg.yawspeed)

    def _on_vfr_hud(self, snap: Telemetry, msg: Any) -> None:
        snap.alt_m = msg.alt
        snap.throttle = msg.throttle
        snap.heading = msg.heading

    def _on_sys_status(self, snap: Telemetry, msg: Any) -> None:
        if msg.voltage_battery not in (0, 65535):
            snap.voltage_v = msg.voltage_battery / 1000.0
        if msg.current_battery >= 0:
            snap.current_a = msg.current_battery / 100.0
        snap.battery_remaining = msg.battery_remaining
        snap.load_pct = msg.load / 10.0

    def _on_battery_status(self, snap: Telemetry, msg: Any) -> None:
        voltages = getattr(msg, "voltages", None)
        if voltages and voltages[0] not in (0, 65535):
            snap.voltage_v = voltages[0] / 1000.0
        if msg.current_battery >= 0:
            snap.current_a = msg.current_battery / 100.0
        snap.battery_remaining = msg.battery_remaining

    def _on_gps(self, snap: Telemetry, msg: Any) -> None:
        snap.gps_fix = msg.fix_type
        snap.gps_sats = msg.satellites_visible

    def _on_servo_output(self, snap: Telemetry, msg: Any) -> None:
        for i in range(1, 17):
            value = getattr(msg, f"servo{i}_raw", None)
            if value:
                snap.servo_pwm[i] = int(value)

    def _on_esc_telemetry(self, snap: Telemetry, msg: Any) -> None:
        kind = msg.get_type()
        base = {"ESC_TELEMETRY_1_TO_4": 1, "ESC_TELEMETRY_5_TO_8": 5, "ESC_TELEMETRY_9_TO_12": 9}[kind]
        voltage = getattr(msg, "voltage", None)
        current = getattr(msg, "current", None)
        rpm = getattr(msg, "rpm", None)
        temperature = getattr(msg, "temperature", None)
        for i in range(4):
            volts = float(voltage[i]) / 100.0 if voltage is not None else 0.0
            # An unpopulated slot reports all zeros; skip it so the UI only lists
            # ESCs that are really reporting.
            if volts <= 0.0:
                continue
            index = base + i
            snap.esc_voltage[index] = volts
            if rpm is not None:
                snap.esc_rpm[index] = float(rpm[i])
            if current is not None:
                snap.esc_current[index] = float(current[i]) / 100.0
            if temperature is not None:
                snap.esc_temp[index] = float(temperature[i])

    def _on_status_text(self, snap: Telemetry, msg: Any) -> None:
        text = msg.text
        if not isinstance(text, str):
            text = bytes(text).decode("utf-8", "ignore")
        text = text.replace("\x00", "").strip()
        if not text:
            return
        # The reader already holds the lock, so append inline rather than via note().
        severity = getattr(msg, "severity", 6)
        kind = EVENT_ERROR if severity <= SEVERITY_ERROR else EVENT_VEHICLE
        self._event_seq += 1
        snap.events.append(Event(seq=self._event_seq, at=time.time(), kind=kind, text=text))
        del snap.events[:-EVENT_HISTORY]
        if kind == EVENT_ERROR:
            snap.error = text

    def _on_command_ack(self, snap: Telemetry, msg: Any) -> None:
        # Only surface refusals. An ack for every accepted command would bury the
        # one that failed, and MAV_RESULT_ACCEPTED is 0.
        result = int(getattr(msg, "result", 0))
        if result == 0:
            return
        self._event_seq += 1
        snap.events.append(Event(
            seq=self._event_seq,
            at=time.time(),
            kind=EVENT_ERROR,
            text=f"command {msg.command} refused (result {result})",
        ))
        del snap.events[:-EVENT_HISTORY]

    _HANDLERS: Dict[str, Any] = {
        "HEARTBEAT": _on_heartbeat,
        "ATTITUDE": _on_attitude,
        "VFR_HUD": _on_vfr_hud,
        "SYS_STATUS": _on_sys_status,
        "BATTERY_STATUS": _on_battery_status,
        "GPS_RAW_INT": _on_gps,
        "SERVO_OUTPUT_RAW": _on_servo_output,
        "ESC_TELEMETRY_1_TO_4": _on_esc_telemetry,
        "ESC_TELEMETRY_5_TO_8": _on_esc_telemetry,
        "ESC_TELEMETRY_9_TO_12": _on_esc_telemetry,
        "STATUSTEXT": _on_status_text,
        "COMMAND_ACK": _on_command_ack,
    }

    # ------------------------------------------------------------------
    # transmit
    # ------------------------------------------------------------------
    def set_param(self, name: str, value: float) -> None:
        """Fire-and-forget PARAM_SET. The reader thread owns all receives."""
        conn = self.require()
        with self._tx_lock:
            conn.mav.param_set_send(
                conn.target_system,
                conn.target_component,
                name.encode("ascii"),
                float(value),
                mavutil.mavlink.MAV_PARAM_TYPE_REAL32,
            )

    def set_servo_function(self, channel: int, function: float) -> None:
        """Set SERVOn_FUNCTION, skipping the write when it already holds that value."""
        if self._function_cache.get(channel) == function:
            return
        self.set_param(f"SERVO{channel}_FUNCTION", function)
        self._function_cache[channel] = function

    def forget_servo_functions(self) -> None:
        """Drop the cache so the next assert re-sends every function."""
        self._function_cache.clear()

    def command_long(self, command: int, *params: float) -> None:
        conn = self.require()
        args = (list(params) + [0.0] * 7)[:7]
        with self._tx_lock:
            conn.mav.command_long_send(conn.target_system, conn.target_component, command, 0, *args)

    def command_long_quiet(self, command: int, *params: float) -> None:
        try:
            self.command_long(command, *params)
        except Exception:
            pass

    def set_servo_pwm(self, channel: int, pwm: int) -> None:
        """
        Drive one output directly.

        DO_SET_SERVO is refused unless the output's function is Disabled, so the
        function is forced first. That is a RAM-only change on the flight
        controller; it is not written to EEPROM.
        """
        self.set_servo_function(channel, FUNC_DISABLED)
        self.command_long(mavutil.mavlink.MAV_CMD_DO_SET_SERVO, float(channel), float(pwm))

    def set_servo_pwm_fast(self, channel: int, pwm: int) -> None:
        """As :meth:`set_servo_pwm` but assumes the function is already Disabled."""
        self.command_long(mavutil.mavlink.MAV_CMD_DO_SET_SERVO, float(channel), float(pwm))

    def motor_test(self, sequence: int, percent: float, seconds: float) -> None:
        self.command_long(
            mavutil.mavlink.MAV_CMD_DO_MOTOR_TEST,
            float(sequence),
            0.0,  # MOTOR_TEST_THROTTLE_PERCENT
            float(percent),
            float(seconds),
        )
