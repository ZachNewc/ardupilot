<!--
  Top-down view of the airframe with each arm's current thrust lean drawn as an arrow.

  Orientation is body frame: forward (nose, the north arm) is up, right is right. The
  arrows are the horizontal component of each rotor pair's thrust, so at rest they
  vanish and under a levelling correction they all swing the same way. Planned arms
  are drawn dashed, which makes the modular build state visible without a legend.
-->
<script setup lang="ts">
import { computed } from 'vue'

import { armColor } from '../lib/format'
import type { ArmState } from '../lib/types'

const props = withDefaults(
  defineProps<{
    arms: ArmState[]
    /** Degrees of lean drawn as a full-length arrow. */
    fullScaleDeg?: number
    showLabels?: boolean
    highlight?: string | null
  }>(),
  { fullScaleDeg: 22.5, showLabels: true, highlight: null },
)

const HUB = 62
const ARROW = 30

const rad = (deg: number) => (deg * Math.PI) / 180

interface Placed {
  arm: ArmState
  color: string
  hub: { x: number; y: number }
  tip: { x: number; y: number }
  lean: number
  label: { x: number; y: number }
}

/**
 * Body frame to screen: body +X (forward) is screen up, body +Y (right) is screen
 * right. Everything in this component goes through here so the mapping is stated once.
 */
const project = (forward: number, right: number) => ({ x: right, y: -forward })

const placed = computed<Placed[]>(() =>
  props.arms.map((arm, index) => {
    const psi = rad(arm.azimuthDeg)
    const hub = project(HUB * Math.cos(psi), HUB * Math.sin(psi))
    const scale = ARROW / props.fullScaleDeg
    const lean = Math.hypot(arm.thrust.forwardDeg, arm.thrust.rightDeg)
    const vector = project(arm.thrust.forwardDeg * scale, arm.thrust.rightDeg * scale)
    const label = project(HUB * 1.3 * Math.cos(psi), HUB * 1.3 * Math.sin(psi))
    return {
      arm,
      color: armColor(arm.id, index),
      hub,
      tip: { x: hub.x + vector.x, y: hub.y + vector.y },
      lean,
      label,
    }
  }),
)
</script>

<template>
  <svg class="frame" viewBox="-100 -100 200 200">
    <defs>
      <marker
        id="lean-arrow"
        viewBox="0 0 8 8"
        refX="6"
        refY="4"
        markerWidth="5"
        markerHeight="5"
        orient="auto"
      >
        <path d="M0,0 L8,4 L0,8 z" fill="context-stroke" />
      </marker>
    </defs>

    <!-- nose marker: the north arm is the vehicle's forward direction -->
    <path class="nose" d="M0,-92 L-5,-84 L5,-84 z" />

    <g v-for="item in placed" :key="item.arm.id" :class="{ dim: !item.arm.live }">
      <line
        class="boom"
        :style="{ stroke: item.color }"
        x1="0"
        y1="0"
        :x2="item.hub.x"
        :y2="item.hub.y"
        :stroke-dasharray="item.arm.live ? undefined : '4 3'"
      />

      <!-- coaxial pair: two circles, offset slightly to read as over and under -->
      <circle class="rotor" :style="{ stroke: item.color }" :cx="item.hub.x" :cy="item.hub.y" r="17" />
      <circle
        class="rotor inner"
        :style="{ stroke: item.color }"
        :cx="item.hub.x"
        :cy="item.hub.y"
        r="12"
      />

      <line
        v-if="item.lean > 0.15"
        class="lean"
        :style="{ stroke: item.color }"
        marker-end="url(#lean-arrow)"
        :x1="item.hub.x"
        :y1="item.hub.y"
        :x2="item.tip.x"
        :y2="item.tip.y"
      />

      <circle
        v-if="props.highlight === item.arm.id"
        class="halo"
        :style="{ stroke: item.color }"
        :cx="item.hub.x"
        :cy="item.hub.y"
        r="23"
      />

      <text
        v-if="showLabels"
        class="tag"
        :style="{ fill: item.color }"
        :x="item.label.x"
        :y="item.label.y + 3"
      >
        {{ item.arm.label }}
      </text>
    </g>

    <circle class="hub" cx="0" cy="0" r="13" />
    <text class="hub-tag" x="0" y="3">FC</text>
  </svg>
</template>

<style scoped>
.frame {
  width: 100%;
  aspect-ratio: 1;
  display: block;
}

.boom {
  stroke-width: 3.5;
  stroke-linecap: round;
  opacity: 0.55;
}

.rotor {
  fill: none;
  stroke-width: 1.4;
  opacity: 0.75;
}

.rotor.inner {
  opacity: 0.35;
  stroke-dasharray: 2 2;
}

.lean {
  stroke-width: 2.2;
  stroke-linecap: round;
}

.halo {
  fill: none;
  stroke-width: 1;
  opacity: 0.6;
  stroke-dasharray: 3 3;
}

.dim {
  opacity: 0.32;
}

.hub {
  fill: var(--surface-3);
  stroke: var(--line-strong);
  stroke-width: 1;
}

.hub-tag,
.tag {
  font-family: var(--mono);
  text-anchor: middle;
}

.hub-tag {
  fill: var(--text-faint);
  font-size: 7px;
}

.tag {
  font-size: 8px;
  letter-spacing: 0.04em;
}

.nose {
  fill: var(--text-faint);
}
</style>
