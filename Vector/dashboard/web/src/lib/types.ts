/**
 * Shapes exchanged with the Python server.
 *
 * These mirror `server/config.py` (the `config` message) and `server/state.py`
 * (the `state` message). Keys are camelCase on both sides; the Python `to_dict`
 * helpers do the conversion so nothing has to be renamed here.
 */

export type ArmStatus = 'live' | 'planned' | 'disabled'
export type AxisName = 'outer' | 'inner'
export type MotorName = 'bottom' | 'top'

/* ---------------------------------------------------------------- config ---- */

export interface AxisConfig {
  /** SERVO1..SERVO32. 0 disables this axis: nothing is written to that pin. */
  channel: number
  sign: number
  centerUs: number
  usPerDeg: number
  servoLimitDeg: number
  minUs: number
  maxUs: number
  trimDeg: number
}

export interface GimbalConfig {
  gearRatio: number
  tiltLimitDeg: number
  coupling: number
  outer: AxisConfig
  inner: AxisConfig
}

export interface MotorConfig {
  /** SERVO1..SERVO32. 0 disables this motor: it is not spun and not mapped. */
  channel: number
  spin: 'cw' | 'ccw'
  reversed: boolean
  testSequence: number | null
  /** SERVOn_FUNCTION for this output: 33-40 for Motor1-Motor8. null means unassigned. */
  function: number | null
}

export interface ArmConfig {
  id: string
  label: string
  status: ArmStatus
  live: boolean
  azimuthDeg: number
  mountYawDeg: number
  gimbal: GimbalConfig
  motors: Partial<Record<MotorName, MotorConfig>>
}

export interface FrameConfig {
  layout: string
  noseArm: string
  rotorDiagonalM: number
  armLengthM: number
}

export interface BenchLimits {
  motorPercent: number
  motorSeconds: number
  servoSpeedDegS: number
  defaultServoSpeedDegS: number
  commandRateHz: number
}

export interface BenchControllerConfig {
  mode: string
  levelGain: number
  leadTimeS: number
  maxTiltFraction: number
  invertRoll: boolean
  invertPitch: boolean
}

/** Reachable envelope plus derived geometry, computed server side per arm. */
export interface Workspace {
  gimbal: [number, number][]
  body: [number, number][]
  tiltLimitDeg: number
  gearRatio: number
  coupling: number
  uniformTiltDeg: number
  outerServoNeededDeg: number
  innerServoNeededDeg: number
  outerServoWindowDeg: [number, number]
  innerServoWindowDeg: [number, number]
  outerHeadroomDeg: number
  innerHeadroomDeg: number
}

export interface CommandInfo {
  name: string
  needsLink: boolean
  summary: string
}

/* ------------------------------------------------------------ output map ---- */

/** One pin, and whichever arm role claims it. */
export interface OutputCell {
  channel: number
  owner: string | null
  kind: 'servo' | 'motor' | null
  /** Which output on the CAN-to-PWM node this is, counting from 1. CAN pins only. */
  canOutput?: number
  /**
   * The `OUTx_FUNCTION` the node itself needs on that output. Set on the node, not the
   * flight controller: 50 + channel for a servo, Motor(k+1) = 33 + k for an ESC in
   * RawCommand slot k. CAN pins only.
   */
  nodeFunction?: number
}

/**
 * One hardware timer and the pins it drives, or the CAN-to-PWM node.
 *
 * `mode` is not a setting. A timer group is all DShot or all PWM, so one motor in the
 * group decides it for every pin in the group, and a servo in a DShot group is dead.
 */
export interface OutputGroup {
  name: string
  mode: 'DShot' | 'PWM' | 'CAN'
  outputs: OutputCell[]
}

/** A channel map the hardware will not honour. `fatal` means that output cannot work. */
export interface OutputProblem {
  severity: 'fatal' | 'warning'
  channel: number
  text: string
}

export interface OutputMap {
  groups: OutputGroup[]
  problems: OutputProblem[]
  boardPwmChannels: number
  canServoFirst: number
  /**
   * Motor channels that leave the vehicle as DroneCAN ESC commands. A CAN ESC only
   * takes throttle while the vehicle is soft-armed, so these spin through the motor
   * test, one at a time, rather than together with the onboard set.
   */
  canEscChannels: number[]
}

export interface VehicleConfigMessage {
  name: string
  summary: string
  controller: string
  frame: FrameConfig
  arms: ArmConfig[]
  link: {
    device: string
    baud: number
    escTelemetrySerials: number[]
    escTelemetrySerial: number | null
  }
  benchLimits: BenchLimits
  benchController: BenchControllerConfig
  path: string
  workspaces: Record<string, Workspace>
  commands: CommandInfo[]
  /**
   * What each physical output is, and every way the map disagrees with the board.
   *
   * Travels with the config rather than with telemetry: a map that cannot work is
   * wrong before anything is connected, and Setup is where it gets edited.
   */
  outputMap: OutputMap
  /**
   * The config file exactly as it is on disk, in its native snake_case form.
   *
   * The Setup page edits a copy of this and submits fragments back, so keys the UI
   * does not model survive a round trip. Everything else in the app reads the
   * camelCase view above instead.
   */
  document: Record<string, any>
}

/* ----------------------------------------------------------------- state ---- */

export interface LinkBudget {
  channels: number
  rateHz: number
  bytesPerSecond: number
  capacityBytesPerSecond: number
  utilisation: number
  overBudget: boolean
}

export interface LinkState {
  connected: boolean
  device: string
  baud: number
  systemId: number
  componentId: number
  /** Null means no heartbeat has ever been seen, which is not the same as a stale one. */
  heartbeatAgeS: number | null
  readerAlive: boolean
  updatedAt: number
  budget: LinkBudget
  lastError: string
}

export interface VehicleState {
  armed: boolean
  mode: string
  rollDeg: number
  pitchDeg: number
  yawDeg: number
  rollRateDegS: number
  pitchRateDegS: number
  yawRateDegS: number
  rateMagnitudeDegS: number
  /** IMU specific force in g, body NED. At rest Z is about +1, not 0. */
  accelXg: number
  accelYg: number
  accelZg: number
  /** Age of the newest IMU accel sample. Null before SCALED_IMU has arrived. */
  accelAgeS: number | null
  voltage: number
  current: number
  batteryRemaining: number
  loadPercent: number
  gpsFix: number
  gpsSats: number
  throttle: number
  /** Age of the newest message of any kind. Null before anything has arrived. */
  ageS: number | null
}

export interface AxisState {
  channel: number
  pwm: number
  servoDeg: number
  tiltDeg: number
  limitDeg: number
  servoLimitDeg: number
  atLimit: boolean
}

export interface MotorState {
  channel: number
  pwm: number
  percent: number
  spin: string
  reversed: boolean
  testSequence: number | null
  rpm: number | null
  temperatureC: number | null
  voltage: number | null
  current: number | null
}

/** Where an arm's thrust is pointing: lean angles plus the unit vector they came from. */
export interface ThrustState {
  forwardDeg: number
  rightDeg: number
  vector: [number, number, number]
}

export interface ArmState {
  id: string
  label: string
  status: ArmStatus
  live: boolean
  azimuthDeg: number
  mountYawDeg: number
  axes: Record<AxisName, AxisState>
  motors: Partial<Record<MotorName, MotorState>>
  thrust: ThrustState
}

/** A commanded body-frame lean, in degrees. */
export interface LeanState {
  forward: number
  right: number
  magnitude: number
}

export interface ControllerState {
  active: boolean
  mode: string
  levelGain: number
  leadTimeS: number
  maxTiltFraction: number
  invertRoll: boolean
  invertPitch: boolean
  accelGainDegG: number
  accelDeadbandG: number
  invertAccelX: boolean
  invertAccelY: boolean
  tiltCapDeg: number
  /** What the loop is currently commanding. Zero when it is not running. */
  target: LeanState
  /** What the gyro levelling law would command right now, running or not. */
  preview: LeanState & { saturated: boolean }
  /** What the accel-hold law would command right now, running or not. */
  accelPreview: LeanState & { saturated: boolean }
  /** Gravity-compensated linear accel in g. Zero until an IMU sample has arrived. */
  linearAccelG: { x: number; y: number; z: number; horizontal: number }
  saturated: boolean
  loopHz: number
  updates: number
  lastError: string
}

export interface OutputsState {
  /** Which subsystem currently owns the servo outputs, or null if none does. */
  owner: string | null
  rampActive: boolean
  motorTestActive: boolean
  oscillateActive: boolean
}

export interface EventItem {
  id: number
  /** Epoch seconds, so this can be interleaved with locally sent commands. */
  at: number
  kind: 'command' | 'vehicle' | 'error'
  text: string
}

/** One reporting ESC. Absent from the list entirely until it reports. */
export interface EscState {
  index: number
  label: string
  rpm: number | null
  voltage: number | null
  current: number | null
  temperatureC: number | null
}

export interface StateMessage {
  link: LinkState
  vehicle: VehicleState
  arms: ArmState[]
  escs: EscState[]
  controller: ControllerState
  outputs: OutputsState
  events: EventItem[]
  /** MAVLink message type to receive count. Useful for spotting a missing stream. */
  messageCounts: Record<string, number>
}

/* -------------------------------------------------------------- messages ---- */

/**
 * A reply to one command.
 *
 * `command` echoes what was asked for, which is how the browser clears the pending
 * flag on the right control. `id` is the client's request number, if it sent one.
 */
export interface AckMessage {
  type: 'ack'
  command: string
  id?: number | null
  ok: boolean
  message?: string
  /** Set by commands that change the config, telling every tab to re-read it. */
  reloadConfig?: boolean
}

/**
 * Anything the server sends.
 *
 * Replies to a command carry `command`, whatever their type: `get_config` answers
 * with a config message rather than an ack, and the browser still needs to know
 * which control to release.
 */
export type ServerMessage =
  | { type: 'config'; data: VehicleConfigMessage; command?: string; id?: number | null }
  | { type: 'state'; data: StateMessage }
  | { type: 'ports'; data: SerialPort[]; command?: string; id?: number | null }
  | AckMessage

export interface SerialPort {
  device: string
  label: string
}
