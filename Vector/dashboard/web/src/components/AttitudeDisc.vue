<!--
  Attitude readout drawn as a tilt disc rather than an artificial horizon.

  For this vehicle the useful question is "how far off vertical is the body, and in
  which direction", because that is exactly what the gimbals have to cancel. A disc
  answers it in one glance; a horizon ribbon does not. The optional second marker
  shows the lean the levelling controller wants, so the two can be compared directly.
-->
<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    rollDeg: number
    pitchDeg: number
    yawDeg?: number
    /** Commanded gimbal lean, drawn as a hollow ring. */
    lean?: { forward: number; right: number } | null
    /** Degrees from centre to the outer ring. */
    fullScaleDeg?: number
    /** Ring marking the gimbals' correction envelope. */
    envelopeDeg?: number | null
  }>(),
  { fullScaleDeg: 45, lean: null, envelopeDeg: null, yawDeg: 0 },
)

const scale = computed(() => 82 / props.fullScaleDeg)

/**
 * Body tilt as a point on the disc: distance is how far from vertical, direction is
 * which way the top of the airframe has fallen. Nose up puts the marker at the top.
 */
const body = computed(() => ({
  x: props.rollDeg * scale.value,
  y: -props.pitchDeg * scale.value,
}))

const leanPoint = computed(() =>
  props.lean
    ? { x: props.lean.right * scale.value, y: -props.lean.forward * scale.value }
    : null,
)

const envelope = computed(() =>
  props.envelopeDeg ? props.envelopeDeg * scale.value : null,
)

const rings = computed(() => {
  const step = props.fullScaleDeg / 3
  return [step, step * 2, step * 3].map((value) => ({
    r: value * scale.value,
    label: value.toFixed(0),
  }))
})

const tilt = computed(() => Math.hypot(props.rollDeg, props.pitchDeg))
</script>

<template>
  <svg class="disc" viewBox="-100 -100 200 200">
    <circle v-for="ring in rings" :key="ring.r" class="ring" cx="0" cy="0" :r="ring.r" />
    <circle
      v-if="envelope"
      class="envelope"
      cx="0"
      cy="0"
      :r="envelope"
    />

    <line class="axis" x1="-88" y1="0" x2="88" y2="0" />
    <line class="axis" x1="0" y1="-88" x2="0" y2="88" />

    <text class="tick" x="2" :y="-rings[2].r - 3">{{ rings[2].label }}&#176;</text>
    <text class="tick" x="2" :y="-rings[1].r - 3">{{ rings[1].label }}&#176;</text>

    <!-- where the levelling controller wants the thrust -->
    <circle
      v-if="leanPoint"
      class="lean"
      :cx="leanPoint.x"
      :cy="leanPoint.y"
      r="7"
    />

    <!-- body attitude -->
    <line class="stem" x1="0" y1="0" :x2="body.x" :y2="body.y" />
    <circle class="body" :cx="body.x" :cy="body.y" r="4.5" />

    <text class="edge" x="0" y="-91">NOSE UP</text>
    <text class="edge" x="0" y="96">NOSE DN</text>

    <text class="value mono" x="0" y="-32">{{ tilt.toFixed(1) }}&#176;</text>
    <text class="value-label" x="0" y="-22">off vertical</text>
  </svg>
</template>

<style scoped>
.disc {
  width: 100%;
  aspect-ratio: 1;
  display: block;
}

.ring {
  fill: none;
  stroke: var(--line);
  stroke-width: 0.7;
}

.envelope {
  fill: color-mix(in srgb, var(--accent) 7%, transparent);
  stroke: color-mix(in srgb, var(--accent) 38%, transparent);
  stroke-width: 1;
  stroke-dasharray: 3 2;
}

.axis {
  stroke: var(--line-strong);
  stroke-width: 0.7;
}

.stem {
  stroke: var(--warn);
  stroke-width: 1.6;
}

.body {
  fill: var(--warn);
  stroke: var(--bg);
  stroke-width: 1;
}

.lean {
  fill: none;
  stroke: var(--accent);
  stroke-width: 2;
}

.tick,
.edge,
.value,
.value-label {
  font-family: var(--mono);
  fill: var(--text-faint);
  font-size: 7px;
}

.edge,
.value,
.value-label {
  text-anchor: middle;
}

.edge {
  letter-spacing: 0.08em;
}

.value {
  fill: var(--text);
  font-size: 15px;
}

.value-label {
  font-size: 6px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}
</style>
