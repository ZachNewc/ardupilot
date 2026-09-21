# Kinematics

Everything in this chapter is implemented in
[`dashboard/server/kinematics.py`](../dashboard/server/kinematics.py), ported to
TypeScript in [`dashboard/web/src/lib/kinematics.ts`](../dashboard/web/src/lib/kinematics.ts),
and pinned by golden vectors in `tests/golden_kinematics.json` so the two languages
cannot drift apart.

Angles are degrees on every public interface. Internal trigonometry uses radians.

## Frames and conventions

**Body frame** is ArduPilot's: X forward, Y right, Z down. A centred gimbal points its
thrust along body −Z, straight up.

**Arm azimuth** ψ is measured from body +X toward body +Y. North 0°, East 90°,
South 180°, West 270°.

**Mount yaw** is how far the gimbal assembly is rotated about body Z relative to the
airframe. On this vehicle it equals the arm azimuth, because each gimbal is bolted onto
its arm in the same orientation relative to that arm.

**Gimbal angles** are the two geometric tilts:

- `tilt_outer` (α) — rotation about the outer axis, which is mounted to the arm
- `tilt_inner` (β) — rotation about the inner axis, which the outer ring carries

Both are limited to ±22.5°.

**Sign conventions** come from the config, not from code: each axis has a `sign` of ±1
that absorbs which way the servo is bolted on and which way the linkage runs. Getting
these wrong makes the vehicle destabilise itself, so they are established on the bench
before anything else — see [Bench testing](08-bench-testing.md).

## 1. Tilt to servo angle

This is the map the vehicle actually commands: given a desired geometric tilt, what
angle must each servo hold?

### The outer axis

The outer servo drives the outer ring through the 2:1 reduction:

```text
servo_outer = sign_outer · gear · tilt_outer + trim_outer
```

With `sign_outer = −1`, `gear = 2`, `trim = 0`, a tilt of +22.5° needs −45° of servo.

### The inner axis, and why it needs double travel

Here is the part that is easy to get wrong. The inner axis's servo is **mounted to the
airframe, not to the outer ring**. So when the outer ring rotates, it drags the inner
axis with it, and the inner servo has to undo that just to stay put.

Let the outer drive angle be:

```text
drive_outer = sign_outer · gear · tilt_outer
```

The inner servo's command is its own motion plus the correction for the outer ring
having moved underneath it:

```text
servo_inner = sign_inner · gear · tilt_inner  +  coupling · drive_outer  +  trim_inner
```

`coupling` is 1.0 on this vehicle: the outer ring's rotation transfers fully into the
inner axis. A design where the inner servo rides on the outer ring would have
`coupling = 0` and would need only ±45° of travel.

The travel requirement follows directly:

```text
inner travel needed = gear · tilt_limit · (1 + |coupling|)
                    = 2 · 22.5 · 2
                    = 90 degrees
```

Which is exactly what a ±90° servo provides — with nothing to spare. That is a real
constraint, not a coincidence, and it is discussed under [Headroom](#headroom) below.

### Worked values, North arm

`sign_outer = −1`, `sign_inner = −1`, `gear = 2`, `coupling = 1`, centre 1500 µs at
11.111 µs/deg:

| tilt (outer, inner) | servo outer | servo inner | pulse (outer, inner) |
|---|---|---|---|
| (0, 0) | 0° | 0° | 1500, 1500 |
| (22.5, 0) | −45° | −45° | 1000, 1000 |
| (0, 22.5) | 0° | −45° | 1500, 1000 |
| (22.5, 22.5) | −45° | **−90°** | 1000, 500 |
| (22.5, −22.5) | −45° | **0°** | 1000, 1500 |
| (−22.5, −22.5) | +45° | **+90°** | 2000, 2500 |

Two rows deserve attention. At (22.5, −22.5) the inner servo sits at **zero** — the
coupling correction and the inner axis's own motion happen to cancel. At (22.5, 22.5)
they add, and the inner servo is at its mechanical limit. So the inner servo's travel
is consumed at the *corners* of the tilt square, and which corners depends on the signs.

### Inverse

Recovering tilt from servo angles is the same algebra rearranged, and it is what the
dashboard uses to display where the gimbals actually are:

```text
drive_outer = servo_outer − trim_outer
tilt_outer  = drive_outer / (sign_outer · gear)
tilt_inner  = (servo_inner − trim_inner − coupling · drive_outer) / (sign_inner · gear)
```

## 2. Tilt to thrust direction

Where does the thrust actually point? Start with a centred arm, thrust along body −Z:

```text
n = (0, 0, −1)
```

The inner rotation happens on the ring the outer one carries, so in body frame the
outer rotation is applied last. Using intrinsic rotations, the composition is:

```text
n₀ = Rx(α) · Ry(β) · (0, 0, −1)
```

Expanding, with Rx a rotation about body X and Ry about body Y:

```text
Ry(β) · (0, 0, −1) = (−sin β,  0,  −cos β)

Rx(α) · (−sin β, 0, −cos β) = (−sin β,  cos β · sin α,  −cos β · cos α)
```

Then rotate by the mount yaw about body Z:

```text
n = Rz(mount_yaw) · n₀
```

So the complete forward map is:

```text
n₀ = ( −sin β,  cos β · sin α,  −cos β · cos α )
n  = Rz(mount_yaw) · n₀
```

This is exact — no small-angle assumption anywhere. A centred gimbal gives
`n = (0, 0, −1)` as it must.

### Lean angles

It is often more convenient to talk about how far the thrust leans out of vertical
toward body +X and +Y:

```text
forward = asin(nₓ)
right   = asin(n_y)
```

These are what the dashboard's aim pad and the levelling controller work in, because
they are the same quantity on every arm regardless of mount yaw. Asking all four arms
for "10° forward" gives four different pairs of gimbal angles that all point their
thrust the same way:

| Arm | Mount yaw | tilt (outer, inner) | servo (outer, inner) |
|---|---|---|---|
| North | 0° | (0.000, −10.000) | (0.00, −20.00) |
| East | 90° | (−10.000, 0.000) | (20.00, 20.00) |
| South | 180° | (0.000, 10.000) | (0.00, 20.00) |
| West | 270° | (10.000, 0.000) | (−20.00, −20.00) |

That table is the whole payoff of doing the geometry properly: one command, four
correct answers, no per-arm lookup tables.

## 3. The linear map, and what it costs

The exact inverse needs `asin` and `atan2`. A flight-controller mixer running at 400 Hz
wants something cheaper, and it wants something trivially invertible. So there is a
second map — a plain rotation, exact to first order:

```text
forward = −β · cos(mount_yaw) − α · sin(mount_yaw)
right   = −β · sin(mount_yaw) + α · cos(mount_yaw)
```

and its exact inverse:

```text
α = −forward · sin(mount_yaw) + right · cos(mount_yaw)
β = −(forward · cos(mount_yaw) + right · sin(mount_yaw))
```

### The error is not negligible

The two gimbal rotations do not commute, so this approximation is not free. Comparing
the linear map against the exact geometry across the whole reachable envelope:

**Worst-case error: 1.795°, at (α, β) = (±22.5, ±22.5).**

That is about 8% of full deflection. It is not a rounding difference and it should not
be described as one.

The error has a single cause. Look at the exact `right` component:

```text
right_exact = asin(sin α · cos β)
```

The outer axis's contribution is scaled by `cos β`. Deflecting the inner axis tilts the
outer axis's rotation plane away from horizontal, so some of the outer axis's authority
stops pointing sideways. At β = 22.5°, `cos β = 0.924`, and:

```text
asin(sin 22.5° · cos 22.5°) = asin(0.3536) = 20.705°
```

against a linear prediction of 22.5° — a shortfall of 1.795°, exactly as measured. The
error lives entirely in the outer axis's component; the inner axis's component
(`asin(sin β)` = β) is exact at any deflection.

Growth with deflection, along the diagonal:

| Deflection (α = β) | Exact `right` | Linear `right` | Error |
|---|---|---|---|
| 0° | 0.000° | 0.000° | 0.000° |
| 11.25° | 11.031° | 11.250° | 0.219° |
| 22.5° | 20.705° | 22.500° | 1.795° |

Roughly quadratic, negligible below about 10° and material at the limit.

### Which map to use where

| Use | Map | Why |
|---|---|---|
| Firmware mixer | Linear | Cheap, invertible, and it sits inside a closed loop that absorbs the error |
| Dashboard display | Exact | It must show where the thrust actually points |
| Calibration | Exact | Errors here become permanent config values |
| Levelling controller | Exact | It builds a thrust direction directly, so there is nothing to approximate |

The firmware choice is defensible because the attitude loop measures the *result*: a
1.8° discrepancy at full deflection appears as a small gain error the integrator
removes. It is **not** defensible for open-loop use, and the dashboard never uses it
for anything an operator reads.

`solve_body_tilt()` exists specifically so the dashboard can reproduce the linear map
when comparing against firmware behaviour.

## 4. Reachable workspace

Four constraints bound a tilt request, and every one of them is linear in a scale
factor applied to that request:

```text
|tilt_outer| ≤ tilt_limit                                   mechanical stop
|tilt_inner| ≤ tilt_limit                                   mechanical stop
servo_outer within its usable window                        servo travel and pulse range
servo_inner within its usable window                        servo travel and pulse range
```

Because they are all linear, the largest reachable fraction of any request is just the
tightest of the four. That is what `max_scale()` computes, and it is why an
unreachable request is **scaled**, never clipped per-axis: scaling preserves the
*direction* of the thrust vector, while clipping one axis alone would swing the thrust
somewhere the caller never asked for. Direction matters far more than magnitude here.

With the current config the workspace is the **full ±22.5° square**:

```text
        tilt_inner
            │
   +22.5 ┌──┼──┐
         │  │  │
   ──────┼──┼──┼────── tilt_outer
         │  │  │
   −22.5 └──┼──┘
        −22.5  +22.5
```

- Smallest tilt available in every direction: **22.5°** (along the axes)
- Largest tilt available in any direction: **31.82°** (at the corners, = 22.5·√2)

The corner (22.5, 22.5) is reachable but exactly consumes the inner servo's ±90°.
Reduce the inner servo's travel at all and the corners get cut off, turning the square
into an octagon. The dashboard traces the real outline by casting a ray per azimuth
rather than assuming a shape, so a config change of that kind shows up immediately on
the Arms page.

**`uniform_tilt_limit()` returns 22.5°**, the smallest radius over all azimuths. That
is the honest number to budget control authority against. Anything larger is only
reachable along some directions, so a controller sized for the corner value would
saturate asymmetrically and pull the thrust off-axis.

### Headroom

`describe_gimbal()` reports how much servo travel is spare after the envelope's demands
are met:

| Axis | Window | Needed | Headroom |
|---|---|---|---|
| outer | ±90° | 45° | **45°** |
| inner | ±90° | 90° | **0°** |

The outer axis has room. The inner axis has none, and cannot — the coupling term makes
its requirement exactly equal to the servo's travel.

The practical consequence: **any `trim_inner` reduces reachable tilt on one side,
one degree for one degree.** There is no way around it short of changing the gear
ratio, lowering the tilt limit, or re-engineering the linkage so the inner servo rides
on the outer ring (`coupling = 0`).

This is not a bug and the software handles it correctly — `max_scale()` reports the
reduced envelope and the dashboard displays it. But it means the inner servo horns
should be mechanically centred as accurately as possible, using trim only for the
residual. The Setup page's centring tool exists for exactly this.

## 5. The levelling law

To hold thrust at world vertical while the airframe moves, an arm must point its
thrust along the world's up direction expressed in body frame. That vector is the third
row of the body-to-world rotation:

```text
up_body = ( sin(pitch),  −sin(roll) · cos(pitch),  −cos(roll) · cos(pitch) )
```

A level airframe gives `(0, 0, −1)`. Nose up leans the required thrust forward; right
side down leans it left. Values:

| roll | pitch | up in body frame | lean forward | lean right |
|---|---|---|---|---|
| 0° | 0° | (0.000, 0.000, −1.000) | 0.000° | 0.000° |
| 10° | 0° | (0.000, −0.174, −0.985) | 0.000° | −10.000° |
| 0° | 10° | (0.174, 0.000, −0.985) | 10.000° | 0.000° |
| −15° | 8° | (0.139, 0.256, −0.957) | 8.000° | 14.851° |

The last row is worth noting: a −15° roll asks for 14.851° of right lean, not 15°. The
lean angles are not simply the negated Euler angles — the two rotations interact, and
the vector formulation gets it right where subtracting angles would not.

The bench controller feeds this through two knobs:

```text
lead_roll  = sign_roll  · (roll  + lead_time · roll_rate)
lead_pitch = sign_pitch · (pitch + lead_time · pitch_rate)

up     = world_up_in_body(lead_roll, lead_pitch)
target = up · gain, blended toward (0, 0, −1) as gain → 0
```

- `lead_time_s` extrapolates the attitude forward to offset servo lag. It is a **time**,
  not an abstract gain, so it can be set from a measured step response.
- `level_gain` blends from leaving the gimbals with the airframe (0) to holding thrust
  at true world vertical (1).

At gain 1 and zero lead the commanded lean is exactly the true world-up direction, which
is asserted directly in `tests/test_controller.py`.

## 5.1 Opposing linear acceleration

The Accel page uses a different law on the same host loop. ArduPilot IMUs report
specific force in body NED, so a vehicle at rest reads approximately gravity, not
zero. Gravity in body frame is the opposite of the levelling vector:

```text
g_down = −world_up_in_body(roll, pitch)
a_lin  = imu_g − g_down
```

A static tilt therefore cancels. A real shove remains. The gimbals then lean motor
thrust against the horizontal part:

```text
lean_forward = −gain · a_lin_x     # +X accel (forward shove) → aft lean
lean_right   = −gain · a_lin_y     # +Y accel (right shove)  → left lean
```

`gain` is in degrees per g. Vertical linear accel is ignored — this loop does not
modulate motor RPM. The same envelope cap as levelling applies. Signs and the rest-
is-not-a-shove case are asserted in `tests/test_controller.py`.

## 6. Yaw by tangential tilt

Yaw has two available sources. Differential thrust within each coaxial pair is the
cheap one but has limited authority. Leaning all four thrust vectors tangentially is
the interesting one, and it is exact.

For each arm at azimuth ψ, lean its thrust along the tangential direction:

```text
forward = −sin ψ
right   =  cos ψ
```

For the four arms:

| Arm | ψ | forward | right |
|---|---|---|---|
| North | 0° | 0 | +1 |
| East | 90° | −1 | 0 |
| South | 180° | 0 | −1 |
| West | 270° | +1 | 0 |

The four side forces sum to zero — they are equal in magnitude and point around a
circle — while their moments about body Z all have the same sign and add. So the
pattern produces pure yaw with no net force and no roll or pitch moment.
`test_kinematics.py` asserts both halves of that claim numerically.

The two yaw sources are blended by configuration rather than one being chosen; see
[Control](04-control.md).

## Testing

| File | Covers |
|---|---|
| `tests/test_kinematics.py` | Every relationship above, plus edge cases and error bounds |
| `tests/golden_kinematics.json` | Reference values, regenerated from the Python module |
| `web/src/lib/kinematics.test.ts` | Asserts the TypeScript port matches the golden vectors |

The golden vectors are the mechanism that keeps a sign flip from landing in one
language only. Any future C++ mixer should be checked against the same file.

Notable tests, because they encode facts rather than just exercising code:

- `test_body_tilt_means_the_same_thing_on_every_arm` — the mount-yaw transform is right
- `test_linear_map_error_is_bounded` — pins the 1.795° figure
- `test_outer_axis_loses_cosine_of_inner_deflection` — explains *why*
- `test_inner_servo_needs_full_travel_at_the_corners` — pins the headroom situation
- `test_trim_eats_into_reach` — asserts trim costs envelope, deliberately
- `test_yaw_tilt_pattern_cancels_force_and_produces_moment` — the yaw claim above
