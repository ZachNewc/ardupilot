# Roadmap

## Build state

| Item | State |
|---|---|
| North arm | Built, wired, bench-tested |
| East / South / West arms | Not built. Described in the config as `planned` |
| Gimbal kinematics | Derived, implemented, tested in two languages |
| Dashboard | Complete as a bench tool |
| Documentation | This set |
| Flight firmware | **Not started.** Tree is unmodified ArduPilot |
| SITL model | Not started |

## Verified on hardware

- North gimbal servos on S1/S2, motors on S3/S4
- ESC telemetry on RX4, `SERIAL6_PROTOCOL` 16
- DShot600 with the top motor reversed via BLHeli passthrough
- `MAV_CMD_DO_MOTOR_TEST` sequences 1 and 3 under QUAD / X

## Not yet verified

Listed explicitly so nothing here gets mistaken for measured fact:

- The 2:1 gear ratio and ±22.5° limit as *built*, versus as designed
- Servo lag, which sets `lead_time_s`. Currently a placeholder of 60 ms
- Axis `sign` values for the three unbuilt arms
- Whether the outer axis reaches ±22.5° without binding
- Whether the coaxial pair's torques actually cancel closely enough to ignore
- Every number relating to the three unbuilt arms

The [bench testing](08-bench-testing.md) procedures establish all of these.

## Open decisions

### Servo output allocation

Undecided, deliberately. The flight controller has 12 usable PWM outputs and the timer
grouping means a group cannot mix DShot and servo PWM. Motors take S3–S10 (two full
timer groups). That leaves S1, S2, S11, S12 for servos, against a requirement of eight.

| Option | Trade-off |
|---|---|
| All eight servos on CAN-to-PWM | Uniform timing across gimbals. Adds a CAN dependency to every gimbal |
| Four on the controller, four on CAN | Uses existing hardware. Two gimbals respond on a different path from the others |

Recorded in [Hardware](02-hardware.md). The only place the decision has to land is the
channel numbers in `vector.json`.

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

### 1. Build a second arm

Everything about the geometry generalises across arms in software already, but exactly
one arm has been built. A second arm — East, opposite in mount yaw — is the first real
test of the mount-yaw transform on hardware, and it is cheap compared to building all
three.

Change required: `status` from `planned` to `live` in the config. Nothing else.

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
