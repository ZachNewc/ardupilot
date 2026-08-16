# Control

## The available actuators

Eight motors and eight servos, giving these physical effects:

| Actuator pattern | Produces |
|---|---|
| All motors up/down together | Vertical thrust |
| Differential thrust, opposite arms | Roll and pitch moments |
| Differential thrust within a coaxial pair | Yaw moment (reaction torque) |
| All gimbals leaned the same way | Lateral force, body attitude unchanged |
| All gimbals leaned tangentially | Yaw moment, no net force |
| Gimbals leaned differentially | Mostly nothing useful; see below |

## Why gimbals cannot control attitude

This has to be stated plainly because building the controller on a wrong assumption
here produces an airframe that fights itself.

Each rotor sits in the plane containing the centre of mass. Tilting its thrust produces
a horizontal force **in that plane**. The moment of a force about an axis is the cross
product of the lever arm with the force; for a horizontal force applied at a point in
the centre-of-mass plane, the lever arm about the roll and pitch axes is zero. So the
moment about roll and pitch is zero.

Yaw is different: the lever arm from the centre to the hub is horizontal, and a
horizontal tangential force at that hub has a genuine moment about the vertical axis.
That is why the tangential pattern works for yaw and nothing analogous works for roll
or pitch.

So:

- **Roll and pitch authority comes from differential thrust.** Same as a conventional
  quad. ArduPilot's existing attitude controller handles it and should not be modified.
- **Vectoring adds a lateral force channel and a yaw channel.** These are additions on
  top of the normal quad problem, not replacements for it.

The one caveat: real hardware is not ideal. Rotors may sit slightly above or below the
centre-of-mass plane, and gimbal mass shifting produces small inertial effects. These
are second-order and must not be relied on for control.

## Where each loop lives

| Loop | Location | Rate | Status |
|---|---|---|---|
| Rate (gyro to motor) | ArduPilot `AC_AttitudeControl` | 400 Hz | Exists, unmodified |
| Attitude (angle to rate) | ArduPilot `AC_AttitudeControl` | 400 Hz | Exists, unmodified |
| Motor mixing | ArduPilot `AP_MotorsMatrix` | 400 Hz | Exists, needs coaxial config |
| **Thrust-vector mixing** | **New AP_Motors backend** | **400 Hz** | **Not written** |
| Position and velocity | ArduPilot `AC_PosControl` | 100 Hz | Exists, needs a lateral-force path |
| Bench levelling demo | Dashboard, host | 25 Hz | Exists, bench only |

Only one piece is genuinely missing: the mixer that turns a lateral-force demand into
eight servo positions. [Firmware](05-firmware.md) covers its design.

## Why the host-side loop cannot fly the vehicle

The dashboard has a working levelling controller. It holds thrust at world vertical
while you tilt the rig by hand, and it is genuinely useful. It also cannot ever become
flight stabilisation, for two independent reasons.

**Mechanical.** It commands lateral force. Lateral force does not stabilise attitude, per
the section above. Even with zero latency it would be solving the wrong problem.

**Latency.** The path is Python, over MAVLink, over USB serial, at 25 Hz. Round-trip
latency is tens of milliseconds and not bounded. Attitude control needs hundreds of
hertz with a deterministic budget. A 25 Hz loop with variable delay in an attitude
path is unstable, not merely sluggish.

Both reasons are in the module docstring of
[`controller.py`](../dashboard/server/controller.py) so nobody discovers this by
reading only the code. The Stabilize page in the dashboard says the same thing on
screen.

What the bench loop *is* good for:

- Confirming every sign in the config is right, at low speed, with props off
- Seeing the levelling behaviour and building intuition for it
- Measuring servo lag, which sets `lead_time_s`
- Establishing gimbal centres and trims
- Proving the kinematics are correct before committing them to firmware

That last point is the real value: the firmware mixer will use the same relationships,
already validated.

## The levelling law

Derived in [Kinematics §5](03-kinematics.md#5-the-levelling-law). Summarised:

```text
lead_roll  = sign_roll  · (roll  + lead_time · roll_rate)
lead_pitch = sign_pitch · (pitch + lead_time · pitch_rate)

up      = world_up_in_body(lead_roll, lead_pitch)
target  = blend(body_up, up, level_gain)
forward, right = lean_of_vector(target)
clamp magnitude to tilt_cap
```

The three knobs:

| Knob | Range | Meaning |
|---|---|---|
| `level_gain` | 0 to 1.5 | 0 leaves gimbals with the airframe, 1 holds true world vertical |
| `lead_time_s` | 0 to 0.5 | Seconds of attitude extrapolation, to offset servo lag |
| `max_tilt_fraction` | 0.05 to 1 | Fraction of the uniform tilt limit the loop may use |

`invert_roll` and `invert_pitch` are for establishing signs on the bench. Once they are
right they should be folded into the per-axis `sign` values in the config, so the
controller settings describe tuning and the config describes hardware.

The magnitude clamp uses `uniform_tilt_limit()` — the smallest tilt available in every
direction — scaled by `max_tilt_fraction`. Scaling both components together preserves
the commanded direction when the request saturates.

## The output arbiter

Several dashboard features can drive servos: manual sliders, the aim pad, the levelling
loop, the centring command. If two of them write at once, the servos jitter between
targets and the cause is invisible.

[`outputs.py`](../dashboard/server/outputs.py) makes that impossible. One owner at a
time, identified by name, with a generation number:

```text
generation = arbiter.acquire(OWNER_STABILIZE)
...
if not arbiter.write(targets, generation):
    break          # somebody else took ownership; stop immediately
```

A stale writer's generation no longer matches, so its writes are rejected rather than
silently interleaved. The current owner appears in the state snapshot and the dashboard
displays it, which turns "the servos are doing something unexpected" into a visible
fact.

Two safety behaviours fall out of this:

- Closing the last browser tab releases live aiming and stops the levelling loop. A
  closed tab must not leave servos chasing a stale drag target.
- The Stop control takes ownership and centres everything, so it works regardless of
  what was running.

## Planned flight control

Design intent, not implemented. Recorded here so the firmware work has a target.

**Attitude.** Unmodified ArduPilot. Roll, pitch and yaw via differential thrust. The
frame is a coaxial quad in plus configuration; ArduPilot supports this natively via
`FRAME_CLASS`/`FRAME_TYPE` with eight motors.

**Attitude reference.** Level by default. A configurable bias allows a deliberate lean
at higher speeds, where leaning into the airflow reduces drag on the airframe. The bias
is a target attitude offset, not a mixer change.

**Translation.** Lateral acceleration demand from `AC_PosControl` routes to gimbal lean
rather than to an attitude change. This is the new capability and the reason the mixer
has to exist.

**Yaw.** Blended between differential thrust within coaxial pairs and tangential gimbal
tilt, with the blend configurable. Reaction-torque yaw is immediate but limited and
disturbs thrust; tangential tilt has more authority and disturbs nothing, but is limited
by servo bandwidth. A blend gets the fast response from one and the authority from the
other.

**Saturation.** When a lateral demand exceeds the gimbal envelope, the mixer must scale
the whole vector rather than clip per-axis, for the same reason `max_scale()` does — a
clipped vector points somewhere that was never commanded. Priority when servos
saturate is attitude first, translation second: losing translation authority is a
degraded flight, losing attitude authority is a crash.

## Open questions

Genuinely undecided, listed in [Roadmap](10-roadmap.md):

- Yaw blend ratio between reaction torque and tangential tilt
- Whether gimbal lean should feed forward from the attitude controller's lateral demand
  or sit under position control only
- Servo bandwidth, measured — sets the achievable mixer rate and `lead_time_s`
- Whether gimbal inertia is significant enough to need feed-forward compensation
