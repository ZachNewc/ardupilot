<!--
  Setup: edit the vehicle config, and calibrate the servos against it.

  Every field here writes to `Vector/config/vector.json`, which is the single source
  of truth for the server, this UI, and the docs. The server validates the whole
  merged document before writing, so a bad edit is rejected rather than half-applied.

  The page edits a copy of the raw document and submits only the sections that
  changed, which means keys this form does not know about are preserved.
-->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import PanelCard from '../components/PanelCard.vue'
import StatTile from '../components/StatTile.vue'
import StatusPill from '../components/StatusPill.vue'
import { armColor, num, signedDeg, us } from '../lib/format'
import store, { arms, canCommand, detached, isPending, linkUp, send } from '../lib/store'
import type { AxisName, MotorName } from '../lib/types'

type Document = Record<string, any>

/** Working copy of the on-disk document. Edits stay local until Save is pressed. */
const draft = ref<Document>({})
const dirty = ref(false)
const problem = ref<string | null>(null)

function reset() {
  draft.value = detached(store.config?.document) ?? {}
  dirty.value = false
  problem.value = null
}

watch(() => store.config?.document, reset, { immediate: true, deep: false })

function touch() {
  dirty.value = true
  problem.value = null
}

function telemSerialsText(): string {
  const serials = draft.value.link?.esc_telemetry_serials
  if (Array.isArray(serials) && serials.length) {
    return serials.join(', ')
  }
  const single = draft.value.link?.esc_telemetry_serial
  return single == null || single === '' ? '' : String(single)
}

function setTelemSerials(event: Event) {
  if (!draft.value.link) {
    return
  }
  const target = event.target as HTMLInputElement
  draft.value.link.esc_telemetry_serials = target.value
    .split(/[,\s]+/)
    .filter(Boolean)
    .map(Number)
    .filter((n) => Number.isFinite(n))
  delete draft.value.link.esc_telemetry_serial
  touch()
}

/* Channel conflicts are the failure that silently destroys hardware, so the form
   checks for them locally and refuses to submit rather than relying on the server's
   rejection alone. */
const conflicts = computed(() => {
  const owners = new Map<number, string[]>()
  for (const arm of draft.value.arms ?? []) {
    const claims: [number | undefined, string][] = [
      [arm.outer?.channel, `${arm.id}.outer`],
      [arm.inner?.channel, `${arm.id}.inner`],
    ]
    for (const [name, motor] of Object.entries(arm.motors ?? {})) {
      claims.push([(motor as any)?.channel, `${arm.id}.${name}`])
    }
    for (const [channel, who] of claims) {
      if (typeof channel !== 'number' || channel < 1) continue
      owners.set(channel, [...(owners.get(channel) ?? []), who])
    }
  }
  return [...owners.entries()]
    .filter(([, who]) => who.length > 1)
    .map(([channel, who]) => `SERVO${channel}: ${who.join(' and ')}`)
})

function save() {
  if (conflicts.value.length) {
    problem.value = 'Fix the channel conflicts first.'
    return
  }
  send('save_config', {
    updates: {
      vehicle: draft.value.vehicle,
      gimbal_defaults: draft.value.gimbal_defaults,
      arms: draft.value.arms,
      link: draft.value.link,
      bench_limits: draft.value.bench_limits,
      bench_controller: draft.value.bench_controller,
    },
  })
  dirty.value = false
}

/* --------------------------------------------------------------- calibration */

const calArm = ref<string>('')
watch(
  arms,
  (list) => {
    if (!calArm.value && list.length) calArm.value = list.find((a) => a.live)?.id ?? list[0].id
  },
  { immediate: true },
)

const calArmState = computed(() => arms.value.find((arm) => arm.id === calArm.value) ?? null)

const calDraftArm = computed(
  () => (draft.value.arms ?? []).find((arm: any) => arm.id === calArm.value) ?? null,
)

/** Axis geometry with the defaults folded in, matching how the server resolves it. */
function resolved(axis: AxisName): Record<string, any> {
  const defaults = draft.value.gimbal_defaults?.[axis] ?? {}
  return { ...defaults, ...(calDraftArm.value?.[axis] ?? {}) }
}

/**
 * Adopt the servo's current position as the axis' centre.
 *
 * This is the calibration that matters most: the mechanical zero of a servo horn is
 * never exactly 1500 us, and every angle in the system is measured from it.
 */
function adoptCenter(axis: AxisName) {
  const state = calArmState.value
  const entry = calDraftArm.value
  if (!state || !entry) return
  entry[axis] = { ...(entry[axis] ?? {}), center_us: state.axes[axis].pwm }
  touch()
}

function nudgeTrim(axis: AxisName, delta: number) {
  const entry = calDraftArm.value
  if (!entry) return
  const current = resolved(axis).trim_deg ?? 0
  entry[axis] = { ...(entry[axis] ?? {}), trim_deg: Number((current + delta).toFixed(2)) }
  touch()
}

function flipSign(axis: AxisName) {
  const entry = calDraftArm.value
  if (!entry) return
  entry[axis] = { ...(entry[axis] ?? {}), sign: -(resolved(axis).sign ?? 1) }
  touch()
}

const workspace = computed(() => (calArm.value ? store.config?.workspaces[calArm.value] : null))

/* ----------------------------------------------------------- mapping debug */

/*
  The output map comes from the server, not from `draft`, because it describes the
  saved config against the real board. Editing a channel here does not change it until
  Save; that is deliberate, since a half-typed channel number would otherwise flash
  spurious "this pin cannot work" warnings on every keystroke.
*/
const outputMap = computed(() => store.config?.outputMap ?? null)
const mapProblems = computed(() => outputMap.value?.problems ?? [])

const probeChannel = ref(1)
const probeKind = ref<'motor' | 'servo'>('servo')

function ownerOf(channel: number): string {
  if (channel < 1) return 'disabled (not a pin)'
  for (const arm of draft.value.arms ?? []) {
    if (arm.outer?.channel === channel) return `${arm.label || arm.id} outer servo`
    if (arm.inner?.channel === channel) return `${arm.label || arm.id} inner servo`
    for (const name of ['bottom', 'top'] as MotorName[]) {
      if (arm.motors?.[name]?.channel === channel) {
        return `${arm.label || arm.id} ${name} motor`
      }
    }
  }
  return 'unclaimed'
}

function motorOnChannel(channel: number): { label: string; sequence: number | null } | null {
  for (const arm of draft.value.arms ?? []) {
    for (const name of ['bottom', 'top'] as MotorName[]) {
      const motor = arm.motors?.[name]
      if (motor?.channel === channel) {
        return {
          label: `${arm.label || arm.id} ${name}`,
          sequence: motor.test_sequence ?? null,
        }
      }
    }
  }
  return null
}

watch(probeChannel, (channel) => {
  if (motorOnChannel(channel)) {
    probeKind.value = 'motor'
  }
})

function runProbe() {
  send('probe_output', { channel: probeChannel.value, kind: probeKind.value })
}

/* ----------------------------------------------------------------- add arm */

function addArm() {
  const list = draft.value.arms ?? (draft.value.arms = [])
  const used = new Set<number>()
  for (const arm of list) {
    ;[arm.outer?.channel, arm.inner?.channel].forEach((channel) => {
      if (typeof channel === 'number') used.add(channel)
    })
    Object.values(arm.motors ?? {}).forEach((motor: any) => {
      if (typeof motor?.channel === 'number') used.add(motor.channel)
    })
  }
  const nextFree = (from: number) => {
    let channel = from
    while (used.has(channel)) channel += 1
    used.add(channel)
    return channel
  }

  const index = list.length
  list.push({
    id: `arm${index + 1}`,
    label: `Arm ${index + 1}`,
    // New arms start planned: readouts work, commands are refused until the
    // hardware is actually wired and this is changed by hand.
    status: 'planned',
    azimuth_deg: (360 / (index + 1)) * index,
    mount_yaw_deg: (360 / (index + 1)) * index,
    outer: { channel: nextFree(20) },
    inner: { channel: nextFree(21) },
    motors: {
      bottom: { channel: nextFree(11), spin: 'ccw', reversed: false },
      top: { channel: nextFree(12), spin: 'cw', reversed: true },
    },
  })
  touch()
}

function removeArm(index: number) {
  draft.value.arms.splice(index, 1)
  touch()
}
</script>

<template>
  <div class="stack">
    <!-- save bar -->
    <div class="savebar" :class="{ on: dirty }">
      <div class="grow">
        <StatusPill :tone="dirty ? 'warn' : 'ok'">
          {{ dirty ? 'unsaved changes' : 'saved' }}
        </StatusPill>
        <span class="path mono faint">{{ store.config?.path }}</span>
      </div>
      <span v-if="problem" class="danger small">{{ problem }}</span>
      <button class="ghost" :disabled="!dirty" @click="reset">Discard</button>
      <button class="ghost" @click="send('reload_config')">Reload from disk</button>
      <button class="primary" :disabled="!dirty || isPending('save_config')" @click="save">
        Save
      </button>
    </div>

    <div v-if="conflicts.length" class="alert">
      <strong>Channel conflict.</strong> Two outputs cannot share a pad; they would fight each
      other silently.
      <ul>
        <li v-for="line in conflicts" :key="line" class="mono">{{ line }}</li>
      </ul>
    </div>

    <!-- arms -->
    <PanelCard
      title="Arms"
      note="One entry per arm. Adding an arm here is the only step needed for it to appear everywhere else in the dashboard."
    >
      <template #actions>
        <button class="ghost tiny" @click="addArm">Add arm</button>
      </template>

      <div class="arm-list">
        <div v-for="(arm, index) in draft.arms ?? []" :key="index" class="arm-card">
          <div class="arm-head">
            <span class="swatch" :style="{ background: armColor(arm.id, index) }" />
            <input v-model="arm.label" class="label-input" @input="touch" />
            <select v-model="arm.status" class="narrow" @change="touch">
              <option value="live">live</option>
              <option value="planned">planned</option>
              <option value="disabled">disabled</option>
            </select>
            <button class="ghost tiny" title="Remove this arm" @click="removeArm(index)">
              Remove
            </button>
          </div>

          <div class="field-grid">
            <label>
              <span class="label">id</span>
              <input v-model="arm.id" @input="touch" />
            </label>
            <label>
              <span class="label">azimuth &#176;</span>
              <input v-model.number="arm.azimuth_deg" type="number" step="1" @input="touch" />
            </label>
            <label>
              <span class="label">mount yaw &#176;</span>
              <input v-model.number="arm.mount_yaw_deg" type="number" step="1" @input="touch" />
            </label>
            <label>
              <span class="label">outer ch</span>
              <input v-model.number="arm.outer.channel" type="number" min="0" max="32" @input="touch" />
            </label>
            <label>
              <span class="label">inner ch</span>
              <input v-model.number="arm.inner.channel" type="number" min="0" max="32" @input="touch" />
            </label>
          </div>

          <div class="motor-grid">
            <div v-for="name in (['bottom', 'top'] as MotorName[])" :key="name" class="motor-block">
              <div class="label">{{ name }} motor</div>
              <div class="field-grid tight">
                <label>
                  <span class="label">channel</span>
                  <input
                    v-model.number="arm.motors[name].channel"
                    type="number"
                    min="0"
                    max="32"
                    @input="touch"
                  />
                </label>
                <label>
                  <span class="label">test seq</span>
                  <input
                    :value="arm.motors[name].test_sequence ?? ''"
                    type="number"
                    min="1"
                    placeholder="unset"
                    @input="
                      arm.motors[name].test_sequence =
                        ($event.target as HTMLInputElement).value === ''
                          ? null
                          : Number(($event.target as HTMLInputElement).value);
                      touch()
                    "
                  />
                </label>
                <label>
                  <span class="label">fn</span>
                  <input
                    :value="arm.motors[name].function ?? ''"
                    type="number"
                    min="33"
                    max="40"
                    placeholder="unset"
                    @input="
                      arm.motors[name].function =
                        ($event.target as HTMLInputElement).value === ''
                          ? null
                          : Number(($event.target as HTMLInputElement).value);
                      touch()
                    "
                  />
                </label>
                <label>
                  <span class="label">spin</span>
                  <select v-model="arm.motors[name].spin" @change="touch">
                    <option value="cw">cw</option>
                    <option value="ccw">ccw</option>
                  </select>
                </label>
                <label class="check">
                  <input v-model="arm.motors[name].reversed" type="checkbox" @change="touch" />
                  <span class="label">reversed</span>
                </label>
              </div>
            </div>
          </div>
        </div>
      </div>

      <p class="faint small mt">
        <strong>Azimuth</strong> is where the boom points, measured from the nose toward the
        right. <strong>Mount yaw</strong> is how the gimbal itself is rotated on that boom; it
        is what makes one body-frame command mean the same thing on every arm. They are usually
        equal, but they are separate because a gimbal can be bolted on rotated.
        <strong>Channel 0</strong> disables that output: the pin is unclaimed, nothing is
        written to the flight controller, and two roles may share 0. <strong>Test seq</strong>
        is ArduPilot's motor test ordinal, which is not the output channel &mdash; establish
        it once with a GCS motor test and record it here. It is what
        <code>motor-direction.py</code> addresses. <strong>Fn</strong> is
        <code>SERVOn_FUNCTION</code>, 33&ndash;40 for Motor1&ndash;Motor8: it is what makes the
        output a motor at all, and what the Arms page needs to spin one.
      </p>
    </PanelCard>

    <div class="two-col">
      <!-- calibration -->
      <PanelCard
        title="Servo calibration"
        note="Centre and trim each axis against the real hardware. Do this before trusting any angle the dashboard reports."
      >
        <div class="stack">
          <label>
            <span class="label">Arm</span>
            <select v-model="calArm">
              <option v-for="arm in arms" :key="arm.id" :value="arm.id">
                {{ arm.label }}{{ arm.live ? '' : ` (${arm.status})` }}
              </option>
            </select>
          </label>

          <div v-for="axis in (['outer', 'inner'] as AxisName[])" :key="axis" class="cal-block">
            <div class="row spread">
              <strong class="small">{{ axis }} axis</strong>
              <span class="mono small faint">
                ch{{ resolved(axis).channel ?? calArmState?.axes[axis].channel }}
              </span>
            </div>

            <div class="tiles three">
              <StatTile label="Now" :value="us(calArmState?.axes[axis].pwm)" />
              <StatTile label="Centre" :value="us(resolved(axis).center_us)" />
              <StatTile label="Trim" :value="signedDeg(resolved(axis).trim_deg ?? 0)" />
            </div>

            <div class="row wrap">
              <button
                class="tiny"
                :disabled="!linkUp"
                :title="`Adopt ${us(calArmState?.axes[axis].pwm)} as this axis' centre`"
                @click="adoptCenter(axis)"
              >
                Set centre here
              </button>
              <button class="tiny" @click="nudgeTrim(axis, -0.5)">Trim &minus;0.5&#176;</button>
              <button class="tiny" @click="nudgeTrim(axis, 0.5)">Trim +0.5&#176;</button>
              <button
                class="tiny"
                :title="'Flip if this axis moves the wrong way'"
                @click="flipSign(axis)"
              >
                Flip sign ({{ resolved(axis).sign > 0 ? '+' : '&minus;' }})
              </button>
              <button class="tiny ghost" :disabled="!canCommand" @click="send('center', { arms: [calArm] })">
                Centre servos
              </button>
            </div>
          </div>

          <p class="faint small">
            Procedure: centre the servos, then loosen the horn and set it to mechanical zero by
            hand. If the reported tilt is still offset, use trim; if the axis moves the wrong
            way, flip the sign. Trim eats into corner reach, so keep it small.
          </p>
        </div>
      </PanelCard>

      <!-- derived geometry -->
      <PanelCard
        title="Geometry check"
        note="Recomputed from the saved config. Negative headroom means the servo cannot reach the tilt the config asks for."
      >
        <div class="tiles two">
          <StatTile label="Tilt limit" :value="num(workspace?.tiltLimitDeg, 1)" unit="&#176;" />
          <StatTile
            label="Reach, any direction"
            :value="num(workspace?.uniformTiltDeg, 1)"
            unit="&#176;"
          />
          <StatTile label="Outer needs" :value="num(workspace?.outerServoNeededDeg, 1)" unit="&#176;" />
          <StatTile label="Inner needs" :value="num(workspace?.innerServoNeededDeg, 1)" unit="&#176;" />
          <StatTile
            label="Outer headroom"
            :value="signedDeg(workspace?.outerHeadroomDeg, 1)"
            :tone="(workspace?.outerHeadroomDeg ?? 0) < 0 ? 'danger' : 'ok'"
          />
          <StatTile
            label="Inner headroom"
            :value="signedDeg(workspace?.innerHeadroomDeg, 1)"
            :tone="(workspace?.innerHeadroomDeg ?? 0) < 0 ? 'danger' : 'ok'"
          />
        </div>

        <div class="window-note faint small">
          Outer servo window
          <span class="mono">
            {{ num(workspace?.outerServoWindowDeg[0], 0) }}&#176; to
            {{ num(workspace?.outerServoWindowDeg[1], 0) }}&#176;
          </span>
          &middot; inner
          <span class="mono">
            {{ num(workspace?.innerServoWindowDeg[0], 0) }}&#176; to
            {{ num(workspace?.innerServoWindowDeg[1], 0) }}&#176;
          </span>
        </div>
      </PanelCard>
    </div>

    <div class="two-col">
      <!-- gimbal defaults -->
      <PanelCard
        title="Gimbal defaults"
        note="Inherited by every arm. An arm only overrides what makes it different, which keeps its entry short."
      >
        <div v-if="draft.gimbal_defaults" class="stack">
          <div class="field-grid">
            <label>
              <span class="label">gear ratio</span>
              <input
                v-model.number="draft.gimbal_defaults.gear_ratio"
                type="number"
                step="0.1"
                @input="touch"
              />
            </label>
            <label>
              <span class="label">tilt limit &#176;</span>
              <input
                v-model.number="draft.gimbal_defaults.tilt_limit_deg"
                type="number"
                step="0.5"
                @input="touch"
              />
            </label>
            <label>
              <span class="label">coupling</span>
              <input
                v-model.number="draft.gimbal_defaults.coupling"
                type="number"
                step="0.05"
                @input="touch"
              />
            </label>
          </div>

          <div v-for="axis in (['outer', 'inner'] as AxisName[])" :key="axis">
            <div class="label mb">{{ axis }} axis</div>
            <div class="field-grid">
              <label>
                <span class="label">sign</span>
                <select
                  :value="draft.gimbal_defaults[axis].sign"
                  @change="
                    draft.gimbal_defaults[axis].sign = Number(($event.target as HTMLSelectElement).value);
                    touch()
                  "
                >
                  <option :value="1">+1</option>
                  <option :value="-1">-1</option>
                </select>
              </label>
              <label>
                <span class="label">centre &#181;s</span>
                <input
                  v-model.number="draft.gimbal_defaults[axis].center_us"
                  type="number"
                  @input="touch"
                />
              </label>
              <label>
                <span class="label">&#181;s per &#176;</span>
                <input
                  v-model.number="draft.gimbal_defaults[axis].us_per_deg"
                  type="number"
                  step="0.01"
                  @input="touch"
                />
              </label>
              <label>
                <span class="label">servo limit &#176;</span>
                <input
                  v-model.number="draft.gimbal_defaults[axis].servo_limit_deg"
                  type="number"
                  step="1"
                  @input="touch"
                />
              </label>
              <label>
                <span class="label">min &#181;s</span>
                <input v-model.number="draft.gimbal_defaults[axis].min_us" type="number" @input="touch" />
              </label>
              <label>
                <span class="label">max &#181;s</span>
                <input v-model.number="draft.gimbal_defaults[axis].max_us" type="number" @input="touch" />
              </label>
            </div>
          </div>

          <p class="faint small">
            <strong>Coupling</strong> is how much of the outer servo's travel leaks into the
            inner axis and therefore has to be added back to the inner command. It is 1.0 for
            this linkage, which is why the inner servo needs twice the travel of the outer one.
            <strong>&#181;s per &#176;</strong> is 11.111 for a 500&ndash;2500 &#181;s servo
            over &plusmn;90&#176;.
          </p>
        </div>
      </PanelCard>

      <!-- link and limits -->
      <PanelCard title="Link and bench limits" note="Defaults for the connection, and the caps the server enforces on every command.">
        <div class="stack">
          <div v-if="draft.link" class="field-grid">
            <label>
              <span class="label">device</span>
              <input v-model="draft.link.device" @input="touch" />
            </label>
            <label>
              <span class="label">baud</span>
              <input v-model.number="draft.link.baud" type="number" @input="touch" />
            </label>
            <label>
              <span class="label">ESC telem serials</span>
              <input
                :value="telemSerialsText()"
                @input="setTelemSerials"
              />
            </label>
          </div>
          <p class="faint small">
            SERIALn indexes, comma-separated. On this board 6 is RX4 and 7 is RX6.
            Connect writes <code>SERIALn_PROTOCOL</code> 16; that takes effect at
            boot. RX6 is the default RC input until SERIAL7 is a UART.
          </p>

          <div v-if="draft.bench_limits" class="field-grid">
            <label>
              <span class="label">motor max %</span>
              <input
                v-model.number="draft.bench_limits.motor_percent"
                type="number"
                step="1"
                @input="touch"
              />
            </label>
            <label>
              <span class="label">motor max s</span>
              <input
                v-model.number="draft.bench_limits.motor_seconds"
                type="number"
                step="1"
                @input="touch"
              />
            </label>
            <label>
              <span class="label">servo max &#176;/s</span>
              <input
                v-model.number="draft.bench_limits.servo_speed_deg_s"
                type="number"
                step="10"
                @input="touch"
              />
            </label>
            <label>
              <span class="label">command Hz</span>
              <input
                v-model.number="draft.bench_limits.command_rate_hz"
                type="number"
                step="1"
                @input="touch"
              />
            </label>
          </div>

          <p class="faint small">
            The motor caps are the ones worth keeping low. They bound every test spin the
            dashboard can request, so a mistyped throttle in the UI cannot exceed them.
          </p>

          <div class="row">
            <button class="ghost" :disabled="!linkUp" @click="send('assert_mapping')">
              Re-apply output mapping
            </button>
            <button class="ghost" :disabled="!linkUp" @click="send('refresh_streams')">
              Re-request streams
            </button>
          </div>
        </div>
      </PanelCard>
    </div>

    <PanelCard
      title="Output map"
      note="What each pin on this board actually is. A timer group is all DShot or all PWM, so one motor in a group decides the mode for every pin in it."
    >
      <div class="stack">
        <div v-if="mapProblems.length" class="alert">
          <strong v-if="mapProblems.some((p) => p.severity === 'fatal')">
            This map will not work as written.
          </strong>
          <strong v-else>Things the flight controller cannot do for you.</strong>
          <ul>
            <li v-for="problem in mapProblems" :key="problem.severity + problem.channel">
              <StatusPill :tone="problem.severity === 'fatal' ? 'danger' : 'warn'">
                S{{ problem.channel }}
              </StatusPill>
              {{ problem.text }}
            </li>
          </ul>
        </div>
        <p v-else class="faint small">
          Every group is single-mode and every mixer slot is placed. No output is silently
          incapable of the signal it is being asked for.
        </p>

        <div v-for="group in outputMap?.groups ?? []" :key="group.name" class="group-row">
          <span class="group-name mono">{{ group.name }}</span>
          <StatusPill :tone="group.mode === 'DShot' ? 'accent' : group.mode === 'CAN' ? 'idle' : 'ok'">
            {{ group.mode }}
          </StatusPill>
          <span
            v-for="cell in group.outputs"
            :key="cell.channel"
            class="cell mono small"
            :class="{ free: !cell.owner }"
          >
            S{{ cell.channel }}<template v-if="cell.canOutput"> (out {{ cell.canOutput }})</template>
            <template v-if="cell.owner"> — {{ cell.owner }}</template>
            <template v-else> — free</template>
            <template v-if="cell.nodeFunction"> · node fn {{ cell.nodeFunction }}</template>
          </span>
        </div>
      </div>
    </PanelCard>

    <PanelCard
      title="Mapping debug"
      note="Name a pin, not an arm. The only way to settle 'I asked for north top and something else moved' is to drive the output itself."
    >
      <div class="stack">
        <div class="field-grid">
          <label>
            <span class="label">channel</span>
            <select v-model.number="probeChannel">
              <option v-for="n in 32" :key="n" :value="n">
                S{{ n }} — {{ ownerOf(n) }}
              </option>
            </select>
          </label>
          <label>
            <span class="label">test</span>
            <select v-model="probeKind">
              <option value="servo">servo: deflect, then centre</option>
              <option value="motor">motor: spin 1 s</option>
            </select>
          </label>
        </div>

        <div class="row wrap">
          <button
            class="primary"
            :disabled="!canCommand || isPending('probe_output')"
            @click="runProbe"
          >
            {{ probeKind === 'motor' ? `Spin S${probeChannel} for 1 s` : `Sweep S${probeChannel}` }}
          </button>
          <button class="ghost" :disabled="!canCommand" @click="send('stop_motors')">
            Stop motors
          </button>
        </div>

        <p class="faint small">
          Config says this pin is <strong>{{ ownerOf(probeChannel) }}</strong>.
          <template v-if="probeKind === 'motor' && motorOnChannel(probeChannel)">
            This drives whatever motor function is <strong>already on the board</strong>
            for S{{ probeChannel }}, at the bench cap
            ({{ draft.bench_limits?.motor_percent ?? '?' }}%).
            Saving a new map here does not move the signal until you apply output
            mapping. After that, identify from the Arms page by named motor, not by
            re-probing pin numbers.
          </template>
          <template v-else-if="probeKind === 'motor'">
            Props off. Throttle is the bench cap ({{ draft.bench_limits?.motor_percent ?? '?' }}%).
            The previous motor function is put back afterwards.
          </template>
          <template v-else>
            The servo moves off centre for about half a second, then returns. S1–S13 are
            onboard, and a pin in a group the Output map shows as <strong>DShot</strong> will
            not move a servo however it is commanded. S{{ outputMap?.canServoFirst ?? 14 }} and
            up go out over the CAN-to-PWM node, and do nothing until output mapping has set
            the DroneCAN masks (and a reboot if CAN was off) and the node’s own
            <code>OUTx_FUNCTION</code> matches — the Output map shows the number each output
            needs. A motor on the node only takes throttle from the motor test, so a motor
            probe there runs that instead of driving the pin.
          </template>
        </p>
      </div>
    </PanelCard>

    <PanelCard
      title="Document"
      note="The file as it will be written. Useful for review before saving, and for copying into version control."
      flush
    >
      <pre class="doc mono">{{ JSON.stringify(draft, null, 2) }}</pre>
    </PanelCard>
  </div>
</template>

<style scoped>
.group-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  padding: 0.25rem 0;
}

.group-name {
  min-width: 3.5rem;
  font-weight: 600;
}

.cell.free {
  opacity: 0.45;
}

.savebar {
  display: flex;
  align-items: center;
  gap: var(--s2);
  padding: var(--s2) var(--s3);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--surface-1);
  position: sticky;
  top: 0;
  z-index: 5;
}

.savebar.on {
  border-color: color-mix(in srgb, var(--warn) 45%, transparent);
  background: color-mix(in srgb, var(--warn) 6%, var(--surface-1));
}

.savebar .grow {
  display: flex;
  align-items: center;
  gap: var(--s3);
  min-width: 0;
}

.path {
  font-size: 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.alert {
  border: 1px solid color-mix(in srgb, var(--danger) 45%, transparent);
  background: var(--danger-wash);
  border-radius: var(--radius);
  padding: var(--s3) var(--s4);
  font-size: var(--fs-sm);
}

.alert ul {
  margin: var(--s2) 0 0;
  padding-left: var(--s5);
}

.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--s4);
  align-items: start;
}

.arm-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: var(--s3);
}

.arm-card {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: var(--s3);
  background: var(--surface-2);
  display: flex;
  flex-direction: column;
  gap: var(--s3);
}

.arm-head {
  display: flex;
  align-items: center;
  gap: var(--s2);
}

.swatch {
  width: 9px;
  height: 9px;
  border-radius: 2px;
  flex: none;
}

.label-input {
  font-weight: 600;
  flex: 1;
  min-width: 0;
}

.narrow {
  width: auto;
  min-width: 96px;
  font-size: var(--fs-sm);
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(96px, 1fr));
  gap: var(--s2);
}

.field-grid.tight {
  grid-template-columns: repeat(auto-fit, minmax(78px, 1fr));
}

.field-grid label {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.field-grid input,
.field-grid select {
  font-size: var(--fs-sm);
  padding: 4px var(--s2);
}

.check {
  flex-direction: row !important;
  align-items: center;
  gap: var(--s2);
}

.check input {
  width: auto;
}

.motor-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--s3);
}

.motor-block {
  display: flex;
  flex-direction: column;
  gap: var(--s1);
}

.cal-block {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: var(--s3);
  display: flex;
  flex-direction: column;
  gap: var(--s2);
}

.tiles {
  display: grid;
  gap: var(--s2);
}

.tiles.two {
  grid-template-columns: 1fr 1fr;
}

.tiles.three {
  grid-template-columns: repeat(3, 1fr);
}

.window-note {
  margin-top: var(--s3);
  line-height: 1.6;
}

.small {
  font-size: var(--fs-xs);
  line-height: 1.55;
}

.mt {
  margin-top: var(--s3);
}

.mb {
  margin-bottom: var(--s1);
}

.doc {
  margin: 0;
  padding: var(--s3) var(--s4);
  max-height: 380px;
  overflow: auto;
  font-size: var(--fs-xs);
  color: var(--text-dim);
  line-height: 1.5;
}

@media (max-width: 1150px) {
  .two-col {
    grid-template-columns: 1fr;
  }
}
</style>
