<!--
  Drag-to-aim pad for one arm.

  The axes are body frame, not gimbal frame: up is thrust leaning forward, right is
  thrust leaning right. That holds for every arm regardless of how its gimbal is
  bolted on, so the same gesture means the same thing on all four.

  The grey outline is the arm's real reachable envelope, computed by the server from
  the config. It is a square clipped by the inner servo's travel, not a circle, and
  drawing it honestly is the difference between "the servo stopped" and "I can see
  why the servo stopped".
-->
<script setup lang="ts">
import { computed, ref } from 'vue'

import type { Workspace } from '../lib/types'

const props = withDefaults(
  defineProps<{
    /** Reachable envelope in body axes, from the server. */
    workspace: Workspace | null
    /** Where the thrust is actually pointing now, in degrees. */
    actual?: { forwardDeg: number; rightDeg: number } | null
    /** Where it has been commanded to point, in degrees. */
    target?: { forward: number; right: number } | null
    disabled?: boolean
    accent?: string
    size?: number
  }>(),
  { disabled: false, accent: 'var(--accent)', size: 260, actual: null, target: null },
)

const emit = defineEmits<{
  aim: [forward: number, right: number]
  /** Fires continuously while dragging, so callers can stream at their own rate. */
  drag: [forward: number, right: number]
  release: []
}>()

const svg = ref<SVGSVGElement | null>(null)
const dragging = ref(false)
const hover = ref<{ forward: number; right: number } | null>(null)

/** Half-width of the plot in degrees, rounded out so the outline is not flush to the edge. */
const span = computed(() => {
  const limit = props.workspace?.tiltLimitDeg ?? 22.5
  return limit * 1.25
})

const toX = (rightDeg: number) => (rightDeg / span.value) * 100
const toY = (forwardDeg: number) => (-forwardDeg / span.value) * 100

const outline = computed(() => {
  const points = props.workspace?.body ?? []
  if (!points.length) return ''
  return points.map(([forward, right]) => `${toX(right)},${toY(forward)}`).join(' ')
})

const rings = computed(() => {
  const limit = props.workspace?.uniformTiltDeg ?? props.workspace?.tiltLimitDeg ?? 22.5
  return [limit / 3, (limit * 2) / 3, limit].map((value) => ({
    r: (value / span.value) * 100,
    label: value.toFixed(0),
  }))
})

function fromEvent(event: PointerEvent): { forward: number; right: number } | null {
  const element = svg.value
  if (!element) return null
  const box = element.getBoundingClientRect()
  const nx = ((event.clientX - box.left) / box.width) * 2 - 1
  const ny = ((event.clientY - box.top) / box.height) * 2 - 1
  return { forward: -ny * span.value, right: nx * span.value }
}

function onDown(event: PointerEvent) {
  if (props.disabled) return
  const point = fromEvent(event)
  if (!point) return
  dragging.value = true
  ;(event.target as Element).setPointerCapture?.(event.pointerId)
  emit('aim', point.forward, point.right)
  emit('drag', point.forward, point.right)
}

function onMove(event: PointerEvent) {
  const point = fromEvent(event)
  if (!point) return
  hover.value = point
  if (!dragging.value || props.disabled) return
  emit('drag', point.forward, point.right)
}

function onUp() {
  if (!dragging.value) return
  dragging.value = false
  emit('release')
}

const actualPoint = computed(() =>
  props.actual ? { x: toX(props.actual.rightDeg), y: toY(props.actual.forwardDeg) } : null,
)

const targetPoint = computed(() =>
  props.target ? { x: toX(props.target.right), y: toY(props.target.forward) } : null,
)
</script>

<template>
  <div class="pad-wrap" :style="{ '--pad-accent': accent, width: `${size}px` }">
    <svg
      ref="svg"
      class="pad"
      viewBox="-100 -100 200 200"
      :class="{ disabled }"
      @pointerdown="onDown"
      @pointermove="onMove"
      @pointerup="onUp"
      @pointercancel="onUp"
      @pointerleave="hover = null"
    >
      <!-- envelope -->
      <polygon v-if="outline" class="envelope" :points="outline" />

      <!-- graticule -->
      <circle v-for="ring in rings" :key="ring.r" class="ring" cx="0" cy="0" :r="ring.r" />
      <line class="axis" x1="-100" y1="0" x2="100" y2="0" />
      <line class="axis" x1="0" y1="-100" x2="0" y2="100" />

      <text class="tick" x="3" :y="-rings[rings.length - 1].r - 3">
        {{ rings[rings.length - 1].label }}&#176;
      </text>

      <!-- commanded -->
      <g v-if="targetPoint" class="target">
        <line :x1="0" :y1="0" :x2="targetPoint.x" :y2="targetPoint.y" />
        <circle :cx="targetPoint.x" :cy="targetPoint.y" r="5" />
      </g>

      <!-- measured -->
      <circle
        v-if="actualPoint"
        class="actual"
        :cx="actualPoint.x"
        :cy="actualPoint.y"
        r="3.5"
      />

      <text class="edge n" x="0" y="-90">FWD</text>
      <text class="edge s" x="0" y="94">AFT</text>
      <text class="edge e" x="90" y="4">R</text>
      <text class="edge w" x="-90" y="4">L</text>
    </svg>

    <div class="readout mono">
      <template v-if="hover && !disabled">
        fwd {{ hover.forward.toFixed(1) }}&#176; &nbsp; right {{ hover.right.toFixed(1) }}&#176;
      </template>
      <template v-else-if="actual">
        now fwd {{ actual.forwardDeg.toFixed(1) }}&#176; &nbsp; right
        {{ actual.rightDeg.toFixed(1) }}&#176;
      </template>
      <template v-else>&nbsp;</template>
    </div>
  </div>
</template>

<style scoped>
.pad-wrap {
  display: flex;
  flex-direction: column;
  gap: var(--s2);
}

.pad {
  width: 100%;
  aspect-ratio: 1;
  background: radial-gradient(circle at 50% 50%, var(--surface-2), var(--surface-1) 72%);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  touch-action: none;
  cursor: crosshair;
  display: block;
}

.pad.disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.envelope {
  fill: color-mix(in srgb, var(--pad-accent) 9%, transparent);
  stroke: color-mix(in srgb, var(--pad-accent) 40%, transparent);
  stroke-width: 1;
}

.ring {
  fill: none;
  stroke: var(--line);
  stroke-width: 0.7;
}

.axis {
  stroke: var(--line-strong);
  stroke-width: 0.7;
}

.tick,
.edge {
  fill: var(--text-faint);
  font-size: 8px;
  font-family: var(--mono);
}

.edge {
  text-anchor: middle;
  font-size: 7px;
  letter-spacing: 0.08em;
}

.target line {
  stroke: var(--pad-accent);
  stroke-width: 1.4;
  stroke-dasharray: 3 2;
}

.target circle {
  fill: none;
  stroke: var(--pad-accent);
  stroke-width: 1.8;
}

.actual {
  fill: var(--text);
  stroke: var(--bg);
  stroke-width: 1;
}

.readout {
  font-size: var(--fs-xs);
  color: var(--text-dim);
  text-align: center;
  min-height: 1.2em;
}
</style>
