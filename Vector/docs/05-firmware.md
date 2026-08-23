# Firmware

## State of play

The surrounding tree is an **unmodified ArduPilot checkout**. No Vector-specific
firmware exists yet.

That is deliberate. The kinematics were derived and validated on the bench first, so
the mixer can be written against relationships that are already known to be correct
rather than discovered during flight testing.

> A boot-time hook that automatically spun the North motors was removed from this tree.
> It set `HAL_SERVO_BENCH_OSCILLATE` in the shared `MatekH743` hwdef and, three seconds
> after power-up, called `force_safety_off()`, soft-armed the vehicle and ran both
> motors at ~7% for two seconds — with no operator action. The dashboard's bounded,
> on-demand motor test replaces it. Do not reintroduce anything that arms the vehicle
> without an explicit command.

## Frame configuration

ArduPilot already supports this airframe. A coaxial quad in plus layout is
`OCTAQUAD` / `PLUS`:

| Parameter | Value | Meaning |
|---|---|---|
| `FRAME_CLASS` | 4 | OCTAQUAD |
| `FRAME_TYPE` | 0 | PLUS |

From `AP_MotorsMatrix::setup_octaquad_matrix()`, that gives eight motors arranged as
four counter-rotating pairs, one pair per arm:

| Motor | Angle | Rotation | Test order | Vector arm | Role |
|---|---|---|---|---|---|
| Motor1 | 0° | CCW | 1 | North | bottom |
| Motor6 | 0° | CW | 2 | North | top |
| Motor4 | 90° | CW | 3 | East | top |
| Motor7 | 90° | CCW | 4 | East | bottom |
| Motor3 | 180° | CCW | 5 | South | bottom |
| Motor8 | 180° | CW | 6 | South | top |
| Motor2 | −90° | CW | 7 | West | top |
| Motor5 | −90° | CCW | 8 | West | bottom |

The test ordering is conveniently grouped by arm: North is 1 and 2, East 3 and 4, South
5 and 6, West 7 and 8.

This means **the motor mixing for roll, pitch, yaw and throttle needs no new code at
all.** ArduPilot handles differential thrust correctly for this geometry already. The
only missing piece is the gimbal mixer.

### Test sequence numbers are frame-dependent

`MAV_CMD_DO_MOTOR_TEST` addresses motors by *test order*, not by output channel. So the
`test_sequence` values in `vector.json` are only valid for one `FRAME_CLASS`/`FRAME_TYPE`
combination.

The vehicle was originally bench-tested as **QUAD / X**, where North's pair is Motor1
and Motor2 with test orders 1 and 3. It is now configured for OCTAQUAD / PLUS, so those
test orders became 1 and 2 and the functions became Motor1 and Motor6. Nothing in the
firmware notices the difference — the numbers simply mean something else, which is why
they cannot be carried across a frame change unchanged.

`frame_class` and `frame_type` in `vector.json` record the frame those numbers belong
to. `Vector/tools/fc-report.py` compares them against the board and flags a mismatch,
because a frame mismatch invalidates every motor number at once.

Anything left as `null` means "not established", and the dashboard refuses to spin that
motor rather than guessing. That is the right default — a wrong test number spins an
unexpected motor.

## Motor output parameters

| Parameter | Value | Why |
|---|---|---|
| `FRAME_CLASS` | 4 | OCTAQUAD. Needs a reboot |
| `FRAME_TYPE` | 0 | PLUS, matching arms at 0/90/180/270 |
| `SERVOn_FUNCTION` | 33–40 | Motor1–Motor8, per the table above |
| `MOT_PWM_TYPE` | 6 | DShot600 |
| `SERVO_DSHOT_ESC` | 1 | BLHeli, needed for the reverse command |
| `SERVO_BLH_RVMASK` | bitmask | Bit N is SERVO(N+1); set for each top motor |
| `SERIAL6_PROTOCOL` | 16 | ESC Telemetry on UART4 (RX4) |

Top motors run reversed so that both motors in a coaxial pair produce upward thrust.

The dashboard asserts the `SERVOn_FUNCTION` values, the DShot flags and the gimbal pulse
limits on connect, all from `vector.json`, so the board cannot drift away from the config
unnoticed. It deliberately does **not** write `FRAME_CLASS` or `FRAME_TYPE`: those need a
reboot, and changing them rearranges which physical motor answers to which mixer slot.

### An output with no function emits nothing

This is worth stating plainly because it is silent. `SERVOn_FUNCTION = 0` (Disabled)
produces no signal at all — not a centre pulse, not a zero throttle, nothing. A motor on
a disabled output cannot spin however correct the rest of the configuration is.

It is also easy to cause by accident. The dashboard needs gimbal outputs Disabled for
`DO_SET_SERVO`, and `PARAM_SET` persists to EEPROM, so pointing a gimbal at a channel
that used to carry a motor permanently erases that motor's function. Reassigning outputs
therefore means reasserting the mapping, not just editing the config.

Run `Vector/tools/fc-report.py` when something does not move. It is read-only and prints
the board's own view of every output next to what the config expects.

## Gimbal output parameters

The dashboard sets each gimbal output's `SERVOn_FUNCTION` to 0 (Disabled) before
driving it, because ArduPilot only honours `DO_SET_SERVO` on a disabled output. That is
correct for bench work and wrong for flight — in flight the mixer must own those
outputs.

For flight, gimbal outputs need a function that a mixer can write. Two options:

- **`k_scripting1`–`k_scripting8` (94–101)**, writable from Lua via
  `SRV_Channels:set_output_pwm()`. Good for prototyping.
- **New `SRV_Channel::Aux_servo_function_t` entries**, e.g. `k_vector_gimbal1..8`,
  written by a C++ mixer. The right answer for production.

Pulse limits per output should be set to the servo's real range so nothing else clips
them:

| Parameter | Value |
|---|---|
| `SERVOn_MIN` | 500 |
| `SERVOn_MAX` | 2500 |
| `SERVOn_TRIM` | 1500 |
| `SERVOn_REVERSED` | 0 — signs live in `vector.json`, not here, so there is one place to look |

## The mixer that has to be written

### What it does

Input: a lateral acceleration demand in body frame, plus a yaw contribution.
Output: eight servo pulse widths.

```text
for each arm:
    forward, right   = demand, plus the tangential yaw pattern scaled by yaw demand
    tilt_outer, tilt_inner = body_tilt_to_gimbal(mount_yaw, forward, right)
    scale            = max_scale(gimbal, tilt_outer, tilt_inner)
    servo_outer, servo_inner = servo_deg_for_tilt(gimbal, tilt · scale)
    pulse            = centre + servo · us_per_deg
```

Every one of those functions already exists and is tested, in
[`kinematics.py`](../dashboard/server/kinematics.py). The C++ mixer should be a direct
transliteration, checked against `tests/golden_kinematics.json` — the same file the
TypeScript port is checked against. That is the point of having golden vectors.

### Saturation

If any arm cannot reach its request, scale the demand for **all** arms by the smallest
achievable fraction. Per-arm scaling would point the four thrust vectors in
inconsistent directions, producing a net moment nobody asked for.

Priority when servos saturate: **attitude first, translation second.** Losing
translation authority is a degraded flight. Losing attitude authority is a crash.

### Where it belongs

A new `AP_Motors` backend deriving from `AP_MotorsMatrix`, so the existing and correct
differential-thrust mixing is inherited and only the servo outputs are added:

```text
libraries/AP_Motors/AP_MotorsVector.h / .cpp     new backend
libraries/AP_Motors/AP_Motors_config.h           AP_MOTORS_VECTOR_ENABLED guard
Tools/scripts/build_options.py                   build server option
```

Constraints from `AGENTS.md` that apply directly:

- Wrap it in `#if AP_MOTORS_VECTOR_ENABLED` and add the build option
- Do not modify core files to accommodate it; a core component must not depend on an
  optional one
- New parameters need full `@Param` annotation blocks, and parameter names cap at 16
  characters
- Never change existing `AP_GROUPINFO` indices

### Suggested parameters

Not yet implemented. Names are within the 16-character limit.

| Parameter | Meaning |
|---|---|
| `VEC_ENABLE` | Master enable |
| `VEC_TILT_MAX` | Gimbal tilt limit, degrees |
| `VEC_GEAR` | Servo degrees per gimbal degree |
| `VEC_COUPLING` | Inner-axis coupling factor |
| `VEC_YAW_MIX` | Blend between reaction-torque yaw and tangential-tilt yaw |
| `VEC_ARM{n}_YAW` | Mount yaw per arm, degrees |
| `VEC_ARM{n}_SGN` | Sign bits per arm |

The gimbal geometry has to be duplicated into parameters because firmware cannot read
`vector.json`. Keeping the two in step is a real maintenance hazard, and the mitigation
is to generate a parameter file from the config rather than typing it twice — see
[Roadmap](10-roadmap.md).

### Safety-critical: needs human review

Flagged explicitly, per `AGENTS.md`. Do not accept AI-generated code for these without
review by someone who knows ArduPilot's control stack:

- Saturation handling and the attitude-over-translation priority
- Behaviour on servo failure or a gimbal jammed off-centre
- What the gimbals do during a failsafe, and during disarm
- Interaction with `AC_AttitudeControl` when a lateral demand and an attitude demand
  conflict
- Whether gimbal inertia needs feed-forward compensation

## A prototyping path

Before committing to C++, the mixer can be prototyped in Lua:

1. Set the eight gimbal outputs to `k_scripting1`–`k_scripting8` (94–101)
2. Read attitude and the lateral demand from the script API
3. Implement the same kinematics and write pulses with
   `SRV_Channels:set_output_pwm()`
4. Validate against the golden vectors by logging what the script computes

Lua runs far slower than the 400 Hz main loop, so this is not flight-ready for
attitude-coupled work. It is, however, a real firmware-side loop with bounded latency,
which the host-side dashboard can never be — see [Control](04-control.md).

## Building

```bash
Vector/tools/build-and-flash.sh --build-only     # build for MatekH743
Vector/tools/build-and-flash.sh                  # build and flash
Vector/tools/build-and-flash.sh --force          # --upload-force
```

On WSL2, `./waf --upload` uses Windows `python.exe` and COM ports, not usbipd. Leave the
board attached to Windows for flashing and only usbipd-attach it afterwards for the
dashboard. Install the upload dependencies on the Windows side once:

```powershell
python.exe -m pip install empy==3.3.4 pyserial
```

## SITL

SITL has no model of a thrust-vectoring frame, so the gimbals do nothing there. A
`SIM_Vector` backend under `libraries/SITL/` would be needed to test the mixer without
hardware, and it is not written. This is on the [roadmap](10-roadmap.md) and is the
single highest-value piece of firmware work after the mixer itself, because it makes
autotest coverage possible.
