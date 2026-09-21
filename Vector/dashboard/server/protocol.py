#!/usr/bin/env python3
"""
AP_FLAKE8_CLEAN
WebSocket command dispatch.

Every command is one entry in ``COMMANDS``: a name, whether it needs a link, and a
handler that receives the session and the decoded message. Adding a command means
adding one row, and the table itself is what the UI's developer view lists.

Handlers are synchronous and may block on the serial link, so ``dispatch`` runs them
in a worker thread and the event loop never stalls behind a servo write.
"""

from __future__ import annotations

import asyncio
import glob
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from . import board
from . import config as vconfig
from . import kinematics as kin
from .bench import Bench
from .controller import MODE_LEVEL, MODE_OFF, LevelController


class CommandError(ValueError):
    """A command the operator got wrong, as opposed to an internal failure."""


@dataclass
class Session:
    """Process-wide state shared by every connected browser."""

    bench: Bench
    controller: LevelController

    def reload_config(self, cfg: vconfig.VehicleConfig) -> None:
        self.controller.stop()
        self.bench.replace_config(cfg)


Handler = Callable[[Session, Dict[str, Any]], Any]


@dataclass(frozen=True)
class Command:
    name: str
    handler: Handler
    needs_link: bool
    summary: str


def list_serial_ports() -> List[Dict[str, str]]:
    """Candidate MAVLink devices, with whatever description the OS offers."""
    found: Dict[str, str] = {}
    try:
        from serial.tools import list_ports

        for port in list_ports.comports():
            found[port.device] = port.description or port.device
    except Exception:
        pass
    for path in sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")):
        found.setdefault(path, path)
    return [{"device": device, "label": label} for device, label in sorted(found.items())]


def _float(msg: Dict[str, Any], key: str, default: Optional[float] = None) -> float:
    if key not in msg or msg[key] is None:
        if default is None:
            raise CommandError(f"'{key}' is required")
        return default
    try:
        return float(msg[key])
    except (TypeError, ValueError):
        raise CommandError(f"'{key}' must be a number") from None


def _str(msg: Dict[str, Any], key: str, default: Optional[str] = None) -> str:
    value = msg.get(key, default)
    if value is None:
        raise CommandError(f"'{key}' is required")
    return str(value)


def _arms(msg: Dict[str, Any]) -> Optional[List[str]]:
    value = msg.get("arms")
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


# ----------------------------------------------------------------------------
# link
# ----------------------------------------------------------------------------
def cmd_ports(session: Session, msg: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "ports", "data": list_serial_ports()}


def cmd_connect(session: Session, msg: Dict[str, Any]) -> str:
    link = session.bench.config.link
    device = _str(msg, "device", link.device)
    baud = int(_float(msg, "baud", link.baud))
    text = session.bench.link.connect(device, baud)
    session.bench.assert_output_mapping()
    return text


def cmd_disconnect(session: Session, msg: Dict[str, Any]) -> str:
    session.controller.stop()
    session.bench.stop_oscillate()
    session.bench.link.disconnect()
    return "Disconnected"


def cmd_refresh_streams(session: Session, msg: Dict[str, Any]) -> str:
    return session.bench.link.request_streams()


def cmd_assert_mapping(session: Session, msg: Dict[str, Any]) -> str:
    return session.bench.assert_output_mapping()


# ----------------------------------------------------------------------------
# aiming
# ----------------------------------------------------------------------------
def cmd_aim(session: Session, msg: Dict[str, Any]) -> str:
    return session.bench.aim(
        _arms(msg),
        _float(msg, "forward"),
        _float(msg, "right"),
        _float(msg, "speed", 0.0),
    )


def cmd_live_aim(session: Session, msg: Dict[str, Any]) -> None:
    session.bench.live_aim(_arms(msg), _float(msg, "forward"), _float(msg, "right"))


def cmd_live_end(session: Session, msg: Dict[str, Any]) -> None:
    session.bench.end_live_aim()


def cmd_oscillate(session: Session, msg: Dict[str, Any]) -> str:
    """Sweep selected arms around the widest circular lean they can hold."""
    if not bool(msg.get("active", False)):
        return session.bench.stop_oscillate()
    if not session.bench.link.connected:
        raise CommandError("Not connected to a flight controller")
    session.controller.stop()
    return session.bench.start_oscillate(_arms(msg))


def cmd_set_axis(session: Session, msg: Dict[str, Any]) -> str:
    return session.bench.set_axis(
        _str(msg, "arm"),
        _str(msg, "axis"),
        _float(msg, "deg"),
        _float(msg, "speed", 0.0),
    )


def cmd_set_tilt(session: Session, msg: Dict[str, Any]) -> str:
    return session.bench.set_tilt(
        _str(msg, "arm"),
        _float(msg, "outer"),
        _float(msg, "inner"),
        _float(msg, "speed", 0.0),
    )


def cmd_center(session: Session, msg: Dict[str, Any]) -> str:
    return session.bench.center(_arms(msg), _float(msg, "speed", 0.0))


# ----------------------------------------------------------------------------
# motors
# ----------------------------------------------------------------------------
def cmd_spin_motors(session: Session, msg: Dict[str, Any]) -> str:
    which = msg.get("motors") or []
    if isinstance(which, str):
        which = [which]
    return session.bench.spin_motors(
        _arms(msg),
        [str(item) for item in which],
        _float(msg, "percent", 5.0),
        _float(msg, "seconds", 2.0),
    )


def cmd_stop_motors(session: Session, msg: Dict[str, Any]) -> str:
    return session.bench.stop_motors()


def cmd_probe_output(session: Session, msg: Dict[str, Any]) -> str:
    """Drive one SERVO pin as a motor or a servo so a mapping can be confirmed by eye."""
    return session.bench.probe_output(int(_float(msg, "channel")), _str(msg, "kind"))


# ----------------------------------------------------------------------------
# levelling controller
# ----------------------------------------------------------------------------
def cmd_level(session: Session, msg: Dict[str, Any]) -> str:
    """
    Start, stop or retune a bench levelling or accel-hold demo.

    Not gated on ``needs_link``, because stopping and retuning must work whether or
    not a link is up -- only starting needs the vehicle, and that is checked below so
    the operator gets a plain sentence instead of a raised exception name.

    ``mode`` selects the law (``level`` or ``accel``). Stopping without a mode stops
    whichever demo is running; stopping with the other mode leaves it alone so a
    slider on the idle page cannot kill the active one.
    """
    session.controller.configure(
        level_gain=msg.get("levelGain"),
        lead_time_s=msg.get("leadTimeS"),
        max_tilt_fraction=msg.get("maxTiltFraction"),
        invert_roll=msg.get("invertRoll"),
        invert_pitch=msg.get("invertPitch"),
        accel_gain_deg_g=msg.get("accelGainDegG"),
        accel_deadband_g=msg.get("accelDeadbandG"),
        invert_accel_x=msg.get("invertAccelX"),
        invert_accel_y=msg.get("invertAccelY"),
    )
    if not bool(msg.get("active", False)):
        requested = msg.get("mode")
        current = session.controller.status().mode
        # A retune from the idle page must not kill the other demo. Stop with no
        # mode, or with the running mode, still means stop.
        if requested is not None and current not in (MODE_OFF, str(requested)):
            return f"Left {current} running"
        return session.controller.stop()

    if not session.bench.link.connected:
        raise CommandError("Not connected to a flight controller")
    if not session.bench.config.live_arms():
        raise CommandError("No arms are marked live in the config")
    session.bench.stop_oscillate()
    return session.controller.start(_str(msg, "mode", MODE_LEVEL))


# ----------------------------------------------------------------------------
# configuration
# ----------------------------------------------------------------------------
def cmd_get_config(session: Session, msg: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "config",
        "data": {
            **vconfig.to_dict(session.bench.config),
            "workspaces": {
                arm.id: kin.workspace_payload(arm) for arm in session.bench.config.arms
            },
            "outputMap": board.payload(session.bench.config),
            # The document exactly as it sits on disk. The Setup page edits a copy of
            # this and submits fragments of it, so keys the UI does not know about
            # survive a round trip instead of being silently dropped.
            "document": session.bench.config.raw,
        },
    }


def cmd_save_config(session: Session, msg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and persist a config edit from the Setup page.

    The merged document is checked before anything is written, so a bad edit leaves
    the file on disk untouched and the running vehicle unchanged.
    """
    updates = msg.get("updates")
    if not isinstance(updates, dict):
        raise CommandError("'updates' must be an object")
    try:
        validated = vconfig.save(session.bench.config, updates)
    except vconfig.ConfigError as exc:
        raise CommandError(f"config rejected: {exc}") from None
    session.reload_config(validated)
    return {
        "type": "ack",
        "ok": True,
        "message": f"Config saved to {validated.path}",
        "reloadConfig": True,
    }


def cmd_reload_config(session: Session, msg: Dict[str, Any]) -> Dict[str, Any]:
    try:
        reloaded = vconfig.load(session.bench.config.path or vconfig.DEFAULT_CONFIG_PATH)
    except (OSError, vconfig.ConfigError) as exc:
        raise CommandError(f"could not reload config: {exc}") from None
    session.reload_config(reloaded)
    return {"type": "ack", "ok": True, "message": "Config reloaded from disk", "reloadConfig": True}


COMMANDS: Dict[str, Command] = {
    command.name: command
    for command in (
        Command("ports", cmd_ports, False, "List candidate serial devices"),
        Command("connect", cmd_connect, False, "Open the MAVLink link and assert output mapping"),
        Command("disconnect", cmd_disconnect, False, "Close the MAVLink link"),
        Command("refresh_streams", cmd_refresh_streams, True, "Re-request telemetry stream rates"),
        Command("assert_mapping", cmd_assert_mapping, True, "Re-apply motor functions and DShot direction"),
        Command("aim", cmd_aim, True, "Point thrust at a body-frame lean"),
        Command("live_aim", cmd_live_aim, True, "Streamed aim while dragging; unacked"),
        Command("live_end", cmd_live_end, False, "Release the outputs after a drag"),
        Command("oscillate", cmd_oscillate, False, "Sweep gimbals around the envelope circle"),
        Command("set_axis", cmd_set_axis, True, "Drive one gimbal axis to an angle"),
        Command("set_tilt", cmd_set_tilt, True, "Drive both gimbal axes of one arm"),
        Command("center", cmd_center, True, "Return gimbals to zero tilt"),
        Command("spin_motors", cmd_spin_motors, True, "Run a bounded motor test"),
        Command("stop_motors", cmd_stop_motors, True, "Cancel any running motor test"),
        Command("probe_output", cmd_probe_output, True, "Spin or sweep one SERVO pin to confirm the wiring"),
        Command("level", cmd_level, False, "Start, stop or retune a bench levelling or accel-hold demo"),
        Command("get_config", cmd_get_config, False, "Send the vehicle config and derived geometry"),
        Command("save_config", cmd_save_config, False, "Validate and persist a config edit"),
        Command("reload_config", cmd_reload_config, False, "Re-read the config file from disk"),
    )
}

# Commands sent at pointer rate. They are answered with silence on purpose: an ack per
# update would both flood the activity feed and add a round trip to every drag frame.
UNACKED = {"live_aim", "live_end"}


async def dispatch(session: Session, msg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run one client command and return the reply, or an empty dict for silence.

    The request names its command under ``command``; the reply names its kind under
    ``type``. Every ack echoes the command name, which is how the browser knows which
    button to re-enable.
    """
    name = str(msg.get("command", ""))
    request_id = msg.get("id")

    command = COMMANDS.get(name)
    if command is None:
        return _fail(name, request_id, f"unknown command '{name}'")

    if command.needs_link and not session.bench.link.connected:
        return _fail(name, request_id, "Not connected to a flight controller")

    try:
        result = await asyncio.to_thread(command.handler, session, msg)
    except CommandError as exc:
        return _fail(name, request_id, str(exc))
    except Exception as exc:
        return _fail(name, request_id, f"{type(exc).__name__}: {exc}")

    if name in UNACKED:
        return {}
    if isinstance(result, dict):
        return {**result, "command": name, "id": request_id}
    return {
        "type": "ack",
        "command": name,
        "id": request_id,
        "ok": True,
        "message": str(result),
    }


def _fail(name: str, request_id: Any, message: str) -> Dict[str, Any]:
    return {
        "type": "ack",
        "command": name,
        "id": request_id,
        "ok": False,
        "message": message,
    }


def command_catalogue() -> List[Dict[str, Any]]:
    """The command table, for the developer view in the UI."""
    return [
        {"name": command.name, "needsLink": command.needs_link, "summary": command.summary}
        for command in sorted(COMMANDS.values(), key=lambda item: item.name)
    ]
