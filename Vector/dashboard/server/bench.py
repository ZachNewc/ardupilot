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
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import board
from . import frames
from . import kinematics as kin
from .config import CHANNEL_MAX, ArmConfig, VehicleConfig, channel_assigned
from .link import FUNC_DISABLED, MavlinkLink
from .outputs import LinkBudget, OutputArbiter, link_budget

OWNER_MANUAL = "manual"
OWNER_LIVE = "live"
OWNER_STABILIZE = "stabilize"

# DShot reverse direction is asserted through BLHeli passthrough.
DSHOT_ESC_BLHELI = 1

# SERVOn_FUNCTION values for Motor1..Motor8.
FUNC_MOTOR_FIRST = 33
FUNC_MOTOR_LAST = 40

# Which pins exist, which timer drives each one, and where the CAN-to-PWM node's
# outputs start. All of that is hardware, so it lives in `board` and is imported here
# rather than restated -- a second copy of the channel count is how a servo ends up on
# a pin that cannot produce a pulse.
BOARD_PWM_CHANNELS = board.BOARD_PWM_CHANNELS
CAN_SERVO_FIRST = board.CAN_SERVO_FIRST
CAN_SERVO_LAST = board.CAN_SERVO_LAST

# CAN_Dx_UC_OPTION bit 4: send raw pulse widths instead of a scaled [-1, 1].
# PWM nodes want the pulse; the unitless form also clips our 500-2500 window.
CAN_OPTION_SEND_PWM = 16

CAN_PROTOCOL_DRONECAN = 1.0
CAN_DRIVER_FIRST = 1.0

# SERIALn_PROTOCOL 16 is ESC Telemetry (BLHeli T-wire into RX).
SERIAL_PROTOCOL_ESC_TELEM = 16.0

# Zero throttle. A 1000 us pulse stops a PWM ESC, and DShot maps 1000-2000 us onto
# its throttle range, so one number covers both output modes.
MOTOR_STOP_US = 1000

# MOT_PWM_MIN/MAX defaults, used only when the board does not answer the read.
MOTOR_PWM_MIN_DEFAULT = 1000
MOTOR_PWM_MAX_DEFAULT = 2000

# How often a PWM pin-probe resends its throttle.
SPIN_REFRESH_HZ = 10.0

# Motor test writes one mixer slot per main-loop pass. A burst of COMMAND_LONG
# on USB finishes in a few milliseconds, so most slots never get a cycle and
# the set comes up at random. Wait for soft-arm/interlock on the first, then
# long enough for several output cycles after each of the rest.
MOTOR_TEST_ARM_S = 0.4
MOTOR_TEST_LATCH_S = 0.1


@dataclass(frozen=True)
class MotorTarget:
    """One motor a spin is addressing, by output channel rather than test order."""

    arm_id: str
    role: str
    channel: int
    function: int


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
        self._spin_stop = threading.Event()
        self._spin_thread: Optional[threading.Thread] = None
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
        # A running spin holds channel numbers and motor functions from the old config,
        # so it has to finish putting those back before the new one is in force.
        self._halt_spin()
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
            if channel_assigned(channel):
                self.link.set_servo_function(channel, FUNC_DISABLED)

    def _current_pwm(self, arms: Sequence[ArmConfig]) -> Dict[int, int]:
        values: Dict[int, int] = {}
        for arm in arms:
            for name in ("outer", "inner"):
                axis = arm.gimbal.axis(name)
                if channel_assigned(axis.channel):
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
                if channel_assigned(channel):
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
                if channel_assigned(channel):
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
        * **ESC telemetry UARTs are protocol 16.** RX3 is SERIAL4 and RX4 is
          SERIAL6 on this board. A change only takes effect at boot.
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
                if not channel_assigned(motor.channel):
                    continue
                if motor.function is None:
                    motors_unmapped.append(f"{arm.id}.{name} (S{motor.channel})")
                    continue
                self.link.set_servo_function(motor.channel, float(motor.function))
                motors_mapped.append(motor.channel)

                # SERVOn_REVERSED must be 0 on a motor, and not because it is merely
                # the wrong place for direction. It inverts a range output, so
                # SRV_Channel::get_limit_pwm(MIN) returns servo_max -- and motors are
                # driven to MIN when disarmed. A reversed motor output idles at full
                # throttle. Direction belongs in SERVO_BLH_RVMASK.
                self.link.set_param(f"SERVO{motor.channel}_REVERSED", 0.0)

                # SERVO_BLH_RVMASK bit N is SERVO(N+1). A CAN ESC is left out: BLHeli
                # passthrough stops at the controller's own pins, and a bit here would
                # only make the direction look applied. board.check_output_map says
                # where it actually has to be set.
                if motor.reversed and not board.is_can(motor.channel):
                    reverse_mask |= 1 << (motor.channel - 1)

        # A gimbal pin that is currently a motor output is the boot-time takeover, seen
        # from the other side. Writing Disabled fixes the parameter immediately and the
        # pin regardless -- but a timer group's PWM/DShot mode is chosen during init, so
        # the pin stays DShot, and silent, until the board is restarted. Collected here
        # because it is the difference between "applied" and "will work".
        seized: List[str] = []

        for arm in self._cfg.live_arms():
            for name in ("outer", "inner"):
                axis = arm.gimbal.axis(name)
                if not channel_assigned(axis.channel):
                    continue
                held = self.link.get_param(f"SERVO{axis.channel}_FUNCTION")
                if held is not None and FUNC_MOTOR_FIRST <= int(held) <= FUNC_MOTOR_LAST:
                    seized.append(
                        f"S{axis.channel} ({arm.id} {name}, was "
                        f"Motor{int(held) - FUNC_MOTOR_FIRST + 1})"
                    )
                self.link.set_servo_function(axis.channel, FUNC_DISABLED)
                self.link.set_param(f"SERVO{axis.channel}_MIN", float(axis.min_us))
                self.link.set_param(f"SERVO{axis.channel}_MAX", float(axis.max_us))
                self.link.set_param(f"SERVO{axis.channel}_TRIM", float(axis.center_us))
                self.link.set_param(f"SERVO{axis.channel}_REVERSED", 0.0)

        stale = self._clear_stale_motor_functions()

        # Read before writing: SERVO_BLH_RVMASK is @RebootRequired, so a changed value
        # is reported by the board but not acted on. Without this comparison, editing
        # `reversed` in the config looks applied and changes nothing on the ESC.
        live_mask = self.link.get_param("SERVO_BLH_RVMASK")

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
        if stale:
            text += f". Cleared stale motor functions on {', '.join(stale)}"
        note = self.link.note(text)

        if live_mask is not None and int(live_mask) != reverse_mask:
            self.link.record_error(
                f"SERVO_BLH_RVMASK was 0x{int(live_mask):X}, config wants "
                f"0x{reverse_mask:X}. Motor direction only changes at boot, so reboot "
                "the flight controller before trusting which way anything spins."
            )
        if stale:
            self.link.record_error(
                f"Motor functions were still set on {', '.join(stale)} from an older "
                "channel map. They are cleared now, but a timer group's PWM/DShot mode "
                "is chosen at boot, so reboot before spinning motors."
            )

        can_note = self._assert_can_outputs()
        if can_note:
            text += f". {can_note}"
            note = self.link.note(text)

        telem_note = self._assert_esc_telemetry_serials()
        if telem_note:
            text += f". {telem_note}"
            note = self.link.note(text)

        # Every write above can succeed on a pin that is physically incapable of
        # carrying the signal asked of it. Nothing in the MAVLink exchange says so, so
        # check the map against the board itself and report it in the same breath.
        for problem in board.check_output_map(self._cfg):
            self.link.record_error(problem.text)

        if seized:
            self.link.record_error(
                "Gimbal channels were holding motor functions: "
                + ", ".join(seized)
                + ". They are Disabled now, but a timer group's output mode is fixed "
                "during init, so those pins stay DShot until the flight controller is "
                "rebooted. Reboot before concluding a servo is dead."
            )

        mismatch = self.check_frame()
        if mismatch:
            self.link.record_error(mismatch)
        directions = self.check_motor_directions()
        if directions:
            self.link.record_error(directions)
        return note

    def _assert_esc_telemetry_serials(self) -> str:
        """
        Open every configured UART as ESC telemetry.

        On Matek H743, RX3 is SERIAL4 (USART3) and RX4 is SERIAL6 (UART4). Stock
        ArduPilot used to consume only the first protocol-16 port; AP_BLHeli now
        reads every instance so both T-wire buses report. The protocol itself is
        applied at SerialManager init, so a change here needs a reboot.
        """
        serials = list(self._cfg.link.esc_telemetry_serials)
        if not serials:
            return ""

        changed: List[str] = []
        for index in serials:
            name = f"SERIAL{index}_PROTOCOL"
            live = self.link.get_param(name)
            self.link.set_param(name, SERIAL_PROTOCOL_ESC_TELEM)
            if live is not None and int(live) != int(SERIAL_PROTOCOL_ESC_TELEM):
                changed.append(name)

        names = ", ".join(f"SERIAL{n}" for n in serials)
        if changed:
            self.link.record_error(
                f"{', '.join(changed)} was not ESC Telemetry (16). "
                "Serial protocol is applied at boot, so reboot before expecting "
                "ESC telemetry on RX3/RX4."
            )
        return f"ESC telemetry on {names}"

    def _servo_bit(self, channel: int) -> int:
        return 1 << (int(channel) - 1)

    def _can_servo_mask(self, extra: Iterable[int] = ()) -> int:
        """
        Bitmask for CAN_D1_UC_SRV_BM: the gimbals on the node, plus any pin being probed.

        Nothing is reserved beyond that. A channel in this mask is sent as an actuator
        command, so a motor here would have its throttle shipped to the node as a servo
        position as well as an ESC command.
        """
        mask = 0
        for channel in board.can_servo_channels(self._cfg):
            mask |= self._servo_bit(channel)
        for channel in extra:
            if channel_assigned(int(channel)):
                mask |= self._servo_bit(int(channel))
        return mask

    def _can_esc_mask(self) -> int:
        """Bitmask for CAN_D1_UC_ESC_BM: the motors on the node."""
        mask = 0
        for channel in board.can_esc_channels(self._cfg):
            mask |= self._servo_bit(channel)
        return mask

    def _assert_can_outputs(self, extra_servos: Iterable[int] = ()) -> str:
        """
        Make the CAN-to-PWM node actually carry what the config puts on it.

        DO_SET_SERVO and the mixer only update the flight controller's idea of an
        output. Channels above S13 have no local timer, so nothing reaches the node
        unless DroneCAN is running and the channel is in the right bitmask -- servos in
        CAN_D1_UC_SRV_BM, ESCs in CAN_D1_UC_ESC_BM -- and both default to 0. Probing
        S14+ with the defaults is why those outputs looked dead.

        CAN_D1_UC_ESC_OF packs the ESC RawCommand so S14 is slot 0. That is what fixes
        the node side: its first output is Motor1, its second Motor2, and so on.

        SERVO_32_ENABLE is the same kind of silent gate for S17-S32: without it the
        parameters for those outputs do not exist and the output loop stops at 16.
        """
        mask = self._can_servo_mask(extra_servos)
        esc_mask = self._can_esc_mask()
        if mask == 0 and esc_mask == 0:
            return ""

        # SERVO17..SERVO32 do not exist as parameters until SERVO_32_ENABLE is set, and
        # the reserved node range reaches past S16 on its own, so this is normally true.
        # Both terms are kept because the mask is a reservation while the config is the
        # actual claim, and either one alone would be reached by a narrower mask.
        if (mask | esc_mask) >> (board.SERVO_32_FIRST - 1) or board.needs_servo_32(self._cfg):
            self.link.set_param("SERVO_32_ENABLE", 1.0)

        previous_driver = self.link.get_param("CAN_P1_DRIVER")
        previous_protocol = self.link.get_param("CAN_D1_PROTOCOL")
        self.link.set_param("CAN_P1_DRIVER", CAN_DRIVER_FIRST)
        self.link.set_param("CAN_D1_PROTOCOL", CAN_PROTOCOL_DRONECAN)

        live_mask = self.link.get_param("CAN_D1_UC_SRV_BM")
        live_esc_mask = self.link.get_param("CAN_D1_UC_ESC_BM")
        self.link.set_param("CAN_D1_UC_SRV_BM", float(mask))
        self.link.set_param("CAN_D1_UC_ESC_BM", float(esc_mask))
        self.link.set_param("CAN_D1_UC_ESC_OF", float(board.CAN_ESC_OFFSET))
        option = self.link.get_param("CAN_D1_UC_OPTION") or 0.0
        self.link.set_param("CAN_D1_UC_OPTION", float(int(option) | CAN_OPTION_SEND_PWM))
        self.link.set_param("CAN_D1_UC_SRV_RT", 50.0)

        # AP_DroneCAN::SRV_push_servos ANDs both masks with BRD_SAFETY_MASK while the
        # safety switch is on, so without these bits the node is silent on the bench.
        safety = self.link.get_param("BRD_SAFETY_MASK")
        if safety is not None:
            self.link.set_param("BRD_SAFETY_MASK", float(int(safety) | mask | esc_mask))

        driver_off = previous_driver is not None and int(previous_driver) != int(CAN_DRIVER_FIRST)
        protocol_off = (
            previous_protocol is not None
            and int(previous_protocol) != int(CAN_PROTOCOL_DRONECAN)
        )
        if driver_off or protocol_off:
            self.link.record_error(
                "CAN was not running DroneCAN "
                f"(CAN_P1_DRIVER={previous_driver}, CAN_D1_PROTOCOL={previous_protocol}). "
                "Those take effect at boot, so reboot before probing S14+ on the "
                "CAN-to-PWM adapter."
            )
        elif mask and live_mask is not None and int(live_mask) == 0:
            self.link.record_error(
                "CAN_D1_UC_SRV_BM was 0, so the CAN-to-PWM node was never sent a "
                "servo command. It is set now; probe those channels again."
            )
        elif esc_mask and live_esc_mask is not None and int(live_esc_mask) == 0:
            self.link.record_error(
                "CAN_D1_UC_ESC_BM was 0, so the CAN-to-PWM node was never sent an "
                "ESC command. It is set now. Those motors only take throttle while the "
                "vehicle is soft-armed, which on the bench means the motor test."
            )

        parts = []
        if mask:
            parts.append(f"CAN servo mask 0x{mask:X}")
        if esc_mask:
            parts.append(f"CAN ESC mask 0x{esc_mask:X} (offset {board.CAN_ESC_OFFSET})")
        return ", ".join(parts)

    def _clear_stale_motor_functions(self) -> List[str]:
        """
        Disable motor functions on board channels the config no longer claims.

        Moving a motor to another output writes the new channel but leaves the old one
        holding its motor function, so two outputs answer to one mixer slot and the
        abandoned pin keeps driving an ESC. Nothing reports that; the symptom is a
        propeller that spins when a different one was asked for.

        Only channels the config does not claim at all are touched, and only when they
        currently hold a motor function. A channel whose function cannot be read is left
        alone, because an unanswered read is not evidence that it is stale.
        """
        claimed = set()
        for arm in self._cfg.arms:
            for name in ("outer", "inner"):
                channel = arm.gimbal.axis(name).channel
                if channel_assigned(channel):
                    claimed.add(channel)
            for motor in arm.motors.values():
                if channel_assigned(motor.channel):
                    claimed.add(motor.channel)

        cleared = []
        for channel in range(1, CAN_SERVO_LAST + 1):
            if channel in claimed:
                continue
            function = self.link.get_param(f"SERVO{channel}_FUNCTION")
            if function is None or not FUNC_MOTOR_FIRST <= int(function) <= FUNC_MOTOR_LAST:
                continue
            self.link.set_servo_function(channel, FUNC_DISABLED)
            cleared.append(f"S{channel} (was Motor{int(function) - FUNC_MOTOR_FIRST + 1})")
        return cleared

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

    def check_motor_directions(self) -> Optional[str]:
        """
        Report motors recorded as turning the way the frame does not want.

        The required direction is not a preference. ArduPilot's mixer table states a yaw
        factor per motor number, which fixes the propeller's rotation, and for this frame
        that works out to every bottom motor CCW and every top motor CW seen from above.
        A motor turning the other way subtracts from yaw authority instead of adding to
        it, and its coaxial partner no longer cancels its torque.

        This compares the *recorded* direction, so it is only as good as the last bench
        observation -- ``Vector/tools/motor-direction.py`` is what establishes it. The
        fix for a disagreement is to flip that motor's ``reversed`` flag and reboot.
        """
        frame = (self._cfg.raw.get("vehicle") or {}).get("frame") or {}
        frame_class, frame_type = frame.get("frame_class"), frame.get("frame_type")
        if frame_class is None or frame_type is None:
            return None

        wrong = []
        for arm in self._cfg.live_arms():
            for name, motor in arm.motors.items():
                if motor.function is None or not channel_assigned(motor.channel):
                    continue
                slot = frames.slot_for_function(frame_class, frame_type, motor.function)
                if slot is None or slot.spin == motor.spin:
                    continue
                wrong.append(
                    f"{arm.id}.{name} (S{motor.channel}, Motor{slot.number}) is "
                    f"recorded {motor.spin}, frame wants {slot.spin}"
                )
        if not wrong:
            return None
        return (
            "; ".join(wrong)
            + ". Flip `reversed` on those motors and reboot, or re-run "
            "Vector/tools/motor-direction.py if the recorded direction is stale."
        )

    # ------------------------------------------------------------------
    # motors
    # ------------------------------------------------------------------
    def _confirm_servo_functions(self, wanted: Mapping[int, float]) -> List[int]:
        """
        Read back SERVOn_FUNCTION and return the channels that did not take.

        PARAM_SET is fire-and-forget and the link caches what it sent, so a dropped
        write looks exactly like a successful one. On a gimbal that costs a servo that
        does not move. On a motor it costs either a propeller that stays silent while
        its neighbours spin -- the symptom this whole path exists to remove -- or, on
        the way back, an output left Disabled and a motor that will not fly.
        """
        wrong = []
        for channel, want in wanted.items():
            got = self.link.get_param(f"SERVO{channel}_FUNCTION")
            if got is None or int(got) != int(want):
                wrong.append(channel)
        return wrong

    def _motor_throttle_us(self, percent: float) -> int:
        """
        Turn a throttle percentage into the pulse width ArduPilot would have used.

        MOT_PWM_MIN/MAX are exactly what ``AP_MotorsMulticopter::get_pwm_output_min``
        and ``..._max`` return, and the motor test scales between them, so reading
        them here keeps a given percentage meaning what it meant when this went
        through DO_MOTOR_TEST. A board that does not answer falls back to the
        1000-2000 defaults, which is also what DShot maps onto its throttle range.
        """
        low = self.link.get_param("MOT_PWM_MIN") or MOTOR_PWM_MIN_DEFAULT
        high = self.link.get_param("MOT_PWM_MAX") or MOTOR_PWM_MAX_DEFAULT
        if high <= low:
            low, high = MOTOR_PWM_MIN_DEFAULT, MOTOR_PWM_MAX_DEFAULT
        return int(round(low + (high - low) * float(percent) / 100.0))

    def _configured_motors(self) -> List[MotorTarget]:
        """Every motor in the config that has a real channel and a mixer function."""
        found: List[MotorTarget] = []
        for arm in self._cfg.arms:
            for role, motor in arm.motors.items():
                if not channel_assigned(motor.channel) or motor.function is None:
                    continue
                found.append(MotorTarget(arm.id, role, motor.channel, motor.function))
        return found

    def _disarm_for_bench(self) -> None:
        """Drop a leftover force-arm / RC override from an earlier mixer attempt."""
        try:
            self.link.force_disarm()
        except Exception:
            pass
        try:
            self.link.clear_rc_override()
        except Exception:
            pass
        try:
            self.link.wait_armed(False, timeout=2.0)
        except Exception:
            pass

    def spin_motors(
        self,
        arm_ids: Optional[Sequence[str]],
        which: Sequence[str],
        percent: float,
        seconds: float,
    ) -> str:
        """
        Spin every selected motor at once, at one throttle, for one duration.

        These ESCs are DShot and DroneCAN. DO_SET_SERVO cannot turn them, and
        force-arming Stabilize does not either -- the mixer sees a bench with
        MOT_SPIN_ARM at 0 and no real radio. DO_MOTOR_TEST is the path that
        already spun them: it soft-arms, suppresses failsafes in RAM, and
        writes each mixer slot.

        ``output_test_seq`` only updates the requested slot. It does not zero
        the others, so each selected sequence is started and given a main-loop
        cycle to latch, then the next is started without a zero in between.
        A USB burst with no gap is why a click used to spin a random subset.
        """
        self.link.require()
        self._disarm_for_bench()
        if self.link.telemetry().armed:
            raise ValueError(
                "the vehicle is armed, so the mixer owns the motor outputs -- "
                "disarm before spinning motors from here"
            )

        arms = self.resolve_arms(arm_ids)
        roles = [role for role in which if role in ("top", "bottom")]
        if not roles:
            raise ValueError("select at least one of 'top' or 'bottom'")

        limits = self._cfg.bench_limits
        percent = max(0.0, min(limits.motor_percent, float(percent)))
        seconds = max(0.1, min(limits.motor_seconds, float(seconds)))

        targets: List[MotorTarget] = []
        missing: List[str] = []
        disabled: List[str] = []
        no_sequence: List[str] = []
        sequences: List[int] = []
        for arm in arms:
            for role in roles:
                motor = arm.motors.get(role)
                if motor is None:
                    continue
                if not channel_assigned(motor.channel):
                    disabled.append(f"{arm.id}.{role}")
                elif motor.function is None:
                    missing.append(f"{arm.id}.{role}")
                elif motor.test_sequence is None:
                    no_sequence.append(f"{arm.id}.{role}")
                else:
                    targets.append(MotorTarget(arm.id, role, motor.channel, motor.function))
                    sequences.append(motor.test_sequence)

        if disabled:
            raise ValueError(
                "channel is 0 (disabled) for " + ", ".join(disabled) +
                " - set a SERVO1..SERVO32 channel on the Setup page first"
            )
        if missing:
            raise ValueError(
                "no motor function configured for " + ", ".join(missing) +
                " - set it on the Setup page before spinning these motors"
            )
        if no_sequence:
            raise ValueError(
                "no test_sequence for " + ", ".join(no_sequence) +
                " - DShot and CAN motors spin through the motor test"
            )
        if not targets:
            raise ValueError("no matching motors on the selected arms")

        self._halt_spin()
        budget = (
            MOTOR_TEST_ARM_S
            + MOTOR_TEST_LATCH_S * len(sequences) * 2
            + seconds
        )
        with self._lock:
            self._spin_stop.clear()
            self._motor_test_until = time.time() + budget
            self._spin_thread = threading.Thread(
                target=self._spin_motor_test_burst,
                args=(tuple(sequences), percent, seconds),
                name="vector-motor-spin",
                daemon=True,
            )
            self._spin_thread.start()

        listing = ", ".join(f"{target.arm_id}.{target.role}" for target in targets)
        return self.link.note(
            f"Spinning {listing} together at {percent:.1f}% for {seconds:.1f}s"
        )

    def _spin_motor_test_burst(
        self,
        sequences: Tuple[int, ...],
        percent: float,
        seconds: float,
    ) -> None:
        """
        Latch every selected slot, then hold, then stop once.

        The first command is what soft-arms and turns interlock on. Commands
        sent before that write ``output_min`` to every motor. Each later
        command is held long enough for the loop to ``rc_write`` that slot
        so its pulse survives when the sequence moves on. The set is sent
        twice so one dropped COMMAND_LONG does not leave a hole.
        """
        try:
            self.link.forget_servo_functions()
            for target in self._configured_motors():
                try:
                    self.link.set_servo_function(target.channel, float(target.function))
                except Exception:
                    pass
            if self._spin_stop.wait(0.05):
                return
            n = len(sequences)
            budget = MOTOR_TEST_ARM_S + MOTOR_TEST_LATCH_S * n * 2 + seconds
            for index, sequence in enumerate(list(sequences) + list(sequences)):
                if self._spin_stop.is_set():
                    return
                self.link.motor_test(sequence, percent, budget)
                pause = MOTOR_TEST_ARM_S if index == 0 else MOTOR_TEST_LATCH_S
                if self._spin_stop.wait(pause):
                    return
            with self._lock:
                self._motor_test_until = time.time() + seconds
            self._spin_stop.wait(seconds)
        except Exception as exc:
            self.link.record_error(f"Motor spin aborted: {exc}")
        finally:
            if sequences:
                try:
                    self.link.motor_test(sequences[0], 0.0, 0.0)
                except Exception:
                    pass
            self._restore_motor_functions(self._configured_motors())
            self._disarm_for_bench()
            with self._lock:
                if self._spin_thread is threading.current_thread():
                    self._spin_thread = None
                    self._motor_test_until = 0.0

    def _spin_worker(
        self,
        targets: Tuple["MotorTarget", ...],
        pwm: int,
        seconds: float,
        can_sequences: Tuple[Tuple["MotorTarget", int], ...] = (),
        percent: float = 0.0,
    ) -> None:
        """
        Hold the selected channels at one throttle, then stop them.

        The throttle is resent while the spin runs even though DO_SET_SERVO latches on
        the vehicle. One dropped command would otherwise leave a single motor of the
        set sitting at zero, which looks exactly like the bug this replaced.

        Motors on the CAN node follow afterwards through DO_MOTOR_TEST, one per test
        sequence. The motor test replaces rather than adds, so they cannot overlap.
        """
        channels = [target.channel for target in targets]
        try:
            if not channels:
                self._spin_can_motors(can_sequences, percent, seconds)
                return
            # Cache cleared first so each write actually goes out, then confirmed
            # before any throttle does: DO_SET_SERVO is refused on a channel that still
            # has a function, and refusing quietly is how one motor of a set ends up
            # not spinning while the rest do.
            self.link.forget_servo_functions()
            for channel in channels:
                self.link.set_servo_function(channel, FUNC_DISABLED)
            stuck = self._confirm_servo_functions({channel: FUNC_DISABLED for channel in channels})
            if stuck:
                raise RuntimeError(
                    "these outputs would not go Disabled, so they cannot be driven: "
                    + ", ".join(f"S{channel}" for channel in stuck)
                )

            # Timed from here rather than from the request, so the read-backs above do
            # not eat into the spin the operator asked for.
            deadline = time.time() + seconds
            with self._lock:
                # The CAN motors still to come are part of this spin, so the active
                # window covers them too; otherwise a caller waiting on it would stop
                # the spin between the onboard set and the first motor test.
                self._motor_test_until = deadline + seconds * len(can_sequences)
            while not self._spin_stop.is_set() and time.time() < deadline:
                for channel in channels:
                    self.link.set_servo_pwm_fast(channel, pwm)
                self._spin_stop.wait(1.0 / SPIN_REFRESH_HZ)
            self._release_motors(targets)
            targets = ()
            self._spin_can_motors(can_sequences, percent, seconds)
        except Exception as exc:
            self.link.record_error(f"Motor spin aborted: {exc}")
        finally:
            self._release_motors(targets)
            with self._lock:
                # Only if a later spin has not already taken over, so restarting a
                # spin does not report itself as finished the moment it begins.
                if self._spin_thread is threading.current_thread():
                    self._spin_thread = None
                    self._motor_test_until = 0.0

    def _spin_can_motors(
        self,
        can_sequences: Tuple[Tuple["MotorTarget", int], ...],
        percent: float,
        seconds: float,
    ) -> None:
        """Run each CAN motor through the motor test in turn, stopping on the shared flag."""
        for index, (target, sequence) in enumerate(can_sequences):
            if self._spin_stop.is_set():
                break
            self.link.motor_test(sequence, percent, seconds)
            with self._lock:
                self._motor_test_until = time.time() + seconds * (len(can_sequences) - index)
            self._spin_stop.wait(seconds)
            # Zero the test rather than trust its own timeout, for the same reason the
            # direct path resends its stop: the write that stops a propeller is the one
            # that must not be lost.
            self.link.motor_test(sequence, 0.0, 0.0)

    def _restore_motor_functions(self, targets: Iterable["MotorTarget"]) -> None:
        """
        Put SERVOn_FUNCTION back on every channel we touched.

        PARAM_SET is saved, so a motor left Disabled is a motor that will not fly
        until the mapping is applied again. Every step is attempted even if an
        earlier one throws, since a link that is failing is exactly when giving
        up is worst.
        """
        wanted = {target.channel: float(target.function) for target in targets}
        if not wanted:
            return
        for attempt in range(2):
            self.link.forget_servo_functions()
            for channel, function in wanted.items():
                try:
                    self.link.set_servo_function(channel, function)
                except Exception:
                    pass
            try:
                missed = self._confirm_servo_functions(wanted)
            except Exception:
                missed = list(wanted)
            if not missed:
                return
            wanted = {channel: wanted[channel] for channel in missed}
            if attempt == 0:
                continue
            self.link.record_error(
                "Could not restore the motor function on "
                + ", ".join(f"S{channel}" for channel in missed)
                + ". Those outputs are still Disabled and will not fly. Re-apply the "
                "output mapping from the Setup page."
            )

    def _release_motors(self, targets: Iterable["MotorTarget"]) -> None:
        """
        Stop the motors, then hand the channels back to the mixer.

        Zero throttle goes out first and more than once, because it is the write that
        actually stops a propeller and it is the one that must not be lost. Restoring
        the motor function is the second line of defence: a channel the mixer owns is
        driven to its minimum while disarmed, so a stop command that never arrived is
        covered by the restore.
        """
        targets = list(targets)
        if not targets:
            return
        for _ in range(3):
            for target in targets:
                try:
                    self.link.set_servo_pwm_fast(target.channel, MOTOR_STOP_US)
                except Exception:
                    pass
            time.sleep(0.02)
        self._restore_motor_functions(targets)

    def _halt_spin(self, timeout: float = 8.0) -> bool:
        """Stop any running spin and wait for its worker to finish releasing the outputs."""
        with self._lock:
            thread = self._spin_thread
            self._spin_thread = None
        self._spin_stop.set()
        if thread is None or not thread.is_alive():
            return False
        thread.join(timeout=timeout)
        return True

    def stop_motors(self) -> str:
        """
        Cancel any running spin and make sure nothing is left driving a motor.

        The mixer path disarms in the worker; this repeats the disarm and clears
        the RC override in case the worker never started. It also zeroes
        ArduPilot's own motor test on every configured sequence, because this
        is the button reached for when something is spinning and the operator
        does not care which mechanism started it -- pin probe and any other
        GCS still use DO_MOTOR_TEST.
        """
        self.link.require()
        stopped = self._halt_spin()
        try:
            self.link.force_disarm()
        except Exception:
            pass
        try:
            self.link.clear_rc_override()
        except Exception:
            pass

        sequences = {
            motor.test_sequence
            for arm in self._cfg.arms
            for motor in arm.motors.values()
            if motor.test_sequence is not None
        }
        for sequence in sorted(sequences):
            self.link.motor_test(sequence, 0.0, 0.0)
        self._restore_motor_functions(self._configured_motors())

        with self._lock:
            self._motor_test_until = 0.0
        return self.link.note(
            "Motor spin stopped" if stopped else "Motors stopped; none were spinning"
        )

    @property
    def motor_test_active(self) -> bool:
        with self._lock:
            return time.time() < self._motor_test_until

    # ------------------------------------------------------------------
    # mapping debug
    # ------------------------------------------------------------------
    def probe_output(self, channel: int, kind: str, hold_s: Optional[float] = None) -> str:
        """
        Drive one SERVO pin so the operator can see what is actually wired to it.

        This is deliberately not an arm command. Mapping bugs show up as "I asked
        for north top and something else moved", and the only way to settle that is
        to name the pin. ``kind`` is ``motor`` (spin for one second at the bench
        throttle cap) or ``servo`` (deflect, then return to centre). Channel 0 is
        refused: that value means unused, and there is no SERVO0.
        """
        self.link.require()
        channel = int(channel)
        if not channel_assigned(channel) or channel > CHANNEL_MAX:
            raise ValueError(
                f"channel {channel} is not a SERVO output; use 1..{CHANNEL_MAX} "
                "(0 disables a pin in the config and is not a real output)"
            )
        kind = str(kind).strip().lower()
        if kind == "servo" and board.is_can(channel):
            self._assert_can_outputs(extra_servos=[channel])
        elif channel >= board.SERVO_32_FIRST:
            self.link.set_param("SERVO_32_ENABLE", 1.0)
        if kind == "motor":
            return self._probe_motor(channel, 1.0 if hold_s is None else float(hold_s))
        if kind == "servo":
            return self._probe_servo(channel, 0.6 if hold_s is None else float(hold_s))
        raise ValueError("kind must be 'motor' or 'servo'")

    def _motor_test_for_pin(self, channel: int) -> Tuple[int, str]:
        """
        The motor-test sequence that actually drives this SERVO pin.

        DO_MOTOR_TEST addresses a mixer slot, not a pad. The slot that lands on
        this pad is whatever SERVOn_FUNCTION the board currently has, not whoever
        the config file assigned to that channel number after a remap.
        """
        raw_frame = (self._cfg.raw.get("vehicle") or {}).get("frame") or {}
        board_fn = self.link.get_param(f"SERVO{channel}_FUNCTION")
        if board_fn is not None and FUNC_MOTOR_FIRST <= int(board_fn) <= FUNC_MOTOR_LAST:
            slot = None
            if raw_frame.get("frame_class") is not None and raw_frame.get("frame_type") is not None:
                slot = frames.slot_for_function(
                    raw_frame.get("frame_class"), raw_frame.get("frame_type"), int(board_fn)
                )
            if slot is not None:
                found = None
                for arm in self._cfg.arms:
                    for name, motor in arm.motors.items():
                        if motor.function == slot.function:
                            found = f"{arm.id} {name}"
                            break
                who = found or f"Motor{slot.number}"
                return slot.test_sequence, f"{who} on the board"
        found = self._cfg.arm_for_motor_channel(channel)
        if found is not None:
            motor = found[0].motors[found[1]]
            if motor.test_sequence is not None:
                return (
                    motor.test_sequence,
                    f"{found[0].id} {found[1]} in the config — apply mapping first",
                )
        raise ValueError(
            f"S{channel} has no motor function on the board and no test_sequence "
            "in the config. Apply output mapping, then probe again."
        )

    def _probe_motor(self, channel: int, seconds: float) -> str:
        if self.link.telemetry().armed:
            raise ValueError(
                "the vehicle is armed, so the mixer owns the motor outputs -- "
                "disarm before probing a motor pin"
            )
        limits = self._cfg.bench_limits
        percent = limits.motor_percent
        seconds = max(0.1, min(limits.motor_seconds, float(seconds)))
        if board.uses_motor_test(channel, self._cfg.motor_channels()):
            # Drive the function that is actually on this pin. Following the config's
            # owner of S{n} is what made remapping look like the wires moved: the
            # click then ran a different test sequence, still on the old pad.
            sequence, who = self._motor_test_for_pin(channel)
            self._halt_spin()
            self.link.motor_test(sequence, percent, seconds)
            with self._lock:
                self._motor_test_until = time.time() + seconds
            extra = ""
            if board.is_can(channel):
                extra = (
                    f". Node OUT{board.can_output(channel)}_FUNCTION must be "
                    f"{board.can_esc_function(channel)}"
                )
            return self.link.note(
                f"Probing S{channel} runs motor test sequence {sequence} "
                f"({who}) at {percent:.1f}% for {seconds:.1f}s{extra}"
            )
        previous = self.link.get_param(f"SERVO{channel}_FUNCTION")
        restore = FUNC_DISABLED
        if previous is not None and FUNC_MOTOR_FIRST <= int(previous) <= FUNC_MOTOR_LAST:
            restore = int(previous)
        target = MotorTarget("probe", f"S{channel}", channel, restore)
        pwm = self._motor_throttle_us(percent)
        self._halt_spin()
        with self._lock:
            self._spin_stop.clear()
            self._motor_test_until = time.time() + seconds
            self._spin_thread = threading.Thread(
                target=self._spin_worker,
                args=((target,), pwm, seconds),
                name="vector-motor-spin",
                daemon=True,
            )
            self._spin_thread.start()
        owner = self._describe_channel(channel)
        return self.link.note(
            f"Probing S{channel} as a motor ({owner}) at {percent:.1f}% for {seconds:.1f}s"
        )

    def _probe_servo(self, channel: int, hold_s: float) -> str:
        """
        Deflect one servo, then put it back.

        The pulse window is opened to 500-2500 first: a board still at the 1000-2000
        default would clamp the excursion and the pin would look dead. Centre is the
        configured rest position when this pin belongs to a gimbal, otherwise 1500.
        """
        motor = self._cfg.arm_for_motor_channel(channel)
        if motor is not None:
            raise ValueError(
                f"S{channel} is {motor[0].id} {motor[1]} — a motor pin. A servo "
                f"sweep is ~1800 us, which an ESC treats as a hard burst, not 1%. "
                f"Use the motor test on that named motor, or set this probe to "
                f"'motor: spin 1 s'."
            )
        self._halt_spin()
        found = self._cfg.arm_for_servo_channel(channel)
        if found is not None:
            axis = found[0].gimbal.axis(found[1])
            center = axis.center_us
            excursion = axis.clamp_us(axis.center_us + int(round(30.0 * axis.us_per_deg)))
            lo, hi = float(axis.min_us), float(axis.max_us)
        else:
            center = 1500
            excursion = 1800
            lo, hi = 500.0, 2500.0
        if excursion == center:
            excursion = min(center + 300, int(hi))

        self.link.set_servo_function(channel, FUNC_DISABLED)
        self.link.set_param(f"SERVO{channel}_MIN", lo)
        self.link.set_param(f"SERVO{channel}_MAX", hi)
        self.link.set_servo_pwm(channel, excursion)
        time.sleep(max(0.0, float(hold_s)))
        self.link.set_servo_pwm_fast(channel, center)

        owner = self._describe_channel(channel)
        note = self.link.note(
            f"Probed S{channel} as a servo ({owner}, {board.describe(channel)}): "
            f"{excursion} us then {center} us"
        )

        # A probe that does nothing is the whole reason this command exists, so say up
        # front what would stop this particular pin from answering. On the expander that
        # is the node's own function mapping, which the flight controller cannot set.
        if board.is_can(channel):
            output = board.can_output(channel)
            self.link.record_error(
                f"S{channel} is CAN-to-PWM output {output}. If it did not move, check "
                f"the node's OUT{output}_FUNCTION is {board.can_function(channel)} "
                f"(50 + {channel}); the flight controller cannot set that for it."
                + (
                    " Note the config has an ESC on this output, whose node function "
                    f"is {board.can_esc_function(channel)} instead."
                    if self._cfg.arm_for_motor_channel(channel) else ""
                )
            )
        else:
            group = board.group_for(channel)
            motors = self._cfg.motor_channels()
            clash = [c for c in (group.channels if group else ()) if c in motors]
            if clash:
                self.link.record_error(
                    f"S{channel} shares {group.name} with "
                    + ", ".join(f"S{c}" for c in clash)
                    + ", which are DShot motor outputs. The group cannot mix modes, so "
                    "this pin emits no servo pulse however it is commanded."
                )
        return note

    def _describe_channel(self, channel: int) -> str:
        servo = self._cfg.arm_for_servo_channel(channel)
        if servo is not None:
            return f"{servo[0].id} {servo[1]} servo"
        motor = self._cfg.arm_for_motor_channel(channel)
        if motor is not None:
            return f"{motor[0].id} {motor[1]} motor"
        return "unclaimed"

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        # Before the arbiter, so the spin worker can still reach the link to stop the
        # motors and hand their channels back.
        self._halt_spin()
        self.outputs.stop()
        self.link.disconnect()

    def describe_arms(self) -> List[Dict[str, Any]]:
        """Derived geometry per arm, for the Setup page."""
        return [
            {"id": arm.id, "workspace": kin.workspace_payload(arm)}
            for arm in self._cfg.arms
        ]
