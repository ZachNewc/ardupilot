#!/usr/bin/env python3
"""
AP_FLAKE8_CLEAN
Which outputs this vehicle physically has, and what each one is capable of.

Everything else in the server treats an output as a number. That is fine right up to
the point where the number names a pin that cannot do what is being asked of it, and
then it is silent: the parameter writes succeed, the flight controller acknowledges
every command, and the servo does not move.

Three hardware facts cause almost all of that silence on this vehicle.

**Timer groups.** The H743's PWM pins are driven in groups by a shared timer, and a
group is either all DShot or all normal PWM. One motor anywhere in a group takes the
whole group, so a servo sharing a timer with an ESC emits nothing. This is a property
of the microcontroller; no parameter overrides it.

**The mixer's default channels.** ``AP_Motors::add_motor_num`` calls
``SRV_Channels::set_aux_channel_default(function, motor_num)``, which places MotorN on
SERVO(N) unless some channel already claims that function -- and it treats
``SERVOn_FUNCTION = 0`` as unclaimed, because Disabled *is* ``k_none``. So every motor
slot the config does not place lands on a low channel at boot, turns it into a DShot
output, and takes its whole timer group with it. A gimbal servo on S1-S8 that looked
fine yesterday stops working the moment a motor slot goes unassigned.

**CAN is two different paths.** A servo on the CAN-to-PWM node is an
``ActuatorCommand`` gated by ``CAN_D1_UC_SRV_BM``; a motor there is an ESC
``RawCommand`` gated by ``CAN_D1_UC_ESC_BM``, and ``AP_DroneCAN::SRV_send_esc`` sends
zero to every ESC unless the vehicle is soft-armed. Nothing that drives a pin directly
while disarmed can turn a CAN motor, and neither BLHeli passthrough nor
``SERVO_BLH_RVMASK`` reaches an ESC behind the node.

The rules live here rather than in ``bench`` because they are true of the hardware
whether or not anything is plugged in, so the Setup page and the offline tests can
check a config against them without a flight controller in the room.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, NamedTuple, Optional, Tuple

from . import frames
from .config import AXIS_NAMES, VehicleConfig, channel_assigned

# PWM pins the Matek H743-Wing V3 exposes. Anything above this has no local timer.
BOARD_PWM_CHANNELS = 13

# S13 is TIM1 alone and the hwdef comments it "for WS2812 LED". Usable as a servo pin
# if the LED is given up, so this is a caution rather than a refusal.
LED_CHANNEL = 13

# The CAN-to-PWM node's outputs are numbered from S14 up: S14 is the node's first
# output, S15 its second, and so on. That mapping is not automatic -- it holds because
# each node output's own OUTx_FUNCTION names the function that reaches it.
CAN_SERVO_FIRST = 14
CAN_NODE_OUTPUTS = 8
CAN_SERVO_LAST = CAN_SERVO_FIRST + CAN_NODE_OUTPUTS - 1

# A servo on the node: OUTx_FUNCTION is 50 + the SERVO channel (64 for S14).
CAN_FUNCTION_BASE = 50

# An ESC on the node: RawCommand slot k feeds the node output whose OUTx_FUNCTION is
# Motor(k + 1), i.e. 33 + k. CAN_D1_UC_ESC_OF shifts S14 down to slot 0, so the node's
# first output is Motor1 (33) regardless of where the channels sit on the controller.
CAN_ESC_OFFSET = CAN_SERVO_FIRST - 1
CAN_ESC_FUNCTION_BASE = 33

# SERVO17..SERVO32 do not exist as parameters until SERVO_32_ENABLE is set.
SERVO_32_FIRST = 17


class TimerGroup(NamedTuple):
    """One hardware timer and the outputs it drives. All of them share an output mode."""

    name: str
    channels: Tuple[int, ...]


# libraries/AP_HAL_ChibiOS/hwdef/MatekH743/hwdef.dat, the PWM(n) lines.
TIMER_GROUPS: Tuple[TimerGroup, ...] = (
    TimerGroup("TIM8", (1, 2)),
    TimerGroup("TIM5", (3, 4, 5, 6)),
    TimerGroup("TIM4", (7, 8, 9, 10)),
    TimerGroup("TIM15", (11, 12)),
    TimerGroup("TIM1", (13,)),
)

FATAL = "fatal"
WARNING = "warning"


@dataclass(frozen=True)
class Problem:
    """Something about an output map that the hardware will not honour."""

    severity: str
    channel: int
    text: str

    @property
    def fatal(self) -> bool:
        return self.severity == FATAL


def group_for(channel: int) -> Optional[TimerGroup]:
    """The timer group a channel belongs to, or None for CAN and out-of-range."""
    for group in TIMER_GROUPS:
        if int(channel) in group.channels:
            return group
    return None


def is_onboard(channel: int) -> bool:
    return channel_assigned(channel) and int(channel) <= BOARD_PWM_CHANNELS


def is_can(channel: int) -> bool:
    return int(channel) > BOARD_PWM_CHANNELS


def uses_motor_test(channel: int, motor_channels) -> bool:
    """
    True when DO_SET_SERVO cannot turn this motor.

    A CAN ESC is silent unless the vehicle is soft-armed. A pad that shares a
    timer group with any motor is DShot when MOT_PWM_TYPE is DShot, and DShot
    ignores a PWM pulse. Both have to go through DO_MOTOR_TEST.
    """
    if is_can(channel):
        return True
    group = group_for(channel)
    if group is None:
        return False
    motors = {int(item) for item in motor_channels}
    return any(int(pad) in motors for pad in group.channels)


def can_output(channel: int) -> Optional[int]:
    """Which output on the CAN-to-PWM node a channel is, counting from 1."""
    if not is_can(channel):
        return None
    return int(channel) - CAN_SERVO_FIRST + 1


def can_function(channel: int) -> int:
    """The node's ``OUTx_FUNCTION`` for a *servo* on this channel."""
    return CAN_FUNCTION_BASE + int(channel)


def can_esc_function(channel: int) -> int:
    """The node's ``OUTx_FUNCTION`` for an *ESC* on this channel, given CAN_D1_UC_ESC_OF."""
    return CAN_ESC_FUNCTION_BASE + int(channel) - 1 - CAN_ESC_OFFSET


def describe(channel: int) -> str:
    """Human-readable location of an output, for messages that have to name a pin."""
    channel = int(channel)
    if not channel_assigned(channel):
        return "disabled (channel 0 is not a pin)"
    if is_can(channel):
        output = can_output(channel)
        if output is not None and 1 <= output <= CAN_NODE_OUTPUTS:
            return f"S{channel}, CAN-to-PWM output {output}"
        return f"S{channel}, above the CAN-to-PWM node's {CAN_NODE_OUTPUTS} outputs"
    group = group_for(channel)
    if group is None:
        return f"S{channel}, not a pin on this board"
    siblings = ", ".join(f"S{c}" for c in group.channels if c != channel)
    return f"S{channel}, onboard {group.name}" + (f" with {siblings}" if siblings else "")


def _claims(cfg: VehicleConfig) -> Tuple[Dict[int, str], Dict[int, str]]:
    """Every assigned channel, split into servo claims and motor claims."""
    servos: Dict[int, str] = {}
    motors: Dict[int, str] = {}
    for arm in cfg.arms:
        for name in AXIS_NAMES:
            channel = arm.gimbal.axis(name).channel
            if channel_assigned(channel):
                servos[channel] = f"{arm.id} {name} servo"
        for name, motor in arm.motors.items():
            if channel_assigned(motor.channel):
                motors[motor.channel] = f"{arm.id} {name} motor"
    return servos, motors


def can_servo_channels(cfg: VehicleConfig) -> List[int]:
    """Gimbal servos that leave the vehicle as DroneCAN actuator commands."""
    servos, _ = _claims(cfg)
    return sorted(c for c in servos if is_can(c))


def can_esc_channels(cfg: VehicleConfig) -> List[int]:
    """Motors that leave the vehicle as DroneCAN ESC RawCommands."""
    _, motors = _claims(cfg)
    return sorted(c for c in motors if is_can(c))


def unplaced_motor_slots(cfg: VehicleConfig) -> Dict[int, int]:
    """
    Motor slots the config leaves for the firmware to place, as ``{motor number: channel}``.

    The channel is where ``set_aux_channel_default`` will put it at the next boot:
    MotorN defaults to SERVO(N). An empty result is the goal -- it means the config
    owns the whole mixer and no output can change role behind its back.
    """
    frame = (cfg.raw.get("vehicle") or {}).get("frame") or {}
    slots = frames.table(frame.get("frame_class"), frame.get("frame_type")) if frame else None
    if slots is None:
        return {}

    placed = {
        motor.function
        for arm in cfg.arms
        for motor in arm.motors.values()
        if motor.function is not None and channel_assigned(motor.channel)
    }
    return {
        number: number
        for number, slot in sorted(slots.items())
        if slot.function not in placed
    }


def check_output_map(cfg: VehicleConfig) -> List[Problem]:
    """
    Every way this config's channel map disagrees with the hardware, worst first.

    Checks only what is silent on the vehicle. A channel conflict already refuses to
    load in ``config.from_dict``; everything here loads cleanly, writes cleanly, and
    then does nothing.
    """
    servos, motors = _claims(cfg)
    problems: List[Problem] = []

    # A motor anywhere in a timer group puts the whole group in DShot. Report it
    # against the servo, because the servo is the output that goes quiet.
    for channel, who in sorted(servos.items()):
        group = group_for(channel)
        if group is None:
            continue
        conflicting = [c for c in group.channels if c in motors]
        if not conflicting:
            continue
        names = ", ".join(f"S{c} ({motors[c]})" for c in conflicting)
        problems.append(Problem(
            FATAL, channel,
            f"S{channel} ({who}) shares {group.name} with {names}. A timer group is "
            "either all DShot or all PWM, so this pin emits no servo pulse at all. "
            "Move the servo to a group with no motor in it, or to the CAN-to-PWM node.",
        ))

    # Motor slots the config does not place get handed a low channel at boot. The pin
    # it lands on is rarely the whole story: it takes its timer group with it, so name
    # every servo in that group rather than only the one that was overwritten.
    unplaced = unplaced_motor_slots(cfg)
    for number, channel in unplaced.items():
        group = group_for(channel)
        casualties = [c for c in (group.channels if group else (channel,)) if c in servos]
        if not casualties:
            problems.append(Problem(
                WARNING, channel,
                f"Motor{number} has no channel in the config, so the mixer will claim "
                f"S{channel} at boot and drive it as an ESC output.",
            ))
            continue
        group_name = group.name if group else "its timer group"
        dead = ", ".join(f"S{c} ({servos[c]})" for c in casualties)
        problems.append(Problem(
            FATAL, channel,
            f"Motor{number} has no channel in the config, so the mixer places it on "
            f"S{channel} at boot. That puts {group_name} into DShot, which silences "
            f"every servo in the group: {dead}. Give Motor{number} an explicit channel "
            "on a group that carries only motors.",
        ))

    # Outputs that need something switched on before they exist at all.
    for channel, who in sorted(list(servos.items()) + list(motors.items())):
        if is_can(channel):
            output = can_output(channel)
            if output is None or output > CAN_NODE_OUTPUTS:
                problems.append(Problem(
                    WARNING, channel,
                    f"S{channel} ({who}) is past the CAN-to-PWM node's "
                    f"{CAN_NODE_OUTPUTS} outputs (S{CAN_SERVO_FIRST}-S{CAN_SERVO_LAST}).",
                ))
        elif channel == LED_CHANNEL:
            problems.append(Problem(
                WARNING, channel,
                f"S{LED_CHANNEL} ({who}) is the WS2812 LED pin on this board. It will "
                "only drive an output if the LED is given up.",
            ))
        elif group_for(channel) is None:
            problems.append(Problem(
                FATAL, channel,
                f"S{channel} ({who}) is not a pin on this board and not on the "
                f"CAN-to-PWM node.",
            ))

    # Direction on a CAN ESC is not something the flight controller can set. The
    # config's `reversed` still records the intent, so say where it has to be applied.
    for arm in cfg.arms:
        for name, motor in arm.motors.items():
            if motor.reversed and channel_assigned(motor.channel) and is_can(motor.channel):
                problems.append(Problem(
                    WARNING, motor.channel,
                    f"S{motor.channel} ({arm.id} {name} motor) is a CAN ESC marked "
                    "reversed. The DShot reverse mask and BLHeli passthrough stop at the "
                    "flight controller's own pins, so set the direction in that ESC's "
                    "own configuration or by swapping two of its motor wires.",
                ))

    problems.sort(key=lambda p: (0 if p.fatal else 1, p.channel))
    return problems


def needs_servo_32(cfg: VehicleConfig) -> bool:
    """True when the map uses an output that SERVO_32_ENABLE gates."""
    servos, motors = _claims(cfg)
    return any(channel >= SERVO_32_FIRST for channel in list(servos) + list(motors))


def summary(cfg: VehicleConfig) -> str:
    """One line per timer group plus the CAN node, for a report or the Setup page."""
    servos, motors = _claims(cfg)
    lines = []
    for group in TIMER_GROUPS:
        cells = []
        for channel in group.channels:
            who = servos.get(channel) or motors.get(channel)
            cells.append(f"S{channel}={who}" if who else f"S{channel}=free")
        kind = "DShot" if any(c in motors for c in group.channels) else "PWM"
        lines.append(f"{group.name:<6} {kind:<6} " + "  ".join(cells))
    can = sorted(c for c in list(servos) + list(motors) if is_can(c))
    if can:
        cells = [
            f"S{c}(out {can_output(c)})={servos.get(c) or motors.get(c)}" for c in can
        ]
        lines.append("CAN    node   " + "  ".join(cells))
    return "\n".join(lines)


def payload(cfg: VehicleConfig) -> Dict[str, object]:
    """
    The output map as the web UI wants it: what each pin is, and what is wrong.

    Sent with the config rather than with telemetry because none of it depends on a
    link. A map that cannot work is wrong on the bench, before anything is plugged in,
    and the Setup page is where it gets edited.
    """
    servos, motors = _claims(cfg)

    def cell(channel: int) -> Dict[str, object]:
        return {
            "channel": channel,
            "owner": servos.get(channel) or motors.get(channel),
            "kind": "servo" if channel in servos else ("motor" if channel in motors else None),
        }

    groups: List[Dict[str, object]] = [
        {
            "name": group.name,
            "mode": "DShot" if any(c in motors for c in group.channels) else "PWM",
            "outputs": [cell(c) for c in group.channels],
        }
        for group in TIMER_GROUPS
    ]

    can = sorted(c for c in list(servos) + list(motors) if is_can(c))
    if can:
        groups.append({
            "name": "CAN-to-PWM",
            "mode": "CAN",
            "outputs": [
                {
                    **cell(c),
                    "canOutput": can_output(c),
                    # What the node itself has to be told for this output to answer.
                    "nodeFunction": can_esc_function(c) if c in motors else can_function(c),
                }
                for c in can
            ],
        })

    return {
        "groups": groups,
        "problems": [
            {"severity": p.severity, "channel": p.channel, "text": p.text}
            for p in check_output_map(cfg)
        ],
        "boardPwmChannels": BOARD_PWM_CHANNELS,
        "canServoFirst": CAN_SERVO_FIRST,
        "canEscChannels": can_esc_channels(cfg),
    }
