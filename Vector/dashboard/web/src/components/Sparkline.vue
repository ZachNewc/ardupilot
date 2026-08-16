<!--
  Rolling trace for one or more series.

  Deliberately minimal: no axes, no legend, no library. The y-range is either fixed
  by the caller or symmetric about zero, because every signal on this dashboard is a
  signed deviation and a chart that rescales itself hides exactly the drift you are
  looking for.
-->
<script setup lang="ts">
import { computed } from 'vue'

export interface Series {
  label: string
  color: string
  values: number[]
}

const props = withDefaults(
  defineProps<{
    series: Series[]
    /** Fixed half-range. Omit to scale to the data, rounded out. */
    range?: number | null
    height?: number
    unit?: string
  }>(),
  { range: null, height: 96, unit: '' },
)

const WIDTH = 300

const span = computed(() => {
  if (props.range) return props.range
  const peak = Math.max(
    1,
    ...props.series.flatMap((entry) => entry.values.map((value) => Math.abs(value))),
  )
  // Round out so the trace is not flush against the frame.
  return peak * 1.15
})

const count = computed(() =>
  Math.max(2, ...props.series.map((entry) => entry.values.length)),
)

function path(values: number[]): string {
  if (values.length < 2) return ''
  const stepX = WIDTH / (count.value - 1)
  return values
    .map((value, index) => {
      const x = index * stepX
      const y = 50 - (value / span.value) * 50
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(2)},${Math.max(-2, Math.min(102, y)).toFixed(2)}`
    })
    .join(' ')
}

const latest = computed(() =>
  props.series.map((entry) => ({
    label: entry.label,
    color: entry.color,
    value: entry.values.length ? entry.values[entry.values.length - 1] : 0,
  })),
)
</script>

<template>
  <div class="spark">
    <svg :viewBox="`0 0 ${WIDTH} 100`" preserveAspectRatio="none" :style="{ height: `${height}px` }">
      <line class="zero" x1="0" y1="50" :x2="WIDTH" y2="50" />
      <line class="grid" x1="0" y1="25" :x2="WIDTH" y2="25" />
      <line class="grid" x1="0" y1="75" :x2="WIDTH" y2="75" />
      <path
        v-for="entry in series"
        :key="entry.label"
        :d="path(entry.values)"
        :style="{ stroke: entry.color }"
      />
    </svg>
    <div class="foot">
      <span v-for="entry in latest" :key="entry.label" class="item">
        <span class="key" :style="{ background: entry.color }" />
        <span class="faint">{{ entry.label }}</span>
        <span class="mono">{{ entry.value.toFixed(1) }}{{ unit }}</span>
      </span>
      <span class="grow" />
      <span class="faint scale mono">&plusmn;{{ span.toFixed(0) }}{{ unit }}</span>
    </div>
  </div>
</template>

<style scoped>
.spark {
  display: flex;
  flex-direction: column;
  gap: var(--s1);
}

svg {
  width: 100%;
  display: block;
  background: var(--surface-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}

path {
  fill: none;
  stroke-width: 1.4;
  vector-effect: non-scaling-stroke;
  stroke-linejoin: round;
}

.zero {
  stroke: var(--line-strong);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}

.grid {
  stroke: var(--line);
  stroke-width: 1;
  stroke-dasharray: 4 4;
  vector-effect: non-scaling-stroke;
}

.foot {
  display: flex;
  align-items: center;
  gap: var(--s3);
  font-size: var(--fs-xs);
  flex-wrap: wrap;
}

.item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.key {
  width: 7px;
  height: 7px;
  border-radius: 2px;
}

.scale {
  color: var(--text-faint);
}
</style>
