#!/usr/bin/env python3
"""
AP_FLAKE8_CLEAN
Single owner for every servo output the dashboard drives.

Four things want to move the gimbals: a manual angle entry, a live pointer drag, a
timed interpolation, and the bench stabiliser. Letting them all send whenever they
like is how you get two sources fighting over one servo, so they all go through this
arbiter instead. Taking control implicitly drops whoever held it before, which
replaces a pile of mutual ``stop_other_thing()`` calls with one rule.

A single worker thread drains pending values at a fixed rate. Pointer motion and
controller updates both arrive far faster than a serial link can carry them, so the
newest value for a channel simply overwrites the pending one.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Mapping, Optional, Tuple

# Bytes on the wire for one COMMAND_LONG carrying DO_SET_SERVO: 12 byte MAVLink 2
# header, 33 byte payload, 2 byte checksum, rounded up for signing-free framing.
BYTES_PER_SERVO_COMMAND = 47

IDLE_TIMEOUT_S = 3.0


@dataclass(frozen=True)
class LinkBudget:
    """How much of the serial link a given command rate would consume."""

    channels: int
    rate_hz: float
    bytes_per_second: float
    capacity_bytes_per_second: float
    utilisation: float
    over_budget: bool


def link_budget(channels: int, rate_hz: float, baud: int, usb: bool) -> LinkBudget:
    """
    Estimate link load for driving ``channels`` outputs at ``rate_hz``.

    USB CDC ignores the configured baud rate and runs orders of magnitude faster, so
    the budget only bites on a real radio. Eight servos at 25 Hz is about 9.4 kB/s,
    which would swamp a 115200 baud radio but is nothing over USB.
    """
    demand = channels * rate_hz * BYTES_PER_SERVO_COMMAND
    # 8N1 framing is 10 bits per byte. Leave a third of the link for telemetry.
    capacity = (2_000_000.0 if usb else baud / 10.0) * 0.66
    utilisation = demand / capacity if capacity > 0 else 1.0
    return LinkBudget(
        channels=channels,
        rate_hz=rate_hz,
        bytes_per_second=demand,
        capacity_bytes_per_second=capacity,
        utilisation=utilisation,
        over_budget=utilisation > 1.0,
    )


class OutputArbiter:
    """Rate-limited, single-owner servo output path."""

    def __init__(
        self,
        send: Callable[[int, int], None],
        prepare: Callable[[Iterable[int]], None],
        on_error: Callable[[str], None],
        rate_hz: float = 25.0,
    ) -> None:
        self._send = send
        self._prepare = prepare
        self._on_error = on_error
        self._rate_hz = max(1.0, rate_hz)

        self._lock = threading.Lock()
        self._owner: Optional[str] = None
        self._generation = 0
        self._pending: Dict[int, int] = {}
        self._last_sent: Dict[int, int] = {}
        self._prepared: set = set()
        self._worker: Optional[threading.Thread] = None
        self._wake = threading.Event()
        self._shutdown = False
        self._ramp: Optional[_Ramp] = None

    # ------------------------------------------------------------------
    # ownership
    # ------------------------------------------------------------------
    @property
    def owner(self) -> Optional[str]:
        with self._lock:
            return self._owner

    def acquire(self, owner: str) -> int:
        """
        Take control of the outputs, dropping whoever held them.

        Returns a generation token. Any in-flight ramp or controller loop holding an
        older token stops writing, which is what makes hand-off safe.
        """
        with self._lock:
            self._owner = owner
            self._generation += 1
            self._ramp = None
            self._ensure_worker_locked()
            return self._generation

    def release(self, owner: str) -> None:
        with self._lock:
            if self._owner == owner:
                self._owner = None
                self._generation += 1
                self._ramp = None

    def holds(self, generation: int) -> bool:
        with self._lock:
            return self._generation == generation

    # ------------------------------------------------------------------
    # writing
    # ------------------------------------------------------------------
    def write(self, values: Mapping[int, int], generation: Optional[int] = None) -> bool:
        """
        Queue pulse widths. Returns False when the caller has been superseded.

        Passing no generation means "write regardless", which is only correct for
        one-shot commands that have just called :meth:`acquire`.
        """
        with self._lock:
            if generation is not None and generation != self._generation:
                return False
            self._pending.update({int(ch): int(us) for ch, us in values.items()})
            self._ensure_worker_locked()
        self._wake.set()
        return True

    def ramp(self, targets: Mapping[int, int], starts: Mapping[int, int], duration_s: float, generation: int) -> None:
        """
        Interpolate several channels so they all arrive together.

        A ramp is just a time-varying source feeding the same queue, so it competes
        for ownership on exactly the same terms as everything else.
        """
        if duration_s <= 0.0:
            self.write(targets, generation)
            return
        with self._lock:
            if generation != self._generation:
                return
            self._ramp = _Ramp(
                starts={int(k): int(v) for k, v in starts.items()},
                targets={int(k): int(v) for k, v in targets.items()},
                duration_s=duration_s,
                started_at=time.monotonic(),
                generation=generation,
            )
            self._ensure_worker_locked()
        self._wake.set()

    def ramp_active(self) -> bool:
        with self._lock:
            return self._ramp is not None

    def cancel_ramp(self) -> None:
        with self._lock:
            self._ramp = None

    def forget(self) -> None:
        """Clear the sent-value cache so the next write is resent even if unchanged."""
        with self._lock:
            self._last_sent.clear()
            self._prepared.clear()

    def stop(self) -> None:
        with self._lock:
            self._shutdown = True
            self._owner = None
            self._ramp = None
            self._pending.clear()
        self._wake.set()

    # ------------------------------------------------------------------
    # worker
    # ------------------------------------------------------------------
    def _ensure_worker_locked(self) -> None:
        if self._shutdown:
            return
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._run, name="vector-outputs", daemon=True)
        self._worker.start()

    def _run(self) -> None:
        period = 1.0 / self._rate_hz
        idle_since = time.monotonic()
        while True:
            self._wake.wait(timeout=period)
            self._wake.clear()

            with self._lock:
                if self._shutdown:
                    return
                batch = self._collect_locked()

            if not batch:
                if time.monotonic() - idle_since > IDLE_TIMEOUT_S:
                    with self._lock:
                        # Re-check under the lock so a write racing the timeout wins.
                        if not self._pending and self._ramp is None:
                            self._worker = None
                            return
                continue

            idle_since = time.monotonic()
            try:
                self._prepare([channel for channel, _ in batch])
                for channel, pwm in batch:
                    self._send(channel, pwm)
            except Exception as exc:
                self._on_error(str(exc))
                with self._lock:
                    self._pending.clear()
                    self._ramp = None

    def _collect_locked(self) -> Tuple[Tuple[int, int], ...]:
        """Fold any active ramp into the pending set, then take everything that changed."""
        ramp = self._ramp
        if ramp is not None:
            if ramp.generation != self._generation:
                self._ramp = None
            else:
                values, done = ramp.sample(time.monotonic())
                self._pending.update(values)
                if done:
                    self._ramp = None

        if not self._pending:
            return ()

        batch = tuple(
            (channel, pwm)
            for channel, pwm in sorted(self._pending.items())
            if self._last_sent.get(channel) != pwm
        )
        self._last_sent.update(self._pending)
        self._pending.clear()
        return batch


@dataclass
class _Ramp:
    starts: Dict[int, int]
    targets: Dict[int, int]
    duration_s: float
    started_at: float
    generation: int

    def sample(self, now: float) -> Tuple[Dict[int, int], bool]:
        fraction = (now - self.started_at) / self.duration_s
        if fraction >= 1.0:
            return dict(self.targets), True
        fraction = max(0.0, fraction)
        values = {}
        for channel, target in self.targets.items():
            start = self.starts.get(channel, target)
            values[channel] = int(round(start + (target - start) * fraction))
        return values, False
