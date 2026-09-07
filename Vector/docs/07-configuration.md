# Configuration

The whole vehicle is described by one file: **`Vector/config/vector.json`**.

Nothing in the server or the web app enumerates arms any other way. Adding an arm means
appending one entry to the `arms` list — no code changes anywhere.

## Adding an arm

Everything an arm does not specify falls back to `gimbal_defaults`, so a new arm is
short:

```json
{
  "id": "north_east",
  "label": "North-East",
  "status": "planned",
  "azimuth_deg": 45,
  "mount_yaw_deg": 45,
  "outer": { "channel": 20 },
  "inner": { "channel": 21 },
  "motors": {
    "bottom": { "channel": 11, "spin": "ccw", "reversed": false },
    "top":    { "channel": 12, "spin": "cw",  "reversed": true }
  }
}
```

Reload the config (Setup page, or restart) and it appears everywhere: the sidebar, the
frame diagram, the Arms page with its own aim pad and correctly-computed envelope, the
3D model, the telemetry tables.

Start with `status: "planned"`. The dashboard will show the arm but refuse to command
it, so a typo in a channel number cannot drive an output that belongs to something else.
Promote to `"live"` once the wiring is verified.

Loading fails outright if two entries claim the same output channel, except **channel 0**,
which means unused and may be shared. Two real outputs on one
pad would silently fight each other, so it is better to refuse than to start.

## Top level

```json
{
  "schema_version": 1,
  "vehicle":         { ... },
  "gimbal_defaults": { ... },
  "arms":            [ ... ],
  "link":            { ... },
  "bench_limits":    { ... },
  "bench_controller":{ ... }
}
```

## `vehicle`

| Field | Type | Meaning |
|---|---|---|
| `name` | string | Shown in the UI title |
| `summary` | string | One-line description |
| `controller` | string | Flight controller model, for reference |
| `frame.layout` | string | `plus` or `x`. Descriptive; the geometry comes from arm azimuths |
| `frame.nose_arm` | string | Which arm the nose points along |
| `frame.rotor_diagonal_m` | number | Rotor to opposite rotor, metres |
| `frame.arm_length_m` | number | Hub to centre, metres. Used by the 3D model and moment arms |
| `battery.*` | | Chemistry, cells in series and parallel, capacity. Reference only |

## `gimbal_defaults`

Every field here can be overridden per arm.

| Field | Type | Meaning |
|---|---|---|
| `gear_ratio` | number | **Servo degrees per gimbal degree.** 2.0 for a 2:1 reduction |
| `tilt_limit_deg` | number | Maximum geometric tilt per axis. The mechanical stop |
| `coupling` | number | How much outer-axis motion leaks into the inner axis. 1.0 here |
| `outer` | object | Outer (roll) axis, mounted to the arm |
| `inner` | object | Inner (pitch) axis, carried by the outer ring |

`coupling` is the field most worth understanding. It is 1.0 on this vehicle because the
inner servo is mounted to the airframe, so rotating the outer ring drags the inner axis
along and the inner command has to add that back. A design where the inner servo rides
on the outer ring would set it to 0 and need half the inner travel.
[Kinematics §1](03-kinematics.md#1-tilt-to-servo-angle) derives it.

### Axis fields

| Field | Type | Meaning |
|---|---|---|
| `channel` | int | Output channel, 0–32. **0 disables the output.** 1–32 are SERVO1..SERVO32; above the controller's own count means the CAN board |
| `sign` | ±1 | Direction. Absorbs which way the servo and linkage are mounted |
| `center_us` | int | Pulse for zero servo angle |
| `us_per_deg` | number | Pulse microseconds per servo degree |
| `servo_limit_deg` | number | How far the servo may be driven from centre |
| `min_us`, `max_us` | int | Absolute pulse bounds. A secondary electrical guard |
| `trim_deg` | number | Mechanical zero offset, in servo degrees |

Three separate limits, and they do different jobs. Getting them confused is the main way
to end up with a gimbal that either cannot reach its envelope or drives into a stop:

| Limit | Bounds | Job |
|---|---|---|
| `tilt_limit_deg` | Gimbal tilt | The operating envelope. Enforced before anything else |
| `servo_limit_deg` | Servo angle | How far the servo itself can travel |
| `min_us` / `max_us` | Pulse width | Last-resort electrical clamp |

Because `tilt_limit_deg` is enforced first, the gimbal can never be commanded past its
stop regardless of what the other two allow. That is why `servo_limit_deg` is set to the
servo's real ±90° rather than the 45° the outer axis happens to need: describing the
hardware honestly gives trim somewhere to go, while the tilt limit still protects the
mechanism.

## `arms`

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Stable identifier used in commands and URLs |
| `label` | string | Display name |
| `status` | enum | `live`, `planned`, or `disabled` |
| `azimuth_deg` | number | Arm direction, from body +X toward +Y |
| `mount_yaw_deg` | number | Gimbal rotation about body Z relative to the airframe |
| `outer`, `inner` | object | Axis overrides. At minimum, `channel` |
| `motors.bottom`, `motors.top` | object | Motor definitions |

### Status

| Status | Shown | Commandable |
|---|---|---|
| `live` | yes | yes |
| `planned` | yes, dimmed | no |
| `disabled` | yes, dimmed | no |

`planned` means "will exist, not built yet" — the build state stays visible in the UI
rather than living in someone's head. `disabled` means "exists but taken out of service".

### `azimuth_deg` versus `mount_yaw_deg`

These are usually equal but they mean different things:

- **`azimuth_deg`** is where the arm physically points. It sets the moment arm and the
  tangential yaw direction.
- **`mount_yaw_deg`** is how the gimbal assembly is rotated. It sets how gimbal angles
  map to thrust direction.

They differ if a gimbal is bolted on rotated relative to its arm. Keeping them separate
means such a build is a config change, not a code change.

### Motor fields

| Field | Type | Meaning |
|---|---|---|
| `channel` | int | Output channel. **0 does not disable the motor — see below** |
| `spin` | `cw`/`ccw` | The direction the propeller was **observed** to turn. Never written to the board |
| `reversed` | bool | The only field that changes direction. Becomes a bit in `SERVO_BLH_RVMASK` — for a motor on the CAN node it is intent only; see [Firmware](05-firmware.md) |
| `test_sequence` | int or null | The number `MAV_CMD_DO_MOTOR_TEST` expects |
| `function` | int or null | `SERVOn_FUNCTION`: 33–40 for Motor1–Motor8. Also what the dashboard needs to spin the motor |

### A motor `channel` of 0 gives the pin away

On a gimbal axis, `channel: 0` means what it says: nothing is written, the pin is free.
On a **motor** it does the opposite of what it looks like.

The dashboard writes no `SERVOn_FUNCTION` for a motor with no channel, so no output
claims that mixer slot. At the next boot `AP_Motors::add_motor_num` calls
`SRV_Channels::set_aux_channel_default(function, motor_num)`, which places MotorN on
SERVO(N) unless a channel already holds that function — and it treats
`SERVOn_FUNCTION = 0` as unclaimed, because Disabled *is* `k_none`. The slot lands on a
low output, that output becomes a DShot ESC pin, and its whole timer group goes into
DShot with it. Any gimbal servo in that group stops moving.

Setting all eight motors to `channel: 0` therefore does not free the board — it hands
S1–S8 to the mixer and silences every servo outside TIM15. Give each motor slot an
explicit channel on a group that carries only motors.

The check for this lives in `Vector/dashboard/server/board.py`. It runs on every mapping
assert, shows in Setup → **Output map**, and prints in `Vector/tools/fc-report.py`.

### `spin` and `reversed` are not the same kind of field

Editing `spin` changes nothing on the vehicle. It is a record of what the propeller was
seen to do, kept so the intended layout can be checked against reality;
`Vector/tools/motor-direction.py` fills it in and `Vector/tools/fc-report.py` reports
where it disagrees with the frame.

`reversed` is the control. It becomes a bit in `SERVO_BLH_RVMASK`, which is
`@RebootRequired` — and the HAL only ORs into its internal reversed mask, so a *cleared*
bit also survives until the next boot. **Changing `reversed` requires a reboot in both
directions.** See [Firmware](05-firmware.md).

`test_sequence` is **not** the output channel. ArduPilot addresses motors by their
position in the frame's test order, which depends on `FRAME_CLASS` and `FRAME_TYPE`, so
it cannot be derived and has to be recorded. A wrong number spins an unexpected motor.

It is needed by `Vector/tools/motor-direction.py` and by any other GCS's motor test, but
not by the dashboard's Motors panel: that spins motors together, which `DO_MOTOR_TEST`
cannot do, so it addresses output channels directly and needs `function` instead. A motor
with no `function` is refused there.

See [Firmware](05-firmware.md) for the values under OCTAQUAD / PLUS.

## `link`

| Field | Type | Meaning |
|---|---|---|
| `device` | string | Serial device, e.g. `/dev/ttyACM0` |
| `baud` | int | 115200 for USB |
| `esc_telemetry_serials` | list of int | Which `SERIALn` ports carry ESC telemetry. `[4, 6]` is RX3 and RX4 on this board |

`esc_telemetry_serials` is asserted on connect (`SERIALn_PROTOCOL` = 16) and names
the right parameters in the UI when nothing is reporting. A legacy
`esc_telemetry_serial` integer still loads as a one-port list.

## `bench_limits`

Hard bounds on what the dashboard will do. These exist so a slider cannot ask for
something dangerous, and they are enforced server-side.

| Field | Value | Meaning |
|---|---|---|
| `motor_percent` | 50.0 | Maximum throttle for a motor test |
| `motor_seconds` | 10.0 | Maximum duration for a motor test |
| `servo_speed_deg_s` | 180.0 | Maximum slew rate |
| `default_servo_speed_deg_s` | 45.0 | Slew rate when a command does not specify one |
| `command_rate_hz` | 25.0 | Rate of the levelling loop and streamed aiming |

`command_rate_hz` also drives the serial budget shown on the Telemetry page: eight
servos at 25 Hz is fine over USB and will not fit on a telemetry radio.

## `bench_controller`

Levelling demo settings. Not flight control — see [Control](04-control.md).

| Field | Range | Meaning |
|---|---|---|
| `mode` | `off`/`level` | Starting mode |
| `level_gain` | 0–1.5 | 0 leaves gimbals with the airframe, 1 holds true world vertical |
| `lead_time_s` | 0–0.5 | Seconds of attitude extrapolation, to offset servo lag |
| `max_tilt_fraction` | 0.05–1 | Fraction of the uniform tilt limit the loop may use |
| `invert_roll` | bool | Bench sign flip |
| `invert_pitch` | bool | Bench sign flip |

The invert flags are for **finding** the right signs. Once found, fold them into the
per-axis `sign` values so the config describes hardware and the controller settings
describe tuning.

## Editing

Three ways, all equivalent:

1. **Setup page.** Validates before writing. Recommended.
2. **Edit the file and reload.** The Setup page has a Reload button; a server restart
   also picks it up.
3. **Programmatically** via `config.save()`, which validates and writes atomically.

Writes go through a temporary file and an atomic rename, and the merged document is
validated *before* anything touches the disk. A rejected edit leaves the previous config
in place and running.

## Validation

Loading refuses a document that would produce a vehicle that cannot work:

| Check | Why |
|---|---|
| Non-empty `arms` list | Nothing to control otherwise |
| Unique arm ids | Commands address arms by id |
| No duplicate output channels | Two writers on one pad fight silently |
| Channels within 1–32 | `SERVO1`–`SERVO32` is what ArduPilot has |
| `sign` is not zero | A zero sign makes the axis uninvertible |
| `us_per_deg` positive | Otherwise degrees and microseconds run backwards |
| `min_us` below `max_us` | An empty window is unreachable |
| `servo_limit_deg` positive | Zero travel is not a servo |
| `gear_ratio` positive | Zero or negative is not a reduction; use `sign` for direction |
| `tilt_limit_deg` positive | Zero envelope |
| `status` in the allowed set | A typo would otherwise read as not-live and be silently ignored |

Errors name the exact path, for example
`arms[1] (east).inner: channel 33 outside 0..SERVO32 (0 disables the output)`.

These are refusals: the document does not load. A separate class of problem — a map that
loads cleanly but that the *hardware* will not honour, such as a servo sharing a timer
group with an ESC — is reported rather than refused, because it depends on the board
rather than on the document. Setup → **Output map** lists those, and the dashboard
repeats them whenever output mapping is asserted.

## Full current config

```json
{
  "schema_version": 1,
  "vehicle": {
    "name": "Vector",
    "controller": "Matek H743-Wing V3",
    "frame": {
      "layout": "plus",
      "nose_arm": "north",
      "rotor_diagonal_m": 0.81,
      "arm_length_m": 0.405
    },
    "battery": { "chemistry": "li-ion", "cells_series": 6, "cells_parallel": 2, "capacity_mah": 10000 }
  },
  "gimbal_defaults": {
    "gear_ratio": 2.0,
    "tilt_limit_deg": 22.5,
    "coupling": 1.0,
    "outer": { "sign": -1, "center_us": 1500, "us_per_deg": 11.11111,
               "servo_limit_deg": 90.0, "min_us": 500, "max_us": 2500, "trim_deg": 0.0 },
    "inner": { "sign":  1, "center_us": 1500, "us_per_deg": 11.11111,
               "servo_limit_deg": 90.0, "min_us": 500, "max_us": 2500, "trim_deg": 0.0 }
  },
  "arms": [ "north (live, inner S5 / outer S6, motors S2 lower / S1 upper)",
             "east  (live, inner S7 / outer S8, motors S11 lower / S12 upper)",
             "south (live, inner S9 / outer S10, motors S15 lower / S14 upper on CAN)",
             "west  (live, inner S3 / outer S4, motors S17 lower / S16 upper on CAN)" ],
  "link": { "device": "/dev/ttyACM0", "baud": 115200, "esc_telemetry_serials": [4, 6] },
  "bench_limits": { "motor_percent": 50.0, "motor_seconds": 10.0,
                    "servo_speed_deg_s": 180.0, "default_servo_speed_deg_s": 45.0,
                    "command_rate_hz": 25.0 },
  "bench_controller": { "mode": "off", "level_gain": 1.0, "lead_time_s": 0.06,
                        "max_tilt_fraction": 1.0, "invert_roll": false, "invert_pitch": false }
}
```

The `arms` list is abbreviated here; read the real file for the channel map, which is
also tabulated in [Hardware](02-hardware.md).
