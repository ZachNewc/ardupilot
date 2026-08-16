# Overview

## The idea

A conventional multirotor has a fixed relationship between attitude and translation.
It can only push along its own thrust axis, so to move sideways it must first tilt,
and to stay level it must stop moving. Attitude and translation are the same control
problem, which is why photography rigs need a separate camera gimbal and why payloads
that must stay level need one too.

Vector separates them. Each of the four arms carries its motor pair in a two-axis
gimbal, so thrust can be aimed while the airframe stays where it is. The airframe
becomes a platform rather than a steering surface.

## What the vehicle is

Four arms in a plus layout at North, East, South and West. The flight controller's
nose points straight down the North arm, so body +X runs along that arm and there is
no 45-degree offset anywhere in the geometry.

Each arm carries:

- **Two 2806.5 motors on 7-inch props**, coaxial and counter-rotating, one facing up
  and one down. Their reaction torques cancel, so a single arm produces thrust with
  almost no net torque about its own axis.
- **A two-axis gimbal** driven by two servos through a 2:1 reduction. The gimbal tilts
  up to 22.5 degrees on each axis.

Eight motors, eight servos, four independently aimable thrust vectors.

## What vectoring buys

**Translation while level.** The obvious one. Tilt all four thrust vectors the same
way and the vehicle accelerates in that direction with the body attitude unchanged.

**Yaw without torque asymmetry.** A conventional quad yaws by unbalancing rotor drag
torque, which necessarily disturbs thrust. Vector can yaw two ways: differentially
within each coaxial pair (torque, cheap, limited authority) or by leaning all four
thrust vectors tangentially so their side forces cancel while their moments add.
The tangential pattern is exact — see
[`yaw_tilt_pattern()`](../dashboard/server/kinematics.py) and the test that proves the
forces cancel.

**Attitude and translation decoupled.** The vehicle can hold a commanded attitude —
level by default, or deliberately leaning for reduced drag at speed — independently
of where it is going.

## What vectoring does not buy

This is the most important paragraph in the documentation, because getting it wrong
leads directly to an airframe that cannot be stabilised.

**Vectoring provides almost no roll or pitch authority.** A rotor sits in the plane of
the centre of mass. Tilting its thrust produces a horizontal force at that rotor. A
horizontal force applied in the centre-of-mass plane has no lever arm about the roll
or pitch axes, so it produces essentially no roll or pitch moment.

Roll and pitch moments come from *differential thrust between opposite arms*, exactly
as on a conventional quad, and that remains ArduPilot's job. Vectoring adds a lateral
force channel and a yaw channel on top of the normal quad control problem; it does not
replace it.

Concretely: if the airframe is knocked 10 degrees off level, the gimbals can keep the
*thrust* pointing at world vertical, but they cannot bring the *airframe* back. Only
differential thrust does that.

## Control split

Three layers, each in the only place it can work:

| Layer | Where | Rate | Job |
|---|---|---|---|
| Attitude and rate | ArduPilot, flight controller | 400 Hz | Roll, pitch, yaw via differential thrust |
| Thrust vector mixing | ArduPilot, flight controller | 400 Hz | Lateral demand to eight servo positions |
| Bench visualisation | Dashboard, host over USB | 25 Hz | Demonstrate and tune the levelling law, verify signs |

The middle layer does not exist yet. [Firmware](05-firmware.md) covers the design;
[Control](04-control.md) covers why the host cannot stand in for it.

## Key numbers

| Quantity | Value | Source |
|---|---|---|
| Arms | 4, plus layout at N/E/S/W | config |
| Motors | 8, coaxial counter-rotating pairs | config |
| Props | 7 inch | build |
| Rotor diagonal | 0.81 m | measured |
| Arm length, hub to centre | 0.405 m | derived |
| Gimbal tilt limit | ±22.5° per axis | design |
| Gear ratio, servo to gimbal | 2:1 | design |
| Servo travel | ±90° over 500–2500 µs | datasheet |
| Battery | 6S2P li-ion, 10 Ah | build |
| Flight controller | Matek H743-Wing V3 | build |
| ESCs | 2 × Velox 70A 4-in-1 | build |

Full detail in [Hardware](02-hardware.md). The reachable tilt envelope is not a
circle, and the reason is worked through in [Kinematics](03-kinematics.md).
