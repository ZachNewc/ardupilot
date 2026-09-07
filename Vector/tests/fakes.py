#!/usr/bin/env python3
"""
AP_FLAKE8_CLEAN
Test doubles shared by the Vector test suite.

``FakeLink`` stands in for :class:`server.link.MavlinkLink`. It records what would
have been written instead of opening a serial port, which lets the bench, the
levelling law and the state snapshot all be tested without hardware.

It deliberately implements the same surface the real link exposes to ``bench.py``.
If ``Bench`` starts calling a new method, the tests fail with a clear
``AttributeError`` rather than silently exercising a different code path.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "Vector", "dashboard"))

from server.link import Telemetry  # noqa: E402


class FakeLink:
    """Records writes, never touches a port."""

    def __init__(self, connected: bool = True) -> None:
        self.connected = connected
        self.reader_alive = connected
        self.writes: List[Tuple[int, int]] = []
        self.functions: Dict[int, float] = {}
        self.params: Dict[str, float] = {}
        self.notes: List[str] = []
        self.motor_tests: List[Tuple[int, float, float]] = []
        self.errors: List[str] = []
        self.stream_requests = 0
        self.armed_ops: List[str] = []
        self.modes: List[str] = []
        self.rc_overrides: List[Tuple[int, ...]] = []
        self._pwm: Dict[int, int] = {}
        self._telemetry = Telemetry(connected=connected)

    # -- connection ----------------------------------------------------
    def require(self) -> "FakeLink":
        if not self.connected:
            raise RuntimeError("Not connected to a flight controller")
        return self

    def connect(self, device: str, baud: int = 115200) -> str:
        self.connected = True
        self._telemetry.connected = True
        self._telemetry.port = device
        return f"Connected {device}"

    def disconnect(self) -> None:
        self.connected = False
        self._telemetry.connected = False

    def request_streams(self) -> str:
        self.stream_requests += 1
        return "streams requested"

    # -- snapshots -----------------------------------------------------
    def telemetry(self) -> Telemetry:
        self._telemetry.servo_pwm = dict(self._pwm)
        return self._telemetry

    def servo_pwm(self, channel: int, default: int = 1500) -> int:
        return int(self._pwm.get(channel, default))

    # -- transmit ------------------------------------------------------
    def set_servo_pwm_fast(self, channel: int, pwm: int) -> None:
        self.writes.append((channel, pwm))
        self._pwm[channel] = pwm

    def set_servo_pwm(self, channel: int, pwm: int) -> None:
        self.set_servo_pwm_fast(channel, pwm)

    def set_servo_function(self, channel: int, function: float) -> None:
        self.functions[channel] = function
        # The board echoes a PARAM_VALUE for every PARAM_SET, which is what makes a
        # read-back after a write meaningful. Modelled here so code that confirms its
        # writes is exercised rather than skipped.
        self.params[f"SERVO{channel}_FUNCTION"] = function

    def forget_servo_functions(self) -> None:
        self.functions.clear()

    def set_param(self, name: str, value: float) -> None:
        self.params[name] = value

    def get_param(self, name: str, default: Optional[float] = None) -> Optional[float]:
        return self.params.get(name, default)

    def motor_test(self, sequence: int, percent: float, seconds: float) -> None:
        self.motor_tests.append((sequence, percent, seconds))

    def set_mode_stabilize(self) -> None:
        self.modes.append("STABILIZE")

    def set_rc_override(self, channels: Sequence[int]) -> None:
        self.rc_overrides.append(tuple(int(v) for v in channels))

    def clear_rc_override(self) -> None:
        self.set_rc_override((0, 0, 0, 0, 0, 0, 0, 0))

    def force_arm(self) -> None:
        self.armed_ops.append("arm")
        self._telemetry.armed = True

    def force_disarm(self) -> None:
        self.armed_ops.append("disarm")
        self._telemetry.armed = False

    def wait_armed(self, want: bool, timeout: float = 3.0) -> bool:
        return self._telemetry.armed == want

    # -- events --------------------------------------------------------
    def note(self, message: str) -> str:
        self.notes.append(message)
        return message

    def record_error(self, message: str) -> None:
        self.errors.append(message)
        self._telemetry.error = message

    def __getattr__(self, name: str) -> Any:
        # Anything Bench needs that is not modelled here should fail loudly rather
        # than returning a Mock-like object that silently does nothing.
        raise AttributeError(
            f"FakeLink has no '{name}'. Add it here if MavlinkLink gained a method."
        )
