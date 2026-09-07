<!--
  Arms: aim one arm (or all of them together), inspect its envelope, test its motors.

  The arm tabs come from the config, so a fifth arm appears here the moment it is
  added to vector.json. "All live arms" is a first-class target because commanding
  four arms to the same body-frame lean is the gesture that shows the mount-yaw
  bookkeeping is right: they should all lean the same way on screen.
-->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AimPad from '../components/AimPad.vue'
import FrameDiagram from '../components/FrameDiagram.vue'
import PanelCard from '../components/PanelCard.vue'
import SliderRow from '../components/SliderRow.vue'
import StatTile from '../components/StatTile.vue'
import StatusPill from '../components/StatusPill.vue'
import { armColor, num, signedDeg, us } from '../lib/format'
import * as kin from '../lib/kinematics'
import store, { arms, canCommand, commandBlockedReason, isPending, send } from '../lib/store'
import type { AxisName, MotorName } from '../lib/types'

const route = useRoute()
const router = useRouter()

const ALL = 'all'

/** Selected target: an arm id, or ALL for every live arm at once. */
const selection = ref<string>((route.query.arm as string) || ALL)

watch(
  () => route.query.arm,
  (value) => {
    if (typeof value === 'string' && value) selection.value = value
  },
)

function choose(id: string) {
  selection.value = id
  router.replace({ query: id === ALL ? {} : { arm: id } })
}

const liveArms = computed(() => arms.value.filter((arm) => arm.live))

/** The arm whose numbers are shown. For ALL, the first live arm stands in. */
const focus = computed(() => {
  if (selection.value === ALL) return liveArms.value[0] ?? arms.value[0] ?? null
  return arms.value.find((arm) => arm.id === selection.value) ?? null
})

const focusConfig = computed(() =>
  focus.value ? store.config?.arms.find((arm) => arm.id === focus.value!.id) ?? null : null,
)

const workspace = computed(() =>
  focus.value ? store.config?.workspaces[focus.value.id] ?? null : null,
)

const targets = computed(() => (selection.value === ALL ? undefined : [selection.value]))

const accent = computed(() =>
  focus.value ? armColor(focus.value.id, arms.value.indexOf(focus.value)) : 'var(--accent)',
)

const selectionLive = computed(() =>
  selection.value === ALL ? liveArms.value.length > 0 : (focus.value?.live ?? false),
)

const disabled = computed(() => !canCommand.value || !selectionLive.value)

/* ------------------------------------------------------------------- aiming */

const commanded = ref<{ forward: number; right: number } | null>(null)

// Throttle the drag stream to the configured command rate rather than the pointer
// rate: a trackpad emits far more events than a serial link can carry.
let lastSent = 0
function onDrag(forward: number, right: number) {
  commanded.value = { forward, right }
  const minGap = 1000 / (store.config?.benchLimits.commandRateHz ?? 25)
  const now = performance.now()
  if (now - lastSent < minGap) return
  lastSent = now
  send('live_aim', { arms: targets.value, forward, right }, true)
}

function onRelease() {
  send('live_end', {}, true)
}

/** Preview of what a lean would actually do, computed locally before anything moves. */
const preview = computed(() => {
  if (!focusConfig.value || !commanded.value) return null
  const answer = kin.solveThrustLean(
    focusConfig.value,
    commanded.value.forward,
    commanded.value.right,
  )
  const [forward, right] = kin.thrustLean(
    focusConfig.value.mountYawDeg,
    answer.outer.tiltDeg,
    answer.inner.tiltDeg,
  )
  return { answer, forward, right }
})

function aimTo(forward: number, right: number) {
  commanded.value = { forward, right }
  send('aim', { arms: targets.value, forward, right, speed: slewSpeed.value })
}

const slewSpeed = ref(0)

/* -------------------------------------------------------------- axis sliders */

const axisTilt = ref<Record<AxisName, number>>({ outer: 0, inner: 0 })

// Follow the vehicle unless the operator is holding the slider.
const axisHeld = ref<AxisName | null>(null)
watch(
  focus,
  (arm) => {
    if (!arm) return
    if (axisHeld.value !== 'outer') axisTilt.value.outer = arm.axes.outer.tiltDeg
    if (axisHeld.value !== 'inner') axisTilt.value.inner = arm.axes.inner.tiltDeg
  },
  { immediate: true, deep: true },
)

function driveAxis(axis: AxisName, value: number) {
  axisTilt.value[axis] = value
  if (!focus.value) return
  send('set_axis', { arm: focus.value.id, axis, deg: value, speed: slewSpeed.value })
}

/* ---------------------------------------------------------------- motor test */

const motorPercent = ref(5)
const motorSeconds = ref(2)
const motorPick = ref<Record<MotorName, boolean>>({ bottom: false, top: false })

/** Motor1..Motor8 are SERVOn_FUNCTION 33..40. */
const FUNC_MOTOR_FIRST = 33

const limits = computed(() => store.config?.benchLimits ?? null)

const pickedMotors = computed(() =>
  (Object.keys(motorPick.value) as MotorName[]).filter((name) => motorPick.value[name]),
)

/**
 * How many motors a click actually spins.
 *
 * The tick boxes name roles, not motors, so "All live arms" with both ticked is four
 * propellers rather than two. They all start together now, which is a number worth
 * being honest about before pressing the button.
 */
const spinCount = computed(() => {
  const targeted = selection.value === ALL ? liveArms.value.length : focus.value?.live ? 1 : 0
  return targeted * pickedMotors.value.length
})

/**
 * The motor function for one role on the focused arm, or null if it has none.
 *
 * Spinning force-arms the mixer, so `function` -- not `test_sequence` -- is
 * what decides whether a motor can be driven and handed back afterwards.
 */
function motorFunction(name: MotorName): number | null {
  return focusConfig.value?.motors?.[name]?.function ?? null
}

function motorFunctionLabel(name: MotorName): string {
  const fn = motorFunction(name)
  return fn === null ? 'unset' : `M${fn - FUNC_MOTOR_FIRST + 1}`
}

/** Motors with no function cannot be addressed, so say so instead of failing later. */
const unmapped = computed(() => {
  const arm = focusConfig.value
  if (!arm) return []
  return (Object.keys(arm.motors) as MotorName[]).filter((name) => motorFunction(name) === null)
})

function spin() {
  send('spin_motors', {
    arms: targets.value,
    motors: pickedMotors.value,
    percent: motorPercent.value,
    seconds: motorSeconds.value,
  })
}
</script>

<template>
  <div class="stack">
    <!-- target picker -->
    <div class="tabs">
      <button :class="['tab', { on: selection === ALL }]" @click="choose(ALL)">
        <span class="swatch all" />
        All live arms
        <span class="count">{{ liveArms.length }}</span>
      </button>
      <button
        v-for="(arm, index) in arms"
        :key="arm.id"
        :class="['tab', { on: selection === arm.id, planned: !arm.live }]"
        @click="choose(arm.id)"
      >
        <span class="swatch" :style="{ background: armColor(arm.id, index) }" />
        {{ arm.label }}
        <span v-if="!arm.live" class="count">{{ arm.status }}</span>
      </button>
    </div>

    <p v-if="commandBlockedReason" class="blocked">{{ commandBlockedReason }}</p>
    <p v-else-if="!selectionLive" class="blocked">
      {{ focus?.label }} is marked <strong>{{ focus?.status }}</strong> in the config. Readouts
      work; commands are refused so a half-built arm cannot be driven.
    </p>

    <div class="layout">
      <!-- aiming -->
      <PanelCard
        title="Aim"
        note="Drag inside the envelope. Axes are body frame: up leans thrust forward, right leans it right."
        :accent="accent"
      >
        <template #actions>
          <button class="ghost tiny" :disabled="disabled" @click="aimTo(0, 0)">Center</button>
        </template>

        <div class="aim-grid">
          <AimPad
            :workspace="workspace"
            :actual="focus ? { forwardDeg: focus.thrust.forwardDeg, rightDeg: focus.thrust.rightDeg } : null"
            :target="commanded"
            :disabled="disabled"
            :accent="accent"
            @drag="onDrag"
            @release="onRelease"
          />

          <div class="stack">
            <div class="tiles two">
              <StatTile
                label="Thrust fwd"
                :value="signedDeg(focus?.thrust.forwardDeg)"
                hint="Exact lean of this arm's thrust, out of vertical toward the nose"
              />
              <StatTile label="Thrust right" :value="signedDeg(focus?.thrust.rightDeg)" />
              <StatTile
                label="Outer tilt"
                :value="signedDeg(focus?.axes.outer.tiltDeg)"
                :tone="focus?.axes.outer.atLimit ? 'warn' : undefined"
              />
              <StatTile
                label="Inner tilt"
                :value="signedDeg(focus?.axes.inner.tiltDeg)"
                :tone="focus?.axes.inner.atLimit ? 'warn' : undefined"
              />
            </div>

            <div v-if="preview" class="preview">
              <div class="label">If commanded</div>
              <div class="mono small">
                outer {{ num(preview.answer.outer.servoDeg, 1) }}&#176; &rarr;
                {{ us(preview.answer.outer.pwmUs) }}
              </div>
              <div class="mono small">
                inner {{ num(preview.answer.inner.servoDeg, 1) }}&#176; &rarr;
                {{ us(preview.answer.inner.pwmUs) }}
              </div>
              <p v-if="preview.answer.clamped" class="warn small">
                Outside the envelope; pulled back to
                {{ (preview.answer.scale * 100).toFixed(0) }}% so the thrust direction is kept.
              </p>
            </div>

            <SliderRow
              v-model="slewSpeed"
              label="Slew rate"
              :min="0"
              :max="limits?.servoSpeedDegS ?? 180"
              :step="5"
              :digits="0"
              unit=" &#176;/s"
              note="0 commands the new position immediately. A finite rate ramps to it, which is gentler on the linkage during setup."
            />

            <div class="row wrap">
              <button :disabled="disabled" @click="aimTo(10, 0)">Fwd 10&#176;</button>
              <button :disabled="disabled" @click="aimTo(-10, 0)">Aft 10&#176;</button>
              <button :disabled="disabled" @click="aimTo(0, 10)">Right 10&#176;</button>
              <button :disabled="disabled" @click="aimTo(0, -10)">Left 10&#176;</button>
            </div>
          </div>
        </div>
      </PanelCard>

      <!-- per-axis -->
      <PanelCard
        title="Axes"
        :note="focus ? `Drive ${focus.label}'s gimbal axes directly. Values are gimbal degrees, not servo degrees.` : ''"
        :accent="accent"
      >
        <div class="stack">
          <SliderRow
            :model-value="axisTilt.outer"
            label="Outer (roll ring)"
            :min="-(workspace?.tiltLimitDeg ?? 22.5)"
            :max="workspace?.tiltLimitDeg ?? 22.5"
            :step="0.5"
            :digits="1"
            unit="&#176;"
            :disabled="disabled || selection === ALL"
            :note="`Servo travel ${num(workspace?.outerServoNeededDeg, 0)}\u00b0 of ${num(focus?.axes.outer.servoLimitDeg, 0)}\u00b0 available.`"
            @update:model-value="driveAxis('outer', $event)"
            @pointerdown="axisHeld = 'outer'"
            @pointerup="axisHeld = null"
          />
          <SliderRow
            :model-value="axisTilt.inner"
            label="Inner (pitch ring)"
            :min="-(workspace?.tiltLimitDeg ?? 22.5)"
            :max="workspace?.tiltLimitDeg ?? 22.5"
            :step="0.5"
            :digits="1"
            unit="&#176;"
            :disabled="disabled || selection === ALL"
            :note="`Needs up to ${num(workspace?.innerServoNeededDeg, 0)}\u00b0 because it also carries the outer ring's motion.`"
            @update:model-value="driveAxis('inner', $event)"
            @pointerdown="axisHeld = 'inner'"
            @pointerup="axisHeld = null"
          />

          <p v-if="selection === ALL" class="faint small">
            Per-axis control targets one arm. Pick an arm above to use these.
          </p>

          <div class="axis-table">
            <div class="axis-row head">
              <span>Axis</span><span class="r">Pulse</span><span class="r">Servo</span
              ><span class="r">Tilt</span>
            </div>
            <div v-for="name in (['outer', 'inner'] as AxisName[])" :key="name" class="axis-row">
              <span>
                {{ name }}
                <span class="faint">ch{{ focus?.axes[name].channel }}</span>
              </span>
              <span class="r mono">{{ us(focus?.axes[name].pwm) }}</span>
              <span class="r mono">{{ signedDeg(focus?.axes[name].servoDeg) }}</span>
              <span class="r mono" :class="{ warn: focus?.axes[name].atLimit }">
                {{ signedDeg(focus?.axes[name].tiltDeg) }}
              </span>
            </div>
          </div>
        </div>
      </PanelCard>

      <!-- envelope facts -->
      <PanelCard
        title="Envelope"
        note="Derived from the config, so a calibration mistake shows up as a number here rather than as a stalled servo."
        :accent="accent"
      >
        <div class="tiles two">
          <StatTile
            label="Tilt limit"
            :value="num(workspace?.tiltLimitDeg, 1)"
            unit="&#176;"
            hint="Configured maximum on each axis"
          />
          <StatTile
            label="Reach, any direction"
            :value="num(workspace?.uniformTiltDeg, 1)"
            unit="&#176;"
            hint="Largest lean available in every direction. Budget authority against this, not the axis limit."
          />
          <StatTile label="Gear ratio" :value="num(workspace?.gearRatio, 1)" unit=":1" />
          <StatTile
            label="Coupling"
            :value="num(workspace?.coupling, 2)"
            hint="How much outer travel the inner command has to add back"
          />
          <StatTile
            label="Outer headroom"
            :value="signedDeg(workspace?.outerHeadroomDeg, 1)"
            :tone="(workspace?.outerHeadroomDeg ?? 0) < 0 ? 'danger' : 'ok'"
            hint="Spare servo travel beyond what full tilt needs"
          />
          <StatTile
            label="Inner headroom"
            :value="signedDeg(workspace?.innerHeadroomDeg, 1)"
            :tone="(workspace?.innerHeadroomDeg ?? 0) < 0 ? 'danger' : 'ok'"
          />
        </div>

        <p class="faint small mt">
          The inner axis needs {{ num(workspace?.innerServoNeededDeg, 0) }}&#176; of servo travel
          against the outer axis' {{ num(workspace?.outerServoNeededDeg, 0) }}&#176;, because it
          is driven from the airframe and has to undo the outer ring's motion as well as make
          its own.
        </p>
      </PanelCard>

      <!-- motors -->
      <PanelCard
        title="Motors"
        note="Selected motors are latched one after another through the motor test, then held together. A click can take a second to bring the whole set up. Clear the props, or take them off."
        :accent="accent"
      >
        <template #actions>
          <button class="danger tiny" :disabled="!canCommand" @click="send('stop_motors')">
            Stop
          </button>
        </template>

        <div class="stack">
          <div class="motor-table">
            <div class="motor-row head">
              <span>Motor</span><span>Spin</span><span class="r">Ch</span><span class="r">Fn</span
              ><span class="r">RPM</span>
            </div>
            <div v-for="(motor, name) in focus?.motors ?? {}" :key="name" class="motor-row">
              <label class="row">
                <input
                  type="checkbox"
                  :checked="motorPick[name as MotorName]"
                  :disabled="disabled || motorFunction(name as MotorName) === null"
                  @change="motorPick[name as MotorName] = ($event.target as HTMLInputElement).checked"
                />
                {{ name }}
              </label>
              <span class="faint">{{ motor?.spin }}{{ motor?.reversed ? ' rev' : '' }}</span>
              <span class="r mono">{{ motor?.channel }}</span>
              <span class="r mono" :class="{ danger: motorFunction(name as MotorName) === null }">
                {{ motorFunctionLabel(name as MotorName) }}
              </span>
              <span class="r mono">{{ motor?.rpm !== null ? num(motor?.rpm, 0) : '--' }}</span>
            </div>
          </div>

          <p v-if="unmapped.length" class="warn small">
            {{ unmapped.join(' and ') }} {{ unmapped.length > 1 ? 'have' : 'has' }} no
            <code>function</code> in the config, so
            {{ unmapped.length > 1 ? 'those outputs are' : 'that output is' }} Disabled and
            emits nothing. Set <strong>fn</strong> for that motor on the Setup page, then
            re-apply the output mapping.
          </p>

          <SliderRow
            v-model="motorPercent"
            label="Throttle"
            :min="1"
            :max="limits?.motorPercent ?? 50"
            :step="1"
            :digits="0"
            unit="%"
            :note="`Server clamps to ${num(limits?.motorPercent, 0)}%.`"
          />
          <SliderRow
            v-model="motorSeconds"
            label="Duration"
            :min="0.5"
            :max="limits?.motorSeconds ?? 10"
            :step="0.5"
            :digits="1"
            unit=" s"
          />

          <button
            class="primary"
            :disabled="disabled || !spinCount || isPending('spin_motors')"
            @click="spin"
          >
            Spin {{ spinCount || 'no' }}
            {{ spinCount === 1 ? 'motor' : 'motors together' }}
            at {{ motorPercent }}% for {{ motorSeconds }}s
          </button>
        </div>
      </PanelCard>
    </div>

    <PanelCard
      title="All arms"
      note="Commanding every live arm to one body-frame lean should swing all the arrows the same way. If one points elsewhere, its mount yaw or an axis sign is wrong."
      flush
    >
      <div class="all-arms">
        <FrameDiagram :arms="arms" :highlight="selection === ALL ? null : selection" />
        <div class="stack">
          <div class="row wrap">
            <button :disabled="!canCommand" @click="send('aim', { forward: 10, right: 0 })">
              All fwd 10&#176;
            </button>
            <button :disabled="!canCommand" @click="send('aim', { forward: 0, right: 10 })">
              All right 10&#176;
            </button>
            <button :disabled="!canCommand" @click="send('center')">Center all</button>
          </div>
          <div class="lean-list">
            <div v-for="(arm, index) in arms" :key="arm.id" class="lean-row">
              <span class="swatch" :style="{ background: armColor(arm.id, index) }" />
              <span class="grow">{{ arm.label }}</span>
              <StatusPill v-if="!arm.live" tone="idle">{{ arm.status }}</StatusPill>
              <span v-else class="mono small">
                {{ signedDeg(arm.thrust.forwardDeg) }} fwd /
                {{ signedDeg(arm.thrust.rightDeg) }} right
              </span>
            </div>
          </div>
        </div>
      </div>
    </PanelCard>
  </div>
</template>

<style scoped>
.tabs {
  display: flex;
  gap: var(--s2);
  flex-wrap: wrap;
}

.tab {
  display: flex;
  align-items: center;
  gap: var(--s2);
  background: var(--surface-1);
  border-color: var(--line);
  font-size: var(--fs-sm);
}

.tab.on {
  background: var(--accent-wash);
  border-color: var(--accent);
  color: var(--text);
}

.tab.planned {
  color: var(--text-faint);
}

.swatch {
  width: 8px;
  height: 8px;
  border-radius: 2px;
  flex: none;
}

.swatch.all {
  background: linear-gradient(135deg, var(--arm-north), var(--arm-east), var(--arm-south), var(--arm-west));
}

.count {
  font-size: var(--fs-xs);
  color: var(--text-faint);
  text-transform: uppercase;
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
  grid-template-columns: 1.35fr 1fr;
  gap: var(--s4);
  align-items: start;
}

.aim-grid {
  display: grid;
  grid-template-columns: minmax(200px, 270px) 1fr;
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

.preview {
  border: 1px dashed var(--line-strong);
  border-radius: var(--radius-sm);
  padding: var(--s2) var(--s3);
}

.small {
  font-size: var(--fs-xs);
  line-height: 1.5;
}

.mt {
  margin-top: var(--s3);
}

.axis-table,
.motor-table {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  overflow: hidden;
  font-size: var(--fs-sm);
}

.axis-row,
.motor-row {
  display: grid;
  gap: var(--s2);
  padding: 5px var(--s3);
  border-bottom: 1px solid var(--line);
  align-items: center;
}

.axis-row {
  grid-template-columns: 1.4fr 1fr 1fr 1fr;
}

.motor-row {
  grid-template-columns: 1.3fr 1fr 0.6fr 0.6fr 0.9fr;
}

.axis-row:last-child,
.motor-row:last-child {
  border-bottom: none;
}

.axis-row.head,
.motor-row.head {
  background: var(--surface-2);
  font-size: var(--fs-xs);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-faint);
}

.r {
  text-align: right;
}

.all-arms {
  display: grid;
  grid-template-columns: minmax(200px, 300px) 1fr;
  gap: var(--s4);
  padding: var(--s4);
  align-items: start;
}

.lean-list {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}

.lean-row {
  display: flex;
  align-items: center;
  gap: var(--s2);
  padding: var(--s2) var(--s3);
  border-bottom: 1px solid var(--line);
  font-size: var(--fs-sm);
}

.lean-row:last-child {
  border-bottom: none;
}

@media (max-width: 1150px) {
  .layout,
  .aim-grid,
  .all-arms {
    grid-template-columns: 1fr;
  }
}
</style>
