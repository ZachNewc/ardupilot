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

### The CAN-to-PWM adapter is not a pin remap

S14 and above do not exist as timer pins on the H743. `DO_SET_SERVO` on those channels
only updates a number inside the flight controller. That number becomes a pulse on the
expander if, and only if:

| Parameter | Needed value | Why |
|---|---|---|
| `CAN_P1_DRIVER` | 1 | Attaches DroneCAN to the CAN1 port. Reboot required |
| `CAN_D1_PROTOCOL` | 1 | DroneCAN. Reboot required |
| `CAN_D1_UC_SRV_BM` | bits for S14–S21 | **Defaults to 0.** With the default, the adapter is silent |
| `CAN_D1_UC_OPTION` bit 4 | set | Send raw pulse widths, not a scaled −1..1 |
| `SERVO_32_ENABLE` | 1 | Without it, S17–S32 do not exist as outputs |

The dashboard asserts all of these when output mapping is applied. If CAN was off, reboot
once before probing S14+. On the expander itself, each `OUTx_FUNCTION` must be
`50 + servo number` (51 for servo 1, 64 for servo 14) or that node output stays a
default motor slot and ignores actuator commands.

S11 and S12 are the only onboard pair that is PWM-only and not shared with DShot. They
will move a servo even when the CAN path is completely unconfigured, which is why they
were the only channels that answered during mapping debug.

### Which node output a channel is

The node's outputs are numbered from S14 up: **S14 is the node's first output, S15 its
second, S16 its third, S17 its fourth.** That correspondence is not automatic. It holds
only because each node output's own `OUTx_FUNCTION` names the thing that reaches it, and
that number differs by what is on the output:

| On the node output | Path from the flight controller | Node `OUTx_FUNCTION` |
|---|---|---|
| A servo | `ActuatorCommand`, gated by `CAN_D1_UC_SRV_BM` | `50 + channel` (64 for S14) |
| An ESC | ESC `RawCommand`, gated by `CAN_D1_UC_ESC_BM` | `Motor(k+1)` = `33 + k`, where k is the RawCommand slot |

The dashboard sets `CAN_D1_UC_ESC_OF = 13`, which packs the RawCommand so S14 is slot
0. So for the motors as built: **OUT1 = 33, OUT2 = 34, OUT3 = 35, OUT4 = 36.** Get that
wrong on the node and the output is silent in a way no flight-controller parameter
explains. The Setup page's Output map prints the number each output needs.

### Two things about motors on the node

**They only turn while the vehicle is soft-armed.** `AP_DroneCAN::SRV_send_esc` sends
zero to every ESC in the RawCommand unless `hal.util->get_soft_armed()` is true. The
dashboard's normal bench spin drives pins with `DO_SET_SERVO` while disarmed, which can
never move a CAN ESC. Only `DO_MOTOR_TEST` soft-arms while on the ground
(`ArduCopter/motor_test.cpp`), so South and West motors spin through the motor test —
one at a time, by `test_sequence`, after the onboard set has run together.

**Their direction cannot be set from the flight controller.** `SERVO_BLH_RVMASK` and
BLHeli passthrough stop at the H743's own pins. `reversed: true` on South and West upper
motors records the intent, and the dashboard says so every time it asserts the mapping;
the direction itself has to be set in that ESC's configuration or by swapping any two
of its three motor wires.

### The mixer claims any motor slot the config does not place

This is the other half of the same problem, and it is the one that actually bit.

`AP_Motors::add_motor_num` calls `SRV_Channels::set_aux_channel_default(function,
motor_num)`, which puts MotorN on SERVO(N) unless some channel already claims that
function. The catch is in that function's first test: it treats `SERVOn_FUNCTION = 0`
as unclaimed, because Disabled **is** `k_none`. Setting a pin to Disabled — exactly what
a gimbal channel needs for `DO_SET_SERVO` — does not reserve it. It offers it up.

So every motor slot left unplaced in `vector.json` lands on a low channel at the next
boot, turns it into a DShot output, and takes its whole timer group with it:

| Config says | What the firmware does at boot | What the operator sees |
|---|---|---|
| All 8 motors `channel: 0` | Motor1–Motor8 claim S1–S8 | TIM8, TIM5, TIM4 all go DShot |
| Gimbal servos on S5–S10 | Those pins are now DShot ESC outputs | Only S11 and S12 move a servo |

The second row is a real session: the servos on S11/S12 worked, a servo on S9 did
nothing, and every parameter read back exactly as written. The fix is not a parameter —
it is to give all eight motor slots an explicit channel, so the mixer has nothing left
to claim.

`Vector/dashboard/server/board.py` holds the timer table and checks a config against it.
The check runs when output mapping is asserted, shows on the Setup page's **Output map**
panel, prints in `Vector/tools/fc-report.py`, and is covered by
`Vector/tests/test_board.py`, so this cannot come back quietly.

### Output allocation

Two arms' motors on the flight controller's two PWM-only timer pairs, all eight servos
on the two four-pin groups, the other two arms' motors on the CAN node. Every timer
group is single-mode.

| Function | Output | Group | Mode |
|---|---|---|---|
| North upper motor | S1 | TIM8 | DShot600 |
| North lower motor | S2 | TIM8 | DShot600 |
| West inner servo | S3 | TIM5 | PWM |
| West outer servo | S4 | TIM5 | PWM |
| North inner servo | S5 | TIM5 | PWM |
| North outer servo | S6 | TIM5 | PWM |
| East inner servo | S7 | TIM4 | PWM |
| East outer servo | S8 | TIM4 | PWM |
| South inner servo | S9 | TIM4 | PWM |
| South outer servo | S10 | TIM4 | PWM |
| East lower motor | S11 | TIM15 | DShot600 |
| East upper motor | S12 | TIM15 | DShot600 |
| *(unused / WS2812 LED)* | S13 | TIM1 | — |
| South upper motor | S14 | CAN out 1 | ESC RawCommand |
| South lower motor | S15 | CAN out 2 | ESC RawCommand |
| West upper motor | S16 | CAN out 3 | ESC RawCommand |
| West lower motor | S17 | CAN out 4 | ESC RawCommand |

"Upper" is the config's `top` motor and "lower" its `bottom`.

S17 needs `SERVO_32_ENABLE = 1` before it exists as an output at all.

A timer group's PWM/DShot mode is chosen at boot from the motor functions present
then. Moving East's motors off S3/S4 onto S11/S12 updates the parameters immediately,
but TIM5 stays DShot — and silent for servos — until the board is restarted. TIM4
keeps working through that because it was never a motor group.

### Current config channel map

Taken from `Vector/config/vector.json`.

| Arm | Status | Outer | Inner | Lower (`bottom`) | Upper (`top`) |
|---|---|---|---|---|---|
| North | live | S6 | S5 | S2 | S1 |
| East | live | S8 | S7 | S11 | S12 |
| South | live | S10 | S9 | S15 (CAN 2) | S14 (CAN 1) |
| West | live | S4 | S3 | S17 (CAN 4) | S16 (CAN 3) |

Every gimbal answers with nothing but a USB cable, after a reboot that lets TIM5
come up as PWM. North and East motors do too; South and West motors need the CAN
node running and only turn under the motor test.

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

**ESC telemetry** for the DShot ESCs (North/West, S3–S6) goes to RX4 and RX6. Each
4-in-1 T wire is one-way into RX; TX4 and TX6 are unused. Do not splice two T
outputs onto one pad if both stacks can reply at once — one bus per UART.

On this board `SERIAL_ORDER` is:

```text
OTG1  UART7  USART1  USART2  USART3  UART8  UART4  USART6  OTG2
  0      1      2       3       4      5      6      7      8
```

| Pad | MCU UART | `SERIALn` | Parameter |
|---|---|---|---|
| RX4 | UART4 (`PB8`) | SERIAL6 | `SERIAL6_PROTOCOL` = 16 |
| RX6 | USART6 (`PC7`) | SERIAL7 | `SERIAL7_PROTOCOL` = 16 |

RX6 is TIM3 RCIN by default. `SERIAL7_PROTOCOL` = 16 selects the UART alternate
on that pad. After that, the receiver cannot live on RX6.

The config records this as `link.esc_telemetry_serials: [6, 7]`. Connect and apply
mapping writes both protocols. They take effect at boot, so reboot after the first
apply. East/South sit behind the CAN-to-PWM node: DroneCAN carries their RPM, but
voltage, current and temperature only appear if that 4-in-1's T-wire goes into the
**node's** ESC-telemetry UART, not RX4/RX6.

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
