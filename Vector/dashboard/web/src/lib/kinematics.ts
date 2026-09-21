/**
 * Gimbal kinematics, ported from `server/kinematics.py`.
 *
 * The UI needs these to preview a drag before the servos move and to draw the
 * reachable envelope, so the maths exists in both languages. To stop the two
 * drifting apart, `kinematics.test.ts` checks this file against the golden
 * vectors that the Python test suite generates. A sign flip in one language and
 * not the other fails the build.
 *
 * Keep the structure parallel to the Python module: same function names, same
 * argument order, same conventions. Angles are degrees; body frame is ArduPilot's
 * (X forward, Y right, Z down).
 */

import type { ArmConfig, AxisConfig, GimbalConfig } from './types'

export interface AxisSolution {
  name: string
  channel: number
  tiltDeg: number
  servoDeg: number
  pwmUs: number
  atLimit: boolean
}

export interface GimbalSolution {
  outer: AxisSolution
  inner: AxisSolution
  requested: [number, number]
  scale: number
  clamped: boolean
}

export const clamp = (value: number, low: number, high: number): number =>
  Math.max(low, Math.min(high, value))

const rad = (deg: number): number => (deg * Math.PI) / 180
const deg = (r: number): number => (r * 180) / Math.PI

/* ------------------------------------------------------------ servo window -- */

export const degToUs = (axis: AxisConfig, d: number): number =>
  Math.round(axis.centerUs + d * axis.usPerDeg)

export const usToDeg = (axis: AxisConfig, us: number): number =>
  (us - axis.centerUs) / axis.usPerDeg

export function clampUs(axis: AxisConfig, us: number): number {
  const travelLo = degToUs(axis, -axis.servoLimitDeg)
  const travelHi = degToUs(axis, axis.servoLimitDeg)
  const lo = Math.max(axis.minUs, Math.min(travelLo, travelHi))
  const hi = Math.min(axis.maxUs, Math.max(travelLo, travelHi))
  return Math.round(clamp(us, lo, hi))
}

/** Usable travel: the tighter of the pulse window and the declared mechanical travel. */
export function servoDegWindow(axis: AxisConfig): [number, number] {
  return [
    Math.max(-axis.servoLimitDeg, usToDeg(axis, axis.minUs)),
    Math.min(axis.servoLimitDeg, usToDeg(axis, axis.maxUs)),
  ]
}

/* ---------------------------------------------------------- tilt <-> servo -- */

export const outerDriveDeg = (g: GimbalConfig, tiltOuter: number): number =>
  g.outer.sign * g.gearRatio * tiltOuter

export function servoDegForTilt(
  g: GimbalConfig,
  tiltOuter: number,
  tiltInner: number,
): [number, number] {
  const driveOuter = outerDriveDeg(g, tiltOuter)
  return [
    driveOuter + g.outer.trimDeg,
    g.inner.sign * g.gearRatio * tiltInner + g.coupling * driveOuter + g.inner.trimDeg,
  ]
}

export function tiltForServoDeg(
  g: GimbalConfig,
  outerDeg: number,
  innerDeg: number,
): [number, number] {
  const driveOuter = outerDeg - g.outer.trimDeg
  return [
    driveOuter / (g.outer.sign * g.gearRatio),
    (innerDeg - g.inner.trimDeg - g.coupling * driveOuter) / (g.inner.sign * g.gearRatio),
  ]
}

export function tiltForPwm(g: GimbalConfig, outerUs: number, innerUs: number): [number, number] {
  return tiltForServoDeg(g, usToDeg(g.outer, outerUs), usToDeg(g.inner, innerUs))
}

/* ------------------------------------------------------------ reachability -- */

function maxScaleForBound(coeff: number, offset: number, low: number, high: number): number {
  if (coeff === 0) return low <= offset && offset <= high ? 1 : 0
  const atLow = (low - offset) / coeff
  const atHigh = (high - offset) / coeff
  const lower = Math.min(atLow, atHigh)
  const upper = Math.max(atLow, atHigh)
  // Zero scale already out of bounds means a trim is the problem, and pulling the
  // request back cannot fix it.
  if (lower > 0 || upper < 0) return 0
  return Math.min(1, upper)
}

/** Fraction of the requested tilt that is reachable, in [0, 1]. */
export function maxScale(g: GimbalConfig, tiltOuter: number, tiltInner: number): number {
  const limit = g.tiltLimitDeg
  const [outerLow, outerHigh] = servoDegWindow(g.outer)
  const [innerLow, innerHigh] = servoDegWindow(g.inner)

  const driveOuter = outerDriveDeg(g, tiltOuter)
  const innerCoeff = g.inner.sign * g.gearRatio * tiltInner + g.coupling * driveOuter

  return Math.min(
    maxScaleForBound(tiltOuter, 0, -limit, limit),
    maxScaleForBound(tiltInner, 0, -limit, limit),
    maxScaleForBound(driveOuter, g.outer.trimDeg, outerLow, outerHigh),
    maxScaleForBound(innerCoeff, g.inner.trimDeg, innerLow, innerHigh),
  )
}

/**
 * Resolve a tilt request into pulse widths, pulling it inside the envelope if needed.
 *
 * Both axes scale together so the thrust direction is preserved; clipping one axis
 * alone would swing the thrust somewhere the caller never asked for.
 */
export function solve(g: GimbalConfig, tiltOuter: number, tiltInner: number): GimbalSolution {
  const scale = maxScale(g, tiltOuter, tiltInner)
  const achievedOuter = tiltOuter * scale
  const achievedInner = tiltInner * scale
  const [outerDeg, innerDeg] = servoDegForTilt(g, achievedOuter, achievedInner)

  const axisSolution = (
    name: string,
    axis: AxisConfig,
    tilt: number,
    servo: number,
  ): AxisSolution => {
    const [low, high] = servoDegWindow(axis)
    const margin = 0.05
    return {
      name,
      channel: axis.channel,
      tiltDeg: tilt,
      servoDeg: servo,
      pwmUs: clampUs(axis, degToUs(axis, servo)),
      atLimit: servo <= low + margin || servo >= high - margin,
    }
  }

  return {
    outer: axisSolution('outer', g.outer, achievedOuter, outerDeg),
    inner: axisSolution('inner', g.inner, achievedInner, innerDeg),
    requested: [tiltOuter, tiltInner],
    scale,
    clamped: scale < 0.999,
  }
}

/* ------------------------------------------------------ body frame <-> gimbal */

/** Exact thrust direction in body frame. A centred gimbal gives (0, 0, -1). */
export function thrustUnitVector(
  mountYawDeg: number,
  tiltOuter: number,
  tiltInner: number,
): [number, number, number] {
  const a = rad(tiltOuter)
  const b = rad(tiltInner)
  const x0 = -Math.sin(b)
  const y0 = Math.cos(b) * Math.sin(a)
  const z0 = -Math.cos(b) * Math.cos(a)

  const m = rad(mountYawDeg)
  const cm = Math.cos(m)
  const sm = Math.sin(m)
  return [x0 * cm - y0 * sm, x0 * sm + y0 * cm, z0]
}

/**
 * Linearised body tilt, matching what the mixer uses.
 *
 * Exact only to first order. See `thrustLean` for the exact version and
 * `docs/03-kinematics.md` for the measured error.
 */
export function gimbalToBodyTilt(
  mountYawDeg: number,
  tiltOuter: number,
  tiltInner: number,
): [number, number] {
  const m = rad(mountYawDeg)
  const cm = Math.cos(m)
  const sm = Math.sin(m)
  return [-tiltInner * cm - tiltOuter * sm, -tiltInner * sm + tiltOuter * cm]
}

/** Inverse of `gimbalToBodyTilt`. Returns [tiltOuter, tiltInner]. */
export function bodyTiltToGimbal(
  mountYawDeg: number,
  forwardDeg: number,
  rightDeg: number,
): [number, number] {
  const m = rad(mountYawDeg)
  const cm = Math.cos(m)
  const sm = Math.sin(m)
  return [-forwardDeg * sm + rightDeg * cm, -(forwardDeg * cm + rightDeg * sm)]
}

/** Exact body-frame lean of the thrust vector, in degrees. */
export function thrustLean(
  mountYawDeg: number,
  tiltOuter: number,
  tiltInner: number,
): [number, number] {
  const [x, y] = thrustUnitVector(mountYawDeg, tiltOuter, tiltInner)
  return [deg(Math.asin(clamp(x, -1, 1))), deg(Math.asin(clamp(y, -1, 1)))]
}

/** Exact inverse of `thrustLean`. Returns [tiltOuter, tiltInner]. */
export function gimbalForThrustLean(
  mountYawDeg: number,
  forwardDeg: number,
  rightDeg: number,
): [number, number] {
  let x = Math.sin(rad(forwardDeg))
  let y = Math.sin(rad(rightDeg))
  let horizontal = x * x + y * y
  if (horizontal > 1) {
    const norm = Math.sqrt(horizontal)
    x /= norm
    y /= norm
    horizontal = 1
  }
  const z = -Math.sqrt(Math.max(0, 1 - horizontal))

  const m = rad(mountYawDeg)
  const cm = Math.cos(m)
  const sm = Math.sin(m)
  const x0 = x * cm + y * sm
  const y0 = -x * sm + y * cm

  return [deg(Math.atan2(y0, -z)), deg(-Math.asin(clamp(x0, -1, 1)))]
}

/** Axis angles that point the thrust along a body-frame vector (need not be unit). */
export function gimbalForThrustVector(
  mountYawDeg: number,
  vector: [number, number, number],
): [number, number] {
  const [vx, vy, vz] = vector
  const norm = Math.sqrt(vx * vx + vy * vy + vz * vz)
  if (norm <= 0) return [0, 0]
  const x = vx / norm
  const y = vy / norm
  const z = vz / norm

  const m = rad(mountYawDeg)
  const cm = Math.cos(m)
  const sm = Math.sin(m)
  const x0 = x * cm + y * sm
  const y0 = -x * sm + y * cm

  return [deg(Math.atan2(y0, -z)), deg(-Math.asin(clamp(x0, -1, 1)))]
}

/** Body-frame lean angles of a thrust direction, in degrees. */
export function leanOfVector(vector: [number, number, number]): [number, number] {
  const [x, y, z] = vector
  const norm = Math.sqrt(x * x + y * y + z * z)
  if (norm <= 0) return [0, 0]
  return [deg(Math.asin(clamp(x / norm, -1, 1))), deg(Math.asin(clamp(y / norm, -1, 1)))]
}

/**
 * The world's up direction expressed in body frame — the entire levelling law.
 *
 * Level gives (0, 0, -1). Nose up leans the required thrust forward; right side
 * down leans it left.
 */
export function worldUpInBody(rollDeg: number, pitchDeg: number): [number, number, number] {
  const roll = rad(rollDeg)
  const pitch = rad(pitchDeg)
  return [Math.sin(pitch), -Math.sin(roll) * Math.cos(pitch), -Math.cos(roll) * Math.cos(pitch)]
}

/* ----------------------------------------------------------- arm-level API -- */

export function solveThrustLean(
  arm: ArmConfig,
  forwardDeg: number,
  rightDeg: number,
): GimbalSolution {
  const [outer, inner] = gimbalForThrustLean(arm.mountYawDeg, forwardDeg, rightDeg)
  return solve(arm.gimbal, outer, inner)
}

export function solveThrustVector(
  arm: ArmConfig,
  vector: [number, number, number],
): GimbalSolution {
  const [outer, inner] = gimbalForThrustVector(arm.mountYawDeg, vector)
  return solve(arm.gimbal, outer, inner)
}

export function solveBodyTilt(arm: ArmConfig, forwardDeg: number, rightDeg: number): GimbalSolution {
  const [outer, inner] = bodyTiltToGimbal(arm.mountYawDeg, forwardDeg, rightDeg)
  return solve(arm.gimbal, outer, inner)
}

/** Rotor hub position in body frame, metres. Azimuth is from body +X toward +Y. */
export function armPosition(arm: ArmConfig, armLengthM: number): [number, number, number] {
  const psi = rad(arm.azimuthDeg)
  return [armLengthM * Math.cos(psi), armLengthM * Math.sin(psi), 0]
}

/* ----------------------------------------------------------- levelling law -- */

export interface LevelLaw {
  levelGain: number
  leadTimeS: number
  maxTiltFraction: number
  invertRoll: boolean
  invertPitch: boolean
  tiltCapDeg: number
}

export interface LeanCommand {
  forward: number
  right: number
  saturated: boolean
}

/**
 * The bench levelling law, mirroring `LevelController.desired_lean`.
 *
 * The UI runs this locally so the Stabilize page can show what the controller
 * would command at the current attitude even while it is switched off. The
 * authoritative copy is the Python one; this exists to make the law visible.
 */
export function desiredLean(
  law: LevelLaw,
  rollDeg: number,
  pitchDeg: number,
  rollRate: number,
  pitchRate: number,
): LeanCommand {
  const signRoll = law.invertRoll ? -1 : 1
  const signPitch = law.invertPitch ? -1 : 1

  const leadRoll = signRoll * (rollDeg + law.leadTimeS * rollRate)
  const leadPitch = signPitch * (pitchDeg + law.leadTimeS * pitchRate)

  const up = worldUpInBody(leadRoll, leadPitch)
  const gain = law.levelGain
  const target: [number, number, number] = [
    up[0] * gain,
    up[1] * gain,
    -1 + (up[2] + 1) * gain,
  ]

  const [forward, right] = leanOfVector(target)
  const cap = law.tiltCapDeg * clamp(law.maxTiltFraction, 0, 1)
  const magnitude = Math.hypot(forward, right)
  if (magnitude > cap && magnitude > 0) {
    const scale = cap / magnitude
    return { forward: forward * scale, right: right * scale, saturated: true }
  }
  return { forward, right, saturated: false }
}

/** Unit gravity (down) in body frame. Level is (0, 0, +1). */
export function gravityDownInBody(rollDeg: number, pitchDeg: number): [number, number, number] {
  const [ux, uy, uz] = worldUpInBody(rollDeg, pitchDeg)
  return [-ux, -uy, -uz]
}

/**
 * Gravity-compensated linear acceleration in body frame, in g.
 *
 * Mirrors `linear_accel_g` in kinematics.py. A vehicle at rest reads ~1 g down
 * on the IMU, not zero; subtracting gravity leaves the shove.
 */
export function linearAccelG(
  accelG: [number, number, number],
  rollDeg: number,
  pitchDeg: number,
): [number, number, number] {
  const [gx, gy, gz] = gravityDownInBody(rollDeg, pitchDeg)
  return [accelG[0] - gx, accelG[1] - gy, accelG[2] - gz]
}

export interface AccelLaw {
  accelGainDegG: number
  accelDeadbandG: number
  invertAccelX: boolean
  invertAccelY: boolean
  tiltCapDeg: number
  maxTiltFraction: number
}

/**
 * Lean motor thrust against horizontal linear acceleration.
 *
 * Mirrors `oppose_horizontal_accel` so the Accel page can preview the command
 * while the loop is off. Vertical accel is ignored.
 */
export function desiredAccelLean(
  law: AccelLaw,
  accelG: [number, number, number],
  rollDeg: number,
  pitchDeg: number,
): LeanCommand {
  const [linX, linY] = linearAccelG(accelG, rollDeg, pitchDeg)
  const mag = Math.hypot(linX, linY)
  const band = Math.max(0, law.accelDeadbandG)
  if (mag <= band) {
    return { forward: 0, right: 0, saturated: false }
  }

  const scale = (mag - band) / mag
  const hx = linX * scale
  const hy = linY * scale
  let forward = -law.accelGainDegG * hx
  let right = -law.accelGainDegG * hy
  if (law.invertAccelX) forward = -forward
  if (law.invertAccelY) right = -right

  const cap = law.tiltCapDeg * clamp(law.maxTiltFraction, 0, 1)
  const magnitude = Math.hypot(forward, right)
  if (magnitude > cap && magnitude > 0) {
    const shrink = cap / magnitude
    return { forward: forward * shrink, right: right * shrink, saturated: true }
  }
  return { forward, right, saturated: false }
}
