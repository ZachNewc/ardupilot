<!--
  Accel: bench demo that leans motor thrust against measured linear acceleration.

  Same host-side loop as Stabilize, different law. Gravity is subtracted using
  attitude so a static tilt is not treated as a shove. The gimbals point the
  motors; this page does not spin them. Same mechanical and latency limits as
  the gyro page: this is not flight control.
-->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import AttitudeDisc from '../components/AttitudeDisc.vue'
import EventFeed from '../components/EventFeed.vue'
import FrameDiagram from '../components/FrameDiagram.vue'
import PanelCard from '../components/PanelCard.vue'
import SliderRow from '../components/SliderRow.vue'
import StatTile from '../components/StatTile.vue'
import StatusPill from '../components/StatusPill.vue'
import ToggleRow from '../components/ToggleRow.vue'
import { num, signedDeg } from '../lib/format'
import { desiredAccelLean } from '../lib/kinematics'
import store, { arms, canCommand, commandBlockedReason, send } from '../lib/store'

const controller = computed(() => store.state?.controller ?? null)
const vehicle = computed(() => store.state?.vehicle ?? null)

const gain = ref(40)
const deadband = ref(0.05)
const cap = ref(1)
const invertX = ref(false)
const invertY = ref(false)
let seeded = false

watch(
  controller,
  (value) => {
    if (!value || seeded) return
    gain.value = value.accelGainDegG
    deadband.value = value.accelDeadbandG
    cap.value = value.maxTiltFraction
    invertX.value = value.invertAccelX
    invertY.value = value.invertAccelY
    seeded = true
  },
  { immediate: true },
)

const active = computed(
  () => controller.value?.active === true && controller.value.mode === 'accel',
)

function payload(extra: Record<string, unknown> = {}) {
  return {
    accelGainDegG: gain.value,
    accelDeadbandG: deadband.value,
    maxTiltFraction: cap.value,
    invertAccelX: invertX.value,
    invertAccelY: invertY.value,
    mode: 'accel',
    ...extra,
  }
}

function push() {
  send('level', payload({ active: active.value }))
}

const start = () => send('level', payload({ active: true, mode: 'accel' }))
const stop = () => send('level', { active: false })

const imuSeen = computed(() => (vehicle.value?.accelAgeS ?? null) !== null)
const imuStale = computed(() => {
  const age = vehicle.value?.accelAgeS
  return age !== null && age !== undefined && age > 1
})

const localPreview = computed(() => {
  if (!vehicle.value || !controller.value || !imuSeen.value) return null
  return desiredAccelLean(
    {
      accelGainDegG: gain.value,
      accelDeadbandG: deadband.value,
      invertAccelX: invertX.value,
      invertAccelY: invertY.value,
      tiltCapDeg: controller.value.tiltCapDeg / Math.max(0.05, controller.value.maxTiltFraction),
      maxTiltFraction: cap.value,
    },
    [vehicle.value.accelXg, vehicle.value.accelYg, vehicle.value.accelZg],
    vehicle.value.rollDeg,
    vehicle.value.pitchDeg,
  )
})

const shown = computed(() =>
  active.value && controller.value
    ? { forward: controller.value.target.forward, right: controller.value.target.right }
    : localPreview.value
      ? { forward: localPreview.value.forward, right: localPreview.value.right }
      : null,
)

const linear = computed(() => controller.value?.linearAccelG ?? null)

const rateTone = computed(() => {
  const hz = controller.value?.loopHz ?? 0
  const want = store.config?.benchLimits.commandRateHz ?? 25
  if (!active.value) return 'dim'
  if (hz < want * 0.6) return 'danger'
  if (hz < want * 0.9) return 'warn'
  return 'ok'
})
</script>

<template>
  <div class="stack">
    <div class="scope">
      <div class="scope-head">
        <StatusPill tone="warn">bench demo</StatusPill>
        <strong>This is not flight stabilisation.</strong>
      </div>
      <div class="scope-body">
        <p>
          The IMU reports acceleration. Gravity is subtracted using attitude, then the
          gimbals lean every live arm so the motors would push against the remaining
          horizontal vector. Shove the rig forward and the rotors should point aft.
          Holding the frame at an angle, without shoving it, should do nothing.
        </p>
        <ol>
          <li>
            <strong>Motors aim, they do not spin.</strong> Counteracting a force needs
            thrust along the leaned direction. This loop only points the gimbals. Use
            the Arms page motor test if you want the rotors turning, props off or the
            vehicle clamped.
          </li>
          <li>
            <strong>Mechanically.</strong> Leaned thrust on a plus layout is a lateral
            force, not a roll or pitch moment. The
            <RouterLink to="/stabilize">Stabilize</RouterLink> page holds world-vertical
            with the gyros; this page opposes translation. Neither is an attitude
            controller.
          </li>
          <li>
            <strong>In timing.</strong> Same 25 Hz Python-over-USB loop as levelling.
            A real mixer belongs in firmware &mdash; see
            <RouterLink to="/docs/04-control">Control</RouterLink>.
          </li>
        </ol>
      </div>
    </div>

    <p v-if="commandBlockedReason" class="blocked">{{ commandBlockedReason }}</p>
    <p v-else-if="controller?.active && !active" class="blocked">
      Gyro levelling is running. Start accel hold to switch laws, or stop it from the
      <RouterLink to="/stabilize">Stabilize</RouterLink> page.
    </p>
    <p v-else-if="!imuSeen" class="blocked">
      Waiting for SCALED_IMU. Connect, then use Refresh streams on Telemetry if the
      count stays at zero.
    </p>
    <p v-else-if="imuStale" class="blocked">
      IMU samples are stale ({{ num(vehicle?.accelAgeS, 2) }} s). The loop will hold
      centre until they resume.
    </p>

    <div class="layout">
      <PanelCard title="Accel hold" note="Horizontal linear accel against the lean the motors would produce.">
        <template #actions>
          <StatusPill :tone="active ? 'accent' : 'idle'" :pulse="active">
            {{ active ? 'running' : 'stopped' }}
          </StatusPill>
        </template>

        <AttitudeDisc
          :roll-deg="vehicle?.rollDeg ?? 0"
          :pitch-deg="vehicle?.pitchDeg ?? 0"
          :lean="shown"
          :envelope-deg="controller?.tiltCapDeg ?? null"
        />

        <div class="legend faint">
          <span><span class="key body" /> body tilt</span>
          <span>
            <span class="key lean" />
            {{ active ? 'motor lean (oppose)' : 'lean it would command' }}
          </span>
          <span><span class="key env" /> cap {{ num(controller?.tiltCapDeg, 1) }}&#176;</span>
        </div>

        <div class="tiles two mt">
          <StatTile
            label="Linear horiz"
            :value="num(linear?.horizontal, 3)"
            unit=" g"
            :tone="(linear?.horizontal ?? 0) > 0.2 ? 'warn' : undefined"
          />
          <StatTile
            label="Correction"
            :value="num(Math.hypot(shown?.forward ?? 0, shown?.right ?? 0), 1)"
            unit="&#176;"
            :tone="controller?.saturated ? 'warn' : undefined"
          />
          <StatTile label="Lin fwd" :value="num(linear?.x, 3)" unit=" g" />
          <StatTile label="Lin right" :value="num(linear?.y, 3)" unit=" g" />
          <StatTile label="Lean fwd" :value="signedDeg(shown?.forward)" />
          <StatTile label="Lean right" :value="signedDeg(shown?.right)" />
        </div>

        <p v-if="controller?.saturated && active" class="warn small mt">
          Saturated: the oppose demand is larger than the gimbals can reach, so the
          lean is held at the envelope edge in the right direction.
        </p>
      </PanelCard>

      <PanelCard title="IMU" note="Raw specific force, then gravity removed. Rest should be ~0, 0, +1 g raw.">
        <div class="tiles two">
          <StatTile label="IMU X" :value="num(vehicle?.accelXg, 3)" unit=" g" />
          <StatTile label="IMU Y" :value="num(vehicle?.accelYg, 3)" unit=" g" />
          <StatTile label="IMU Z" :value="num(vehicle?.accelZg, 3)" unit=" g" />
          <StatTile
            label="IMU age"
            :value="imuSeen ? num(vehicle?.accelAgeS, 2) : '--'"
            unit=" s"
            :tone="!imuSeen || imuStale ? 'warn' : 'dim'"
          />
        </div>
        <p class="faint small mt">
          Body NED: X forward, Y right, Z down. Linear accel above is IMU minus
          gravity expressed in that same frame.
        </p>
      </PanelCard>

      <PanelCard title="Tuning" note="Changes apply immediately, running or not.">
        <div class="stack">
          <SliderRow
            v-model="gain"
            label="Oppose gain"
            :min="0"
            :max="90"
            :step="1"
            :digits="0"
            unit=" deg/g"
            note="Degrees of motor lean per g of horizontal linear accel. 40 deg/g uses most of the envelope at about half a g."
            @change="push"
          />
          <SliderRow
            v-model="deadband"
            label="Deadband"
            :min="0"
            :max="0.2"
            :step="0.005"
            :digits="3"
            unit=" g"
            note="Horizontal linear accel inside this band is treated as rest, so IMU noise does not hunt the servos."
            @change="push"
          />
          <SliderRow
            v-model="cap"
            label="Envelope use"
            :min="0.05"
            :max="1"
            :step="0.05"
            :digits="2"
            :note="`Fraction of the reachable envelope the loop may use. Currently ${num(controller?.tiltCapDeg, 1)}\u00b0.`"
            @change="push"
          />

          <div class="stack tight">
            <ToggleRow
              v-model="invertX"
              label="Invert forward response"
              note="Flip if a forward shove leans the motors the wrong way. Check this on the bench before anything spins."
              @update:model-value="push"
            />
            <ToggleRow
              v-model="invertY"
              label="Invert right response"
              @update:model-value="push"
            />
          </div>

          <div class="row">
            <button v-if="!active" class="primary grow" :disabled="!canCommand || !imuSeen" @click="start">
              Start accel hold
            </button>
            <button v-else class="danger grow" @click="stop">Stop accel hold</button>
          </div>

          <p class="faint small">
            Props off, or the vehicle clamped down. The loop drives all live gimbals
            continuously and takes ownership of those outputs until it is stopped.
          </p>
        </div>
      </PanelCard>

      <PanelCard title="Loop health" note="Whether the demo is keeping up with its own target rate.">
        <div class="tiles two">
          <StatTile
            label="Loop rate"
            :value="num(controller?.loopHz, 1)"
            unit=" Hz"
            :tone="rateTone"
            :hint="`Target ${num(store.config?.benchLimits.commandRateHz, 0)} Hz`"
          />
          <StatTile label="Updates" :value="controller?.updates ?? 0" />
          <StatTile
            label="Owner"
            :value="store.state?.outputs.owner ?? 'none'"
            hint="Which subsystem currently owns the servo outputs"
          />
          <StatTile label="Mode" :value="controller?.mode ?? 'off'" />
        </div>

        <p v-if="controller?.lastError" class="danger small mt">{{ controller.lastError }}</p>
      </PanelCard>

      <PanelCard title="Arms" note="Every live arm should lean the same way against the shove.">
        <FrameDiagram :arms="arms" />
      </PanelCard>
    </div>

    <PanelCard title="Activity" flush>
      <EventFeed :limit="30" />
    </PanelCard>
  </div>
</template>

<style scoped>
.scope {
  border: 1px solid color-mix(in srgb, var(--warn) 35%, transparent);
  background: var(--warn-wash);
  border-radius: var(--radius);
  padding: var(--s3) var(--s4);
}

.scope-head {
  display: flex;
  align-items: center;
  gap: var(--s3);
  margin-bottom: var(--s2);
}

.scope-body {
  color: var(--text-dim);
  font-size: var(--fs-sm);
  max-width: 84ch;
}

.scope-body p {
  margin: 0 0 var(--s2);
}

.scope-body ol {
  margin: 0;
  padding-left: var(--s5);
  display: flex;
  flex-direction: column;
  gap: var(--s2);
}

.scope-body strong {
  color: var(--text);
}

.blocked {
  margin: 0;
  padding: var(--s2) var(--s3);
  border-radius: var(--radius-sm);
  border: 1px solid var(--line-strong);
  background: var(--surface-2);
  color: var(--text-dim);
  font-size: var(--fs-sm);
}

.layout {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--s4);
  align-items: start;
}

.tiles {
  display: grid;
  gap: var(--s2);
}

.tiles.two {
  grid-template-columns: 1fr 1fr;
}

.tight {
  gap: var(--s2);
}

.mt {
  margin-top: var(--s3);
}

.small {
  font-size: var(--fs-xs);
  line-height: 1.5;
}

.legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--s3);
  font-size: var(--fs-xs);
  margin-top: var(--s2);
}

.key {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 4px;
}

.key.body {
  background: var(--warn);
}

.key.lean {
  border: 2px solid var(--accent);
}

.key.env {
  border: 1px dashed var(--accent);
}

@media (max-width: 1150px) {
  .layout {
    grid-template-columns: 1fr;
  }
}
</style>
