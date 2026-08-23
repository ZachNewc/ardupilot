# Hardware

## Bill of materials

| Part | Detail | Count |
|---|---|---|
| Flight controller | Matek H743-Wing V3 (`MatekH743` hwdef, STM32H743) | 1 |
| Motor | 2806.5 brushless, 7-inch props | 8 |
| ESC | Velox 70A 4-in-1 | 2 |
| Servo | ±90° travel, 500–2500 µs | 8 |
| Servo power | 20 A SBEC | 1 |
| Battery | 6S2P li-ion, 10 000 mAh | 1 |
| Expansion | CAN-to-PWM board | 1 |
| Frame | Carbon fibre with 3D-printed gimbal parts | 1 |

Rotor-to-rotor diagonal is 810 mm, so each hub sits 405 mm from the centre.

## Geometry

Plus layout. The flight controller's nose points along the North arm, so ArduPilot's
body frame lines up with the arms exactly:

```text
                 North  (body +X, nose)
                   |
                   |
   West ---------- + ---------- East  (body +Y)
                   |
                   |
                 South
```

Body frame is ArduPilot's convention throughout this project: **X forward, Y right,
Z down**. A centred gimbal points its thrust along body −Z, straight up.

Arm azimuth is measured from body +X toward body +Y, so North is 0°, East 90°, South
180°, West 270°. Each arm's `mount_yaw_deg` equals its azimuth, meaning every gimbal
is bolted on rotated to match the arm it sits on. That is what lets one set of
kinematics serve all four arms.

## Each arm

```text
       top motor (CW, reversed)          inner axis (pitch)
              ▲                          carried by the outer ring,
              │                          driven from the airframe
        ┌─────┴─────┐
        │  gimbal   │ ←── outer axis (roll)
        └─────┬─────┘      mounted to the arm
              │
              ▼
      bottom motor (CCW)
```

**Motors.** One up, one down, counter-rotating. Reaction torques cancel, so an arm
produces thrust with almost no torque about its own axis. The top motor is driven
reversed so both produce upward thrust.

**Gimbal.** Two axes in series. The **outer** axis is mounted to the arm and carries
the inner one. The **inner** axis carries the motors. Both are driven through a 2:1
reduction, so 45° of servo gives 22.5° of gimbal.

The naming is worth pinning down because it is the source of most confusion:

| Axis | Position | Servo travel needed | Why |
|---|---|---|---|
| `outer` | Mounted to the arm | ±45° | 2:1 gear × 22.5° tilt |
| `inner` | Carried by the outer ring | ±90° | 2:1 gear × 22.5° tilt, **plus** the coupling term |

The inner axis needs twice the travel because its servo is mounted to the airframe,
not to the outer ring. Rotating the outer ring drags the inner axis along with it, and
the inner command has to add that back. [Kinematics](03-kinematics.md) derives this.

## Output channels

### The timer constraint

The H743 exposes 13 PWM outputs, and they are grouped by hardware timer:

| Timer group | Outputs | Notes |
|---|---|---|
| TIM8 | S1, S2 | |
| TIM5 | S3, S4, S5, S6 | |
| TIM4 | S7, S8, S9, S10 | |
| TIM15 | S11, S12 | |
| TIM1 | S13 | Reserved for WS2812 LED on this board |

**Every output in a timer group must use the same output mode.** A group cannot mix
DShot for an ESC with standard PWM for a servo. This is the single most important
constraint on how outputs get allocated, and it is a property of the microcontroller,
not of ArduPilot.

Twelve usable outputs against a requirement of 8 ESC signals plus 8 servos is why the
CAN-to-PWM board exists on this vehicle.

### As built today (North and South)

| Function | Output | Group | Mode |
|---|---|---|---|
| North inner servo | S1 | TIM8 | PWM |
| North outer servo | S2 | TIM8 | PWM |
| South top motor | S5 | TIM5 | DShot600, reversed |
| South bottom motor | S6 | TIM5 | DShot600 |
| North bottom motor | S7 | TIM4 | DShot600 |
| North top motor | S8 | TIM4 | DShot600, reversed |
| South outer servo | S11 | TIM15 | PWM |
| South inner servo | S12 | TIM15 | PWM |

Every group is single-mode: TIM8 and TIM15 carry only servos, TIM4 and TIM5 carry only
motors. S3 and S4 sit unused on the motor side of TIM5, which is harmless.

South's servos were briefly on S3/S4, which put PWM servos and DShot motors on TIM5 at
once. Moving them to TIM15 was the cheapest fix: two signal wires, and nothing else in
the allocation had to change.

### Planned for East and West

| Arm | Outer | Inner | Bottom | Top |
|---|---|---|---|---|
| East | S14 | S15 | S9 | S10 |
| West | S18 | S19 | S4 | S3 |

East and West servos stay on the CAN-to-PWM board (channels 14 and up). East motors
take the unused half of TIM4 next to North. West motors take the free half of TIM5 next
to South, so all eight ESC signals end up on the two DShot groups and all eight servo
signals on PWM groups or CAN. No later arm forces a rewire of an earlier one.

### Current config channel map

Taken from `Vector/config/vector.json`. Channels 14 and up are CAN-to-PWM placeholders.

| Arm | Status | Outer | Inner | Bottom | Top |
|---|---|---|---|---|---|
| North | live | S2 | S1 | S7 | S8 |
| East | planned | S14 | S15 | S9 | S10 |
| South | live | S11 | S12 | S6 | S5 |
| West | planned | S18 | S19 | S4 | S3 |

`planned` means the dashboard displays the arm but refuses to command it. Only `live`
arms are ever written to.

## Power and wiring

**Servos** are powered from a 20 A SBEC, entirely separate from the flight controller's
rail. Only the signal wire runs to the flight controller. Grounds must be common — the
SBEC ground and the flight controller ground have to be bonded or the servo signal has
no reference and behaves erratically.

Eight servos slewing together is a large transient load. The SBEC is sized for it; the
flight controller's 5 V rail is not, which is why they are separate.

**ESCs** connect ground, four motor signals each, and one telemetry wire. Motor power
comes straight from the battery, not through the flight controller.

**Battery** is 6S2P li-ion: 22.2 V nominal, 25.2 V full, 10 Ah. Li-ion has a much
softer discharge curve than lithium polymer and far less peak current capability, so
the failure mode under a hard throttle transient is voltage sag rather than a clean
cutoff. Watch cell voltage, not pack percentage — the Telemetry page shows pack
voltage divided by six cells for this reason.

**ESC telemetry** goes to RX4. On this board `SERIAL_ORDER` is:

```text
OTG1  UART7  USART1  USART2  USART3  UART8  UART4  USART6  OTG2
  0      1      2       3       4      5      6      7      8
```

so UART4 is **SERIAL6**, and `SERIAL6_PROTOCOL` must be 16 (ESC Telemetry). Telemetry
is one-wire into RX4; TX4 is unused. The config records this as
`link.esc_telemetry_serial: 6` and the dashboard's Telemetry page names the parameter
directly when nothing is reporting.

## Servo signal characteristics

| Property | Value |
|---|---|
| Pulse range | 500–2500 µs |
| Travel | ±90° |
| Scale | 11.111 µs per degree |
| Centre | 1500 µs |

The scale follows from the other rows: 2000 µs of range over 180° of travel. The
config stores it explicitly as `us_per_deg` so a servo with a different response can be
described without changing code.

## What is not on the vehicle

No GPS, compass, rangefinder or airspeed sensor is currently fitted. The dashboard
reports GPS fix and satellite count because the messages exist, but they will read zero.
This matters for firmware work: any flight mode requiring position will not arm.
