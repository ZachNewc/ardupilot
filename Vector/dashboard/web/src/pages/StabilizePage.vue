<!--
  Stabilize: the bench levelling demo.

  This page has one job beyond driving the loop, and it is to be honest about what
  the loop is. It runs in Python over a serial link at tens of hertz, and on a plus
  layout thrust vectoring produces almost no roll or pitch moment, so it cannot fly
  the aircraft. Saying that clearly on screen is not a disclaimer — it is the reason
  the firmware mixer exists, and a dashboard that implied otherwise would be
  actively dangerous.
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
import { desiredLean } from '../lib/kinematics'
import store, { arms, canCommand, commandBlockedReason, send } from '../lib/store'

const controller = computed(() => store.state?.controller ?? null)
const vehicle = computed(() => store.state?.vehicle ?? null)

/* Local copies of the gains so a slider stays where it is put while the server
   snapshot streams in at 20 Hz. */
const gain = ref(1)
const lead = ref(0.06)
const cap = ref(1)
const invertRoll = ref(false)
const invertPitch = ref(false)
let seeded = false

watch(
  controller,
  (value) => {
    if (!value || seeded) return
    gain.value = value.levelGain
    lead.value = value.leadTimeS
    cap.value = value.maxTiltFraction
    invertRoll.value = value.invertRoll
    invertPitch.value = value.invertPitch
    seeded = true
  },
  { immediate: true },
)

const active = computed(() => controller.value?.active === true)

function payload(extra: Record<string, unknown> = {}) {
  return {
    levelGain: gain.value,
    leadTimeS: lead.value,
    maxTiltFraction: cap.value,
    invertRoll: invertRoll.value,
    invertPitch: invertPitch.value,
    ...extra,
  }
}

/** Retune without toggling: the server applies gains then keeps the loop running. */
function push() {
  send('level', payload({ active: active.value }))
}

const start = () => send('level', payload({ active: true, mode: 'level' }))
const stop = () => send('level', { active: false })

/**
 * What the law would command at the current attitude, computed in the browser.
 *
 * This is the same arithmetic the server runs, so the page can show the intended
 * correction while the loop is switched off. It is a preview only; nothing here
 * reaches a servo.
 */
const localPreview = computed(() => {
  if (!vehicle.value || !controller.value) return null
  return desiredLean(
    {
      levelGain: gain.value,
      leadTimeS: lead.value,
      maxTiltFraction: cap.value,
      invertRoll: invertRoll.value,
      invertPitch: invertPitch.value,
      tiltCapDeg: controller.value.tiltCapDeg / Math.max(0.05, controller.value.maxTiltFraction),
    },
    vehicle.value.rollDeg,
    vehicle.value.pitchDeg,
    vehicle.value.rollRateDegS,
    vehicle.value.pitchRateDegS,
  )
})

const shown = computed(() =>
  active.value && controller.value
    ? { forward: controller.value.target.forward, right: controller.value.target.right }
    : localPreview.value
      ? { forward: localPreview.value.forward, right: localPreview.value.right }
      : null,
)

const bodyTilt = computed(() =>
  vehicle.value ? Math.hypot(vehicle.value.rollDeg, vehicle.value.pitchDeg) : 0,
)

/** Loop rate against the configured target: the number that shows why this is bench-only. */
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
    <!-- what this is -->
    <div class="scope">
      <div class="scope-head">
        <StatusPill tone="warn">bench demo</StatusPill>
        <strong>This is not flight stabilisation.</strong>
      </div>
      <div class="scope-body">
        <p>
          Tilt the rig by hand and the gimbals counter-rotate so the motors keep pointing at
          world vertical. That is worth seeing and worth tuning, and it proves the axis signs
          and the kinematics are right. It is not a flight controller, for two reasons that no
          amount of tuning fixes.
        </p>
        <ol>
          <li>
            <strong>Mechanically.</strong> Leaning the thrust on a plus layout produces a
            lateral force and a yaw moment, but almost no roll or pitch moment: a horizontal
            force at a rotor in the centre-of-mass plane has no lever arm about those axes.
            Roll and pitch authority comes from differential thrust between opposite arms,
            which is ArduPilot's job. Vectoring buys translation while staying level; it does
            not buy attitude authority.
          </li>
          <li>
            <strong>In timing.</strong> This loop runs in Python, over MAVLink, over a serial
            link, at
            <span class="mono">{{ num(controller?.loopHz, 0) }} Hz</span>. Attitude control
            needs hundreds of hertz on a deterministic budget. The real controller belongs in
            firmware as a custom <span class="mono">AP_Motors</span> backend &mdash; see
            <RouterLink to="/docs/04-control">Control</RouterLink> and
            <RouterLink to="/docs/05-firmware">Firmware</RouterLink>.
          </li>
        </ol>
      </div>
    </div>

    <p v-if="commandBlockedReason" class="blocked">{{ commandBlockedReason }}</p>

    <div class="layout">
      <PanelCard title="Levelling" note="Body attitude against the lean the law is asking for.">
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
            {{ active ? 'commanded lean' : 'lean it would command' }}
          </span>
          <span><span class="key env" /> cap {{ num(controller?.tiltCapDeg, 1) }}&#176;</span>
        </div>

        <div class="tiles two mt">
          <StatTile label="Body off vertical" :value="signedDeg(bodyTilt)" />
          <StatTile
            label="Correction"
            :value="num(Math.hypot(shown?.forward ?? 0, shown?.right ?? 0), 1)"
            unit="&#176;"
            :tone="controller?.saturated ? 'warn' : undefined"
          />
          <StatTile label="Lean fwd" :value="signedDeg(shown?.forward)" />
          <StatTile label="Lean right" :value="signedDeg(shown?.right)" />
        </div>

        <p v-if="controller?.saturated" class="warn small mt">
          Saturated: the airframe is tilted further than the gimbals can correct, so the lean
          is being held at the envelope edge in the right direction rather than clipped on one
          axis.
        </p>
      </PanelCard>

      <PanelCard title="Tuning" note="Changes apply immediately, running or not.">
        <div class="stack">
          <SliderRow
            v-model="gain"
            label="Level gain"
            :min="0"
            :max="1.5"
            :step="0.05"
            :digits="2"
            note="0 leaves the gimbals fixed to the airframe. 1 holds thrust at true world vertical. Above 1 over-corrects, which is only useful for provoking oscillation deliberately."
            @change="push"
          />
          <SliderRow
            v-model="lead"
            label="Lead time"
            :min="0"
            :max="0.25"
            :step="0.005"
            :digits="3"
            unit=" s"
            note="Extrapolates attitude forward to offset servo lag. It is a time, not an abstract gain, so it can be set from a measured step response: start at the servo's 60-degree time."
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
              v-model="invertRoll"
              label="Invert roll response"
              note="Flip if the gimbals move the wrong way when the frame is rolled. Check this on the bench before anything spins."
              @update:model-value="push"
            />
            <ToggleRow
              v-model="invertPitch"
              label="Invert pitch response"
              @update:model-value="push"
            />
          </div>

          <div class="row">
            <button v-if="!active" class="primary grow" :disabled="!canCommand" @click="start">
              Start levelling
            </button>
            <button v-else class="danger grow" @click="stop">Stop levelling</button>
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

        <p class="faint small mt">
          For scale: a firmware mixer runs at 400 Hz with bounded jitter. Anything here is
          two orders of magnitude away from that, which is the honest measure of the gap.
        </p>
      </PanelCard>

      <PanelCard title="Arms" note="Every live arm should lean the same way. One that does not has a sign or mount-yaw error.">
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
