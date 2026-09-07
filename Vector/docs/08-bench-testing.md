# Bench testing

Procedures in the order they should be done. Each one assumes the previous ones passed.

**Props off for everything except §7.** Read [Safety](09-safety.md) first.

## 0. Before power

- [ ] Props removed
- [ ] Airframe restrained — it will try to move once gimbals or motors run
- [ ] Servo SBEC ground bonded to flight controller ground
- [ ] Nothing in the path of a gimbal that swings unexpectedly
- [ ] Battery voltage above 3.5 V per cell (21 V for 6S)

## 1. Link

```bash
Vector/start.sh
```

Open <http://localhost:8765> and press Connect.

**Expect:** the link pill turns green, heartbeat age stays under 2 s, mode and armed
state populate.

**If nothing appears:** on WSL, the device has to be usbipd-attached — the launcher
does this on every start, including after a reboot left the board Shared but not
Attached. It will not if you passed `--no-usb`, or if the board is currently
attached to Windows for flashing. Check the
Telemetry page's message counts: empty while connected means stream rates were never
granted, so press Re-request streams.

## 2. Output mapping

Press Re-apply output mapping. From `vector.json` this sets, for every live arm:

- each motor channel's `SERVOn_FUNCTION`, without which that output emits nothing at all
- each gimbal channel's `SERVOn_FUNCTION` to 0, which is what `DO_SET_SERVO` requires
- each gimbal channel's `SERVOn_MIN`, `MAX`, `TRIM` and `REVERSED`, because the flight
  controller clamps `DO_SET_SERVO` to that window and a board at the 1000–2000 default
  quietly discards the ends of the travel
- `SERVO_DSHOT_ESC` and `SERVO_BLH_RVMASK` from the `reversed` flags

**Expect:** an event confirming the motor channel count and reverse mask. If it names
motors with no function set, those arms cannot spin until `function` is filled in.

Do this after any ESC power cycle. Reverse direction is not persistent in every ESC
firmware.

### Confirming it took

`Vector/tools/fc-report.py` reads the board and prints its view of every output beside
what the config expects. It writes nothing, so it is safe at any point.

```bash
Vector/tools/fc-report.py                     # /dev/ttyACM0
Vector/tools/fc-report.py --device /dev/ttyACM1   # while the dashboard holds ACM0
```

It reports the frame, the safety parameters, every output's function and pulse window,
which timer groups are being asked to mix modes, and the live `SERVO_OUTPUT_RAW` values.
An output reading 0 there is emitting nothing.

`FRAME_CLASS` and `FRAME_TYPE` are the one thing the dashboard will not fix for you. They
need a reboot, and changing them rearranges which physical motor answers to which mixer
slot, so the report flags a mismatch and leaves it to you.

### Pin-level check

Setup → Mapping debug. Pick a channel and run a servo sweep or a 1 s motor spin. This
drives the **pin**, not the arm name, so it is the check that answers "is this the
output I think it is?" Channel 0 is not a pin — that value disables an output in the
config.

Props off for the motor test. Use the servo test on a PWM group, not on a DShot timer —
Setup → **Output map** shows which groups are which, and a pin in a group marked DShot
will not move a servo however it is commanded.

### A servo that does not move

Work down this list; each item is silent on the vehicle, so none of them will announce
itself.

| Symptom | Cause | Fix |
|---|---|---|
| Some onboard pins work, others do nothing | The dead pins share a timer group with a motor, so the group is in DShot | Output map panel names it. Move the servo to a PWM group or the CAN node |
| Onboard pins that used to work went dead | A motor slot has no channel in the config, so the mixer claimed a low output at boot and took its group into DShot | Give every motor slot an explicit channel, re-apply mapping, reboot |
| Everything on the node stays still | `CAN_D1_UC_SRV_BM` / `CAN_D1_UC_ESC_BM` default to 0, so the node is never sent a command | Re-apply output mapping; reboot if the dashboard says CAN was off |
| One CAN output stays still, the others move | That node output's own `OUTx_FUNCTION` is wrong | Servo: `50 + channel`. ESC: `33 + slot` — OUT1–OUT4 are 33–36 as built. The flight controller cannot do this for you |
| A CAN motor never turns from the Motors panel | A CAN ESC only takes throttle while soft-armed | It goes through the motor test automatically; it needs a `test_sequence` |
| `reversed` on a CAN motor changes nothing | The reverse mask stops at the flight controller's pins | Set direction in the ESC, or swap two motor wires |
| S17 and up do not exist at all | `SERVO_32_ENABLE` is 0 | Re-apply output mapping |

The first two are checked automatically: `Vector/tools/fc-report.py` prints them under
**Output map**, and the dashboard reports them whenever mapping is asserted.

## 3. Gimbal centre

Press Center in the top bar.

**Expect:** every live gimbal goes to 1500 µs on both axes and the frame diagram shows
thrust straight up.

**Measure, do not assume.** Put a level or a square against each motor mount and check
it is actually vertical.

If an axis is off:

1. Prefer fixing it mechanically — reposition the servo horn one tooth at a time.
2. Use `trim_deg` in the config only for the residual.

Trim on the **inner** axis costs reachable tilt one degree for one degree, because the
inner servo's travel is exactly consumed by the envelope. Trim on the outer axis is free,
since it has 45° of headroom. [Kinematics §4](03-kinematics.md#headroom) explains why.

## 4. Axis direction and sign

This is the most important procedure on the page. A wrong sign makes a levelling
controller amplify a disturbance instead of cancelling it.

For each live arm, on the Arms page, move **one axis at a time** by a small amount
(start at 5°):

| Command | Expected physical result, North arm |
|---|---|
| `outer` positive | Thrust leans toward body **+Y (right/East)** |
| `inner` positive | Thrust leans toward body **−X (aft/South)** |

The signs follow from the geometry: positive outer tilt leans thrust right, and positive
inner tilt leans it *backward*, because the inner rotation carries a negative sign in the
lean mapping. Confirm the reported `servoDeg` and `tiltDeg` match what you see.

If an axis moves the wrong way, flip that axis's `sign` in the config — not
`SERVOn_REVERSED` on the flight controller. Keeping direction in one place means there is
one place to look when something is backwards.

Then verify the whole-vehicle mapping with the aim pad. Ask for **forward** lean on all
arms at once:

**Expect:** all four arms lean their thrust toward the nose. On the frame diagram every
vector points the same way. If one arm disagrees, its `mount_yaw_deg` or an axis sign is
wrong.

This test is the entire reason the mount-yaw transform exists, and it catches a wiring
swap immediately.

## 5. Envelope

On the Arms page, drag the aim pad to its boundary in several directions.

**Expect:**

- The commanded lean stops at the envelope outline rather than continuing
- Corner directions reach further than axis directions — 31.82° versus 22.5°
- At the corner where the inner servo saturates, the at-limit indicator lights
- Nothing hits a mechanical stop audibly

The outline drawn on the pad is computed from the config, so if it does not match what
the hardware does, the config is wrong. Compare against the numbers in
[Kinematics §4](03-kinematics.md#4-reachable-workspace).

## 6. Servo lag

Set a step on one axis — centre, then 15°, watching the reported pulse and the physical
response.

Estimate the time from command to settled position. That is the value for
`lead_time_s`. Typical hobby servos land between 40 and 100 ms for a step of this size
under gimbal load.

A rough measurement is fine. `lead_time_s` is a first-order correction; being 20% out
matters far less than having the sign right.

## 7. Motors

**Still props off.** Props go on only after everything above passes, and even then only
with the airframe firmly restrained and everyone clear.

Prerequisite: `function` must be set for the motor. If it is `null` the output is
Disabled and emits nothing, and the dashboard refuses rather than pretending otherwise.
Spinning is also refused while the vehicle is armed.

Start at the lowest throttle that produces rotation, for the shortest duration:

- [ ] Bottom motor alone — confirm direction by eye
- [ ] Top motor alone — confirm it is **opposite** the bottom
- [ ] Both together — listen for beat frequencies, which indicate mismatched speeds
- [ ] All live arms, both motors — every onboard propeller should come up together,
      then South and West follow one at a time
- [ ] Check ESC telemetry: RPM present, temperature sane, current plausible

South and West motors sit behind the CAN-to-PWM node. A CAN ESC receives zero unless
the vehicle is soft-armed, and on the ground only the motor test soft-arms, so the
dashboard runs those four through `DO_MOTOR_TEST` by `test_sequence` after the onboard
set. If one stays silent while the others run, the node's `OUTx_FUNCTION` for that
output is the first thing to check — the Output map on Setup shows the number.

### How the dashboard spins several motors at once

Not with `DO_MOTOR_TEST`. ArduPilot's motor test holds one motor sequence in static
state, so a second command replaces the first instead of adding to it — sending one per
motor left only the last one spinning, which is why a single click used to appear to skip
motors.

The Motors panel drives the output channels directly instead, the same way the gimbals
are driven: each channel's `SERVOn_FUNCTION` is forced to Disabled so `DO_SET_SERVO` is
honoured, all of them are written in one pass, and the motor functions go back when the
spin ends.

Two consequences are worth knowing before pressing the button:

- The motor test's own landed check and failsafe suppression are not in this path, which
  is why spinning is refused while armed.
- **`DO_SET_SERVO` has no vehicle-side timeout.** The flight controller holds the last
  pulse width it was given. The stop is owned by the dashboard server, so closing the tab
  or losing the browser still stops on time — but if the server process or the serial link
  dies mid-spin, nothing on the board will stop those propellers.

### Establishing direction

Which way a propeller *must* turn is fixed by the frame — every bottom motor CCW and
every top motor CW seen from above, derived in [Firmware](05-firmware.md). Whether a
given ESC produces that depends on how its three phases happen to be wired, which is per
motor and cannot be worked out from the config. So it is observed:

```bash
Vector/tools/motor-direction.py
Vector/tools/motor-direction.py --arm north
```

It spins one motor at a time, asks which way it turned, records the answer as `spin`, and
flips `reversed` on any motor turning against the frame. It refuses to start until you
confirm the props are off, and writes nothing until you have seen the summary.

One motor at a time is the point. A pair spun together tells you nothing about which of
the two is wired backwards.

Afterwards, re-apply the output mapping (§2) **and reboot**. Direction does not change
without a reboot: `SERVO_BLH_RVMASK` is `@RebootRequired` and the HAL only ORs into its
reversed mask, so a cleared bit survives until boot too. This is the single most common
reason a direction change appears to do nothing.

Watch cell voltage during the test. Li-ion sags hard under load; a large drop at low
throttle means a tired pack or a bad connection.

## 8. Levelling demo

Stabilize page. Props off. Airframe held in your hands or on a gimballed stand.

Start conservative:

| Setting | Start at |
|---|---|
| `level_gain` | 0.3 |
| `lead_time_s` | measured in §6 |
| `max_tilt_fraction` | 0.5 |

Press start and tilt the airframe slowly.

**Expect:** the gimbals rotate opposite to your motion, keeping the motors pointing at
world vertical. Tilt the nose down and the gimbals should pitch back.

**If they move the wrong way:** stop, and flip `invert_roll` or `invert_pitch`. Then work
out which axis sign in the config is actually wrong and fix it there, so the invert flags
can go back to false.

Raise `level_gain` toward 1.0 and `max_tilt_fraction` toward 1.0 as confidence grows. At
gain 1.0 the gimbals should hold vertical exactly, up to the envelope limit and servo
lag.

**Expected limits, so they are not mistaken for faults:**

- Beyond 22.5° of airframe tilt the gimbals saturate and the saturated indicator lights.
  That is the envelope, not a bug.
- The loop runs at 25 Hz. Fast motion will visibly lag. That is the point of the section
  in [Control](04-control.md) about why this cannot fly the vehicle.

## 9. Serial budget

Telemetry page, Link panel.

**Expect:** with USB at 115200 baud, budget used stays well under 100%.

The number becomes relevant if you ever drive gimbals over a telemetry radio, where it
will not fit. Better to know the limit here than to discover it as unexplained latency.

## Recording results

Note in your build log:

- Trim values for each axis, and whether they were mechanical or config
- Measured servo lag
- Which `sign` values needed flipping
- Motor test throttle and duration used
- Cell voltage under load

Signs and trims are the values everything else depends on. Do not rely on remembering
them — they end up in `vector.json`, which is the record.

## Common symptoms

| Symptom | Likely cause |
|---|---|
| Commands refused, "Not connected" | Link down, or usbipd not attached |
| Commands refused, "no arms live" | Every arm is `planned`; promote one |
| Gimbal moves half as far as asked | `gear_ratio` wrong |
| Inner axis drifts when the outer moves | `coupling` wrong, or the linkage is not as modelled |
| One arm leans opposite the others | `mount_yaw_deg` or an axis `sign` wrong on that arm |
| Levelling amplifies tilt instead of cancelling | Sign inverted; stop immediately |
| Envelope smaller than expected | `trim_deg` eating travel, especially on the inner axis |
| No ESC telemetry | `SERIAL4_PROTOCOL` / `SERIAL6_PROTOCOL` not 16, T-wire not on RX3/RX4, or firmware that only reads the first ESC-telemetry UART |
| Servos jitter | Two writers — check the arbiter owner on screen; or SBEC ground not bonded |
| Pulse readout differs from what was commanded | Something else is writing those outputs |
| A motor does nothing, commands accepted | `SERVOn_FUNCTION` is 0. A disabled output emits no signal at all |
| Nothing on any output after moving a channel | Reassigning a gimbal onto a motor's old channel erased that motor's function, and `PARAM_SET` persists. Re-apply the mapping |
| `motor-direction.py` spins a different motor than named | `test_sequence` is for another `FRAME_CLASS`/`FRAME_TYPE` |
| Dashboard spins a different motor than named | `channel` or `function` is wrong for that arm |
| Gimbal will not reach its limit | `SERVOn_MIN`/`MAX` still at 1000–2000, clamping the 500–2500 window |
| Servo silent but the report shows a valid pulse | Signal is arriving; look downstream at SBEC power or the ground bond |
| Changing `reversed` does nothing | `SERVO_BLH_RVMASK` is `@RebootRequired`, and the HAL only ORs into its reversed mask. Reboot |
| Changing `spin` does nothing | It never will. `spin` is an observation and is never written to the board; `reversed` is the control |
| Wrong propeller spins after moving a motor | The old channel kept its motor function. Re-apply the mapping, which now clears stale ones, then reboot |
| A motor runs up hard the moment it is powered | `SERVOn_REVERSED` is 1 on a motor output, which idles it at full throttle. Re-apply the mapping |

When more than one of these is true at once, run `Vector/tools/fc-report.py` before
changing anything. Most of them are a board that disagrees with `vector.json`, and the
report says which parameter in one line instead of a guess per symptom.
