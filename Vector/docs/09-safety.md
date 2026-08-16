# Safety

This is a 0.81 m airframe with eight 7-inch propellers on 2806.5 motors and a 10 Ah 6S
li-ion pack. It can cause serious injury. Read this before powering anything.

## Non-negotiables

1. **Props off for all bench work** except the motor test in
   [§7](08-bench-testing.md#7-motors), and even then the airframe must be restrained and
   everyone clear of the disc.
2. **Never stand in a propeller plane** while the battery is connected. Coaxial pairs mean
   there are two discs per arm, one of them below the arm.
3. **Restrain the airframe** before commanding gimbals or motors. Eight servos slewing
   together produces a real reaction; motors produce a lot more.
4. **Disconnect the battery** before touching wiring. Not "disarmed" — physically
   disconnected.
5. **Never leave a live vehicle unattended** with the dashboard connected.

## Removed hazard: boot-time auto-spin

This tree previously enabled `HAL_SERVO_BENCH_OSCILLATE` in the shared `MatekH743`
hwdef. Three seconds after power-up, with no operator action, the firmware would:

- call `force_safety_off()`, bypassing the safety switch
- soft-arm the vehicle
- run both North motors at ~7% for two seconds

Powering up with props fitted would have spun them without warning. **This has been
removed** and both files restored to upstream.

If you ever see motors spin at boot without a command, stop and find out why before
doing anything else. Nothing in this project should arm the vehicle except an explicit
command.

## Battery

6S2P li-ion, 10 Ah. Li-ion behaves differently from the lithium polymer packs most
multirotor guidance assumes:

| Property | Consequence |
|---|---|
| Much lower peak current | Hard throttle transients cause severe voltage sag, not clean delivery |
| Softer discharge curve | Percentage remaining is a poor indicator; **watch cell voltage** |
| High energy density | A failure is a more energetic fire |
| Damaged cells can vent later | A pack that took an impact is suspect for days |

Limits:

- **3.0 V per cell absolute minimum** (18.0 V pack). Below this the pack is damaged.
- **3.3 V per cell** is a sensible working floor for bench testing.
- Never charge a pack that is swollen, punctured, or was in a crash.
- Charge in a fire-safe container, never unattended.

The Telemetry page shows cell voltage — pack voltage divided by six — precisely because
it is the number that matters and the pack percentage is not.

## Servos

Eight servos on a 20 A SBEC. Failure modes worth knowing:

**Ground loop or missing ground.** The SBEC ground must be bonded to the flight
controller ground. Without a shared reference the signal is meaningless and servos move
randomly. Symptom: jitter with no command.

**Stall current.** A servo driven into a mechanical stop draws stall current
continuously and will overheat. The software prevents commanding past the envelope, but a
mechanical fault can still put a gimbal against a stop. Symptom: a hot servo, buzzing at
rest.

**Brownout under simultaneous slew.** All eight moving at once is the worst-case load.
Sized for, but a marginal connection turns it into a reset.

## Gimbal envelope

The software will not command past ±22.5° of tilt, and that is enforced before anything
else. But the software only knows what the config says.

**Verify the mechanical limits physically.** Move each gimbal by hand through its full
range with power off. If it can reach further than the config allows, that is fine. If it
reaches a stop *before* the config's limit, lower `tilt_limit_deg` until it does not —
otherwise every full-deflection command drives a servo into a stall.

## Propellers

Coaxial counter-rotating pairs, so the two props on an arm turn opposite ways.

- **Fitting a prop the wrong way** produces downward thrust on that motor. With coaxial
  pairs this is easy to get wrong and hard to see. Check every prop against its motor's
  configured direction before fitting.
- **Never test a motor with a prop fitted and the airframe unrestrained.** A single arm
  at low throttle can flip the frame.
- Inspect for nicks and cracks before every run. A 7-inch prop failing at speed sends
  fragments a long way.

## Dashboard behaviour under fault

Designed so that losing something does not leave the vehicle in an unknown state:

| Fault | Behaviour |
|---|---|
| Browser tab closed | Live aiming released, levelling loop stopped |
| Link lost mid-command | Loop exits; the vehicle holds its last commanded position |
| Two features want the servos | Arbiter grants one; the other stops immediately |
| Server killed | Vehicle holds last position. **Servos stay where they were** |
| Bad config edit | Rejected before writing; previous config keeps running |

Note the third row carefully: **the vehicle holds its last commanded position** when the
dashboard goes away. It does not centre itself. If you kill the server with a gimbal at
full deflection, it stays there. Press Center before disconnecting.

## Where the controllers are, and are not

The dashboard's levelling loop is a **bench demonstration**. It runs at 25 Hz over USB
with unbounded latency and it commands lateral force, which does not stabilise attitude.
[Control](04-control.md) explains both reasons in full.

Concretely:

- It **cannot** keep the airframe upright.
- It **cannot** recover from a disturbance.
- It **must not** be relied on for anything but bench visualisation.

No flight-capable thrust-vector mixer exists in this project yet. Do not attempt to fly
the vehicle under vectored control until one has been written and reviewed.

## First flight, when the time comes

Not yet applicable. Recorded now so it is not improvised later.

1. Tethered hover first, props on, at low altitude, over something soft.
2. Gimbals **locked centred** in firmware for the first flights. Prove the vehicle flies
   as a conventional coaxial quad before enabling vectoring at all.
3. Enable vectoring with a small authority limit — `VEC_TILT_MAX` well below 22.5°.
4. Increase authority only after each step is stable and logged.
5. Have a hardware failsafe that centres the gimbals and hands full control to the
   normal attitude controller.

Step 2 is the important one. If the vehicle cannot fly with the gimbals locked, adding
vectoring will not fix it — it will hide the problem behind a second control loop.

## Reporting

If something behaves unexpectedly — a gimbal moving without a command, a motor spinning
at boot, a servo that will not centre — stop, disconnect the battery, and find the cause
before continuing. On a vehicle at this scale, an intermittent fault that is left alone
becomes an injury.
