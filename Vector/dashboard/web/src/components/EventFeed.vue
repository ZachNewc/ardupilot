<!--
  Merged feed of commands the dashboard sent and messages the vehicle sent back.

  They are interleaved on purpose: when a command is refused, the vehicle's
  explanation is almost always the next line, and separating the two streams into
  different panels would hide that.
-->
<script setup lang="ts">
import { computed } from 'vue'

import { clockTime } from '../lib/format'
import store, { events } from '../lib/store'

const props = withDefaults(defineProps<{ limit?: number }>(), { limit: 60 })

interface Row {
  key: string
  at: number
  kind: string
  text: string
}

const rows = computed<Row[]>(() => {
  const fromVehicle: Row[] = events.value.map((event) => ({
    key: `v${event.id}`,
    at: event.at,
    kind: event.kind,
    text: event.text,
  }))

  const fromUs: Row[] = store.acks.map((ack) => ({
    key: `a${ack.id}`,
    at: ack.at,
    kind: ack.ok ? 'sent' : 'error',
    text: `${ack.command}: ${ack.text}`,
  }))

  return [...fromVehicle, ...fromUs].sort((a, b) => b.at - a.at).slice(0, props.limit)
})
</script>

<template>
  <div class="feed">
    <p v-if="!rows.length" class="empty faint">Nothing yet.</p>
    <div v-for="row in rows" :key="row.key" class="line" :class="row.kind">
      <span class="time mono">{{ clockTime(row.at) }}</span>
      <span class="kind">{{ row.kind }}</span>
      <span class="text">{{ row.text }}</span>
    </div>
  </div>
</template>

<style scoped>
.feed {
  display: flex;
  flex-direction: column;
  max-height: 320px;
  overflow-y: auto;
  font-size: var(--fs-sm);
}

.empty {
  padding: var(--s3) var(--s4);
  margin: 0;
}

.line {
  display: grid;
  grid-template-columns: 62px 62px 1fr;
  gap: var(--s2);
  padding: 5px var(--s4);
  border-bottom: 1px solid var(--line);
  align-items: baseline;
}

.line:last-child {
  border-bottom: none;
}

.time {
  color: var(--text-faint);
  font-size: var(--fs-xs);
}

.kind {
  font-size: var(--fs-xs);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-faint);
}

.text {
  color: var(--text-dim);
  word-break: break-word;
}

.error .kind,
.error .text {
  color: var(--danger);
}

.sent .kind {
  color: var(--accent);
}

.vehicle .kind {
  color: var(--ok);
}
</style>
