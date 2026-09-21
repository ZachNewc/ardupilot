# Roadmap

## Build state

| Item | State |
|---|---|
| North arm | Built, wired |
| South arm | Built, wired. Servos on S9/S10 (TIM4 with East) |
| East / West arms | `live` in the config. South and West motors on the CAN-to-PWM node, S14–S17 |
| Gimbal kinematics | Derived, implemented, tested in two languages |
| Dashboard | Complete as a bench tool |
| Documentation | This set |
| Flight firmware | Mixer not started. `AP_BLHeli` now reads every ESC-telemetry UART |
| SITL model | Not started |

## Verified on hardware

- ESC telemetry on RX4 and RX6, `SERIAL6_PROTOCOL` and `SERIAL7_PROTOCOL` 16
- DShot600 with a reversed top motor via BLHeli passthrough
- `MAV_CMD_DO_MOTOR_TEST` sequences 1 and 3 under QUAD / X (North, on the previous S3/S4 wiring)

That verification no longer transfers. The frame moved to OCTAQUAD / PLUS, which renumbers
North's pair to test orders 1 and 2, and the motors moved to S7/S8.

## Not yet verified

Listed explicitly so nothing here gets mistaken for measured fact:

- The 2:1 gear ratio and ±22.5° limit as *built*, versus as designed
- Servo lag, which sets `lead_time_s`. Currently a placeholder of 60 ms
- Axis `sign` values for East and West
- Every `test_sequence` and `function` under OCTAQUAD / PLUS. The numbers are taken from
  `AP_MotorsMatrix::setup_octaquad_matrix()`, so they follow from the frame, but which
  physical motor answers each one is still unconfirmed on this airframe
- Whether the outer axis reaches ±22.5° without binding
- Whether the coaxial pair's torques actually cancel closely enough to ignore
- Every number relating to the two unbuilt arms

The [bench testing](08-bench-testing.md) procedures establish all of these.

## Open decisions

### Output allocation

Decided. All eight servos are on the controller — TIM5 (S3–S6) and TIM4 (S7–S10) —
and the two PWM-only pairs carry North's motors (S1/S2, TIM8) and East's (S11/S12,
TIM15). South and West ESCs sit behind the CAN-to-PWM node on S14–S17. Recorded in
[Hardware](02-hardware.md).

| Option | Trade-off |
|---|---|
| Servos onboard, four motors on CAN | **Chosen.** Every gimbal on a local timer. Two arms' ESCs need the node, only spin under the motor test, and cannot be reversed from the flight controller |
| Four servos on CAN, all motors onboard | Every ESC on DShot with reverse from the controller. Two gimbals respond on a different path from the others |

Recorded in [Hardware](02-hardware.md); the channel numbers are in `vector.json`. The
cost of the chosen layout is entirely on the CAN motors: direction is set at the ESC,
and the bench cannot spin them together with the rest.

### Yaw blend

Two yaw sources: differential thrust within coaxial pairs (immediate, limited authority,
disturbs thrust) and tangential gimbal tilt (more authority, disturbs nothing, limited by
servo bandwidth). The intent is to blend them, configurable via `VEC_YAW_MIX`. The ratio
needs measurement, not a guess.

### Attitude bias at speed

Level by default, with a configurable lean at higher speeds to reduce airframe drag.
Undecided: whether this is a fixed schedule against airspeed, a pilot input, or derived
from the velocity controller's demand. There is no airspeed sensor fitted, which
constrains the options.

### Lateral demand routing

Whether gimbal lean feeds forward from the attitude controller's lateral demand or sits
under position control only. Affects how the vehicle behaves in manually-flown modes.

## Planned work, in order

### 1. Re-establish the outputs, then prove both arms

The frame moved to OCTAQUAD / PLUS and East's motors sit on S11/S12, so every motor
number changed meaning and no earlier bench result carries over. Set `FRAME_CLASS` 4 and
`FRAME_TYPE` 0, reboot, then confirm with `Vector/tools/fc-report.py` that the board and
`vector.json` agree before touching a motor.

Then run the bench sequence on both arms, props off: centres, signs, envelope, and which
physical motor each test order actually spins.

East (90° mount yaw) is still the first real test of the mount-yaw transform on a
third arm. South and West motors are the first thing that has to work through the CAN
node rather than a local timer.

### 2. SITL frame model

A `SIM_Vector` backend under `libraries/SITL/` modelling four gimballed coaxial pairs.
This is the highest-value firmware work after the mixer, because it makes autotest
coverage possible — and without it, every mixer change has to be validated on hardware.

Needs: thrust and torque per motor, gimbal servo positions affecting thrust direction,
and enough rotor dynamics for the attitude loop to behave realistically.

### 3. Thrust-vector mixer

An `AP_Motors` backend deriving from `AP_MotorsMatrix`, so differential-thrust mixing is
inherited unchanged and only the servo outputs are added. Design in
[Firmware](05-firmware.md).

Approach:

1. Transliterate the kinematics from Python to C++
2. Validate against `tests/golden_kinematics.json` — the same file the TypeScript port
   uses
3. Add the `AP_MOTORS_VECTOR_ENABLED` guard and the build option
4. Unit-test the mixer in isolation
5. Autotest against the SITL model from step 2
6. **Human review of the safety-critical parts** — saturation, failsafe, servo failure,
   interaction with `AC_AttitudeControl`

### 4. Config to parameter generation

The gimbal geometry has to exist twice: in `vector.json` for the dashboard and in
ArduPilot parameters for the firmware. Keeping them in step by hand is a real hazard —
a stale `VEC_GEAR` produces a vehicle whose firmware and dashboard disagree about where
the thrust is pointing.

Mitigation: generate a `.param` file from `vector.json`. A small script in
`Vector/tools/`, plus a check that the running vehicle's parameters match the config,
surfaced on the dashboard.

### 5. 3D World

Currently a scaffold: a correct static model built from the config, no telemetry.

Next steps, roughly in order of value:

1. Drive attitude from live telemetry
2. Animate the gimbals from the actual servo positions
3. Draw thrust vectors with magnitude
4. A simple rigid-body simulation so the mixer can be exercised visually

Steps 1–3 are display work against data the dashboard already receives. Step 4 overlaps
the SITL model and should not duplicate it.

### 6. Flight test programme

Sequenced in [Safety](09-safety.md#first-flight-when-the-time-comes). The key constraint:
fly as a conventional coaxial quad with gimbals locked centred **first**. If it does not
fly that way, vectoring will hide the problem rather than solve it.

## Deliberately not planned

- **Autonomous mission capability.** No GPS or compass is fitted. Out of scope until the
  basics work.
- **Replacing ArduPilot's attitude controller.** It is correct for this frame's
  differential thrust. Vectoring adds channels; it does not replace attitude control.
- **Host-side flight control.** Structurally impossible over MAVLink at 25 Hz. See
  [Control](04-control.md).
- **Upstream contribution, for now.** A thrust-vector mixer might eventually interest
  ArduPilot, but not before it flies. Per `AGENTS.md`, no pull request should be opened on
  unproven work.

## Contributing to this project

If a change touches the ArduPilot tree rather than `Vector/`, the project's `AGENTS.md`
applies in full: one subsystem per commit, `Subsystem: description` messages, astyle
formatting, feature guards, and full `@Param` documentation for new parameters.

Everything under `Vector/` is outside the ArduPilot source tree on purpose, so this work
can be rebased on upstream without conflicts.

Python in `Vector/` is marked `AP_FLAKE8_CLEAN` and should stay that way:

```bash
python3 -m flake8 Vector --max-line-length 127
python3 -m unittest discover -s Vector/tests
cd Vector/dashboard/web && npm test && npm run typecheck
```
