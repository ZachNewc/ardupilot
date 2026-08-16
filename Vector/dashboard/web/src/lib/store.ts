/**
 * The dashboard's single source of truth.
 *
 * One WebSocket, one reactive state object, one `send()`. Pages read from `store`
 * and call `send()`; nothing else opens a socket or keeps its own copy of vehicle
 * state, so there is only one place where the UI and the vehicle can disagree.
 *
 * Reconnection is automatic and silent: a dashboard left open across a server
 * restart picks the link back up on its own.
 */

import { computed, reactive, readonly } from 'vue'

import type {
  ArmState,
  CommandInfo,
  EventItem,
  SerialPort,
  ServerMessage,
  StateMessage,
  VehicleConfigMessage,
} from './types'

const RECONNECT_DELAY_MS = 1200
const ACK_HISTORY = 120

export interface AckEntry {
  id: number
  at: number
  command: string
  ok: boolean
  text: string
}

interface Store {
  /** Browser-to-server socket state, distinct from the server-to-vehicle link. */
  socketOpen: boolean
  config: VehicleConfigMessage | null
  state: StateMessage | null
  ports: SerialPort[]
  acks: AckEntry[]
  pending: Record<string, boolean>
}

const store = reactive<Store>({
  socketOpen: false,
  config: null,
  state: null,
  ports: [],
  acks: [],
  pending: {},
})

let socket: WebSocket | null = null
let reconnectTimer: number | undefined
let ackSeq = 0
let requestSeq = 0

function socketUrl(): string {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${scheme}://${window.location.host}/ws`
}

function handle(message: ServerMessage): void {
  // Some commands reply with data rather than an ack -- `get_config` sends a config
  // message. Clearing here rather than in the ack branch means any reply that names
  // its command releases the control that issued it.
  const named = (message as { command?: string }).command
  if (named) store.pending[named] = false

  switch (message.type) {
    case 'config':
      store.config = message.data
      break
    case 'state':
      store.state = message.data
      break
    case 'ports':
      store.ports = message.data
      break
    case 'ack': {
      store.pending[message.command] = false
      ackSeq += 1
      store.acks.unshift({
        id: ackSeq,
        at: Date.now() / 1000,
        command: message.command,
        ok: message.ok,
        text: message.message || (message.ok ? 'ok' : 'failed'),
      })
      if (store.acks.length > ACK_HISTORY) store.acks.length = ACK_HISTORY
      break
    }
  }
}

export function connect(): void {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return
  }

  socket = new WebSocket(socketUrl())

  socket.onopen = () => {
    store.socketOpen = true
  }

  socket.onclose = () => {
    store.socketOpen = false
    socket = null
    // Acks for in-flight commands will never arrive now, so drop the pending flags
    // rather than leaving controls disabled until the page is reloaded.
    store.pending = {}
    window.clearTimeout(reconnectTimer)
    reconnectTimer = window.setTimeout(connect, RECONNECT_DELAY_MS)
  }

  socket.onerror = () => {
    // onclose always follows, and that is where the retry lives.
  }

  socket.onmessage = (event: MessageEvent<string>) => {
    try {
      handle(JSON.parse(event.data) as ServerMessage)
    } catch {
      // A malformed frame is not worth tearing the session down for.
    }
  }
}

/**
 * Send a command.
 *
 * `quiet` is for high-rate streams such as a drag on the aim pad: the server does
 * not acknowledge those, so tracking them as pending would leave the UI stuck
 * showing work in flight forever.
 */
export function send(command: string, args: Record<string, unknown> = {}, quiet = false): void {
  if (!socket || socket.readyState !== WebSocket.OPEN) return
  requestSeq += 1
  if (!quiet) store.pending[command] = true
  socket.send(JSON.stringify({ command, id: requestSeq, ...args }))
}

export const isPending = (command: string): boolean => store.pending[command] === true

/**
 * A plain, detached copy of something read out of the store.
 *
 * Needed because `structuredClone` throws `DataCloneError` on a Vue reactive proxy,
 * and everything reachable from `store` is proxied. A form that edits store data has
 * to work on a detached copy, or typing in it would mutate the last frame the server
 * sent and the edit would vanish on the next update.
 *
 * A JSON round-trip rather than `toRaw`: everything in the store arrived as JSON over
 * the socket, so this is lossless here, and it detaches the whole tree rather than
 * just the outermost object.
 */
export function detached<T>(value: T): T {
  return JSON.parse(JSON.stringify(value ?? null)) as T
}

/* ------------------------------------------------------------- selectors --- */

export const vehicleName = computed(() => store.config?.name ?? 'Vector')

export const arms = computed<ArmState[]>(() => store.state?.arms ?? [])

export const liveArms = computed<ArmState[]>(() => arms.value.filter((arm) => arm.live))

export const armById = computed<Record<string, ArmState>>(() =>
  Object.fromEntries(arms.value.map((arm) => [arm.id, arm])),
)

export const configArmById = computed(() =>
  Object.fromEntries((store.config?.arms ?? []).map((arm) => [arm.id, arm])),
)

export const linkUp = computed(() => store.state?.link.connected === true)

export const events = computed<EventItem[]>(() => store.state?.events ?? [])

export const commands = computed<CommandInfo[]>(() => store.config?.commands ?? [])

/**
 * Whether it is safe to command servos right now.
 *
 * A dashboard that lets you drag a slider while nothing is listening teaches you
 * to distrust it, so every control is gated on this rather than on hope.
 */
export const canCommand = computed(
  () => store.socketOpen && linkUp.value && liveArms.value.length > 0,
)

export const commandBlockedReason = computed<string | null>(() => {
  if (!store.socketOpen) return 'Dashboard is not connected to the server'
  if (!linkUp.value) return 'No link to the flight controller'
  if (liveArms.value.length === 0) return 'No arms are marked live in the config'
  return null
})

export const vector = readonly(store)
export default store
