# Vector

A thrust-vectoring quadrotor. Four arms, each carrying a coaxial counter-rotating
motor pair inside a two-axis gimbal, so thrust can be pointed independently of where
the airframe is facing.

## Read these in order

| # | Document | What it answers |
|---|---|---|
| 01 | [Overview](01-overview.md) | What the vehicle is, why vectoring, what it can and cannot do |
| 02 | [Hardware](02-hardware.md) | Every component, every wire, every output channel |
| 03 | [Kinematics](03-kinematics.md) | The full geometry: tilt to servo, tilt to thrust, and the limits |
| 04 | [Control](04-control.md) | Where each control loop lives and why |
| 05 | [Firmware](05-firmware.md) | The ArduPilot side: parameters, mixer plan, what is not written yet |
| 06 | [Dashboard](06-dashboard.md) | Running it, the pages, the architecture |
| 07 | [Configuration](07-configuration.md) | `vector.json` field by field, and how to add an arm |
| 08 | [Bench testing](08-bench-testing.md) | Procedures, in the order you should do them |
| 09 | [Safety](09-safety.md) | Hazards specific to this airframe. Read before powering anything |
| 10 | [Roadmap](10-roadmap.md) | Build state, open questions, what comes next |

## Current state

All four arms are `live` in the config. Every gimbal servo and the North and East
motors are on the flight controller's own outputs and answer with nothing but a USB
cable. South and West motors are ESCs behind the CAN-to-PWM node (S14–S17): they need
DroneCAN running, only turn under the motor test, and take their direction from the ESC
itself — see [Hardware](02-hardware.md) for what the node has to be told.

The gimbal kinematics are derived, implemented in Python and TypeScript, and covered
by a shared set of golden test vectors. The dashboard is complete as a bench tool.
**No flight-capable thrust-vector mixer exists yet** — see
[Firmware](05-firmware.md) for what has to be built and
[Control](04-control.md) for why the host-side loop cannot substitute for it.

## Layout

```text
Vector/
  start.sh                start the dashboard
  config/vector.json      the whole vehicle in one file
  dashboard/
    serve.py              entry point
    server/               Python: config, kinematics, MAVLink link, bench, protocol
    web/                  Vue 3 + Vite frontend
  docs/                   this documentation
  tests/                  unit and contract tests
  tools/
    fc-report.py          read-only: what the board believes, diffed against the config
    motor-direction.py    spin each motor, observe rotation, correct the config
    build-and-flash.sh    firmware
    dashboard.sh          the real launcher behind start.sh
```

Everything Vector-specific lives under `Vector/`. The surrounding tree stays close
to upstream ArduPilot. Firmware deltas: `AP_BLHeli` reads every ESC telemetry UART
so RX4 and RX6 both report, and Copter's motor test writes every started sequence
on one output cycle.

## Quick start

```bash
# Bench dashboard (builds the web app if stale, then serves it)
Vector/start.sh

# When something on the bench does not move. Read-only, safe to run any time
Vector/tools/fc-report.py

# Establish which way each propeller actually turns. Props off
Vector/tools/motor-direction.py

# Tests
python3 -m unittest discover -s Vector/tests
cd Vector/dashboard/web && npm test && npm run typecheck

# Firmware
Vector/tools/build-and-flash.sh --build-only
```

## A note on authorship

Large parts of this project, including this documentation, were written with AI
assistance. Anything describing hardware behaviour was checked against the source or
measured; anything not yet verified on hardware says so explicitly. See
[Roadmap](10-roadmap.md) for the list of unverified items.
