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

**If nothing appears:** on WSL, the device has to be usbipd-attached — the launcher does
this, but not if the board is currently attached to Windows for flashing. Check the
Telemetry page's message counts: empty while connected means stream rates were never
granted, so press Re-request streams.

## 2. Output mapping

Press Re-apply output mapping. This sets `SERVO_DSHOT_ESC` and `SERVO_BLH_RVMASK` from
the `reversed` flags in the config.

**Expect:** an event confirming the motor channel count and reverse mask.

Do this after any ESC power cycle. Reverse direction is not persistent in every ESC
firmware.

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

Prerequisite: `test_sequence` must be set for the motor. If it is `null` the dashboard
refuses, which is correct — see [Firmware](05-firmware.md) for the values.

Start at the lowest throttle that produces rotation, for the shortest duration:

- [ ] Bottom motor alone — confirm direction by eye
- [ ] Top motor alone — confirm it is **opposite** the bottom
- [ ] Both together — listen for beat frequencies, which indicate mismatched speeds
- [ ] Check ESC telemetry: RPM present, temperature sane, current plausible

If the top motor spins the same way as the bottom, its `reversed` flag or the DShot
reverse command did not take. Re-apply the output mapping (§2) and retest.

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
| No ESC telemetry | `SERIAL6_PROTOCOL` not 16, or the telemetry wire is not on RX4 |
| Servos jitter | Two writers — check the arbiter owner on screen; or SBEC ground not bonded |
| Pulse readout differs from what was commanded | Something else is writing those outputs |
