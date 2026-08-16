<!--
  Top bar: link state, armed state, and the two controls that must always be one
  click away no matter which page is open.

  "Stop" cuts motor tests and drops the levelling loop; "Center" returns every live
  gimbal to neutral. On a bench with propellers fitted, hunting through pages for
  those is not acceptable, so they live here.
-->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { age, num, pct } from '../lib/format'
import store, { isPending, linkUp, send } from '../lib/store'
import StatusPill from './StatusPill.vue'

const device = ref('')

// Follow the config's device until the operator picks something else.
watch(
  () => store.config?.link.device,
  (value) => {
    if (value && !device.value) device.value = value
  },
  { immediate: true },
)

watch(
  () => store.ports,
  (ports) => {
    if (!device.value && ports.length) device.value = ports[0].device
  },
)

const link = computed(() => store.state?.link ?? null)
const vehicle = computed(() => store.state?.vehicle ?? null)
const controller = computed(() => store.state?.controller ?? null)

const linkTone = computed(() => {
  if (!store.socketOpen) return 'danger'
  if (!linkUp.value) return 'idle'
  const heartbeat = link.value?.heartbeatAgeS
  if (!link.value?.readerAlive) return 'danger'
  if (heartbeat === null || heartbeat === undefined || heartbeat > 3) return 'warn'
  return 'ok'
})

const linkText = computed(() => {
  if (!store.socketOpen) return 'no server'
  if (!linkUp.value) return 'offline'
  if (!link.value?.readerAlive) return 'reader dead'
  const heartbeat = link.value?.heartbeatAgeS
  if (heartbeat === null || heartbeat === undefined) return 'no heartbeat'
  if (heartbeat > 3) return `stale ${age(heartbeat)}`
  return 'linked'
})

const budget = computed(() => link.value?.budget ?? null)

function connect() {
  send('connect', { device: device.value, baud: store.config?.link.baud ?? 115200 })
}

/** One button that quiets everything the dashboard could be driving. */
function stopAll() {
  send('level', { active: false })
  send('stop_motors')
  send('live_end')
}
</script>

<template>
  <header class="bar">
    <div class="row" style="gap: var(--s3)">
      <StatusPill :tone="linkTone" :pulse="linkTone === 'warn'">{{ linkText }}</StatusPill>

      <StatusPill v-if="vehicle?.armed" tone="danger" pulse>armed</StatusPill>
      <StatusPill v-else-if="linkUp" tone="ok">disarmed</StatusPill>

      <StatusPill v-if="controller?.active" tone="accent" pulse>levelling</StatusPill>

      <span v-if="vehicle?.mode && linkUp" class="mode mono">{{ vehicle.mode }}</span>
    </div>

    <div class="grow" />

    <div v-if="linkUp && budget" class="stats mono">
      <span :class="{ warn: budget.overBudget }" :title="`${budget.channels} servo channels at ${budget.rateHz} Hz`">
        link {{ pct(budget.utilisation) }}
      </span>
      <span v-if="vehicle" :title="'Flight controller CPU load'">cpu {{ num(vehicle.loadPercent, 0) }}%</span>
      <span v-if="vehicle?.voltage" :title="'Pack voltage'">{{ num(vehicle.voltage, 2) }} V</span>
    </div>

    <div class="row">
      <template v-if="!linkUp">
        <select v-model="device" class="port" :disabled="!store.socketOpen">
          <option v-if="!store.ports.length" :value="device">{{ device || 'no ports found' }}</option>
          <option v-for="port in store.ports" :key="port.device" :value="port.device">
            {{ port.device }} &mdash; {{ port.label }}
          </option>
        </select>
        <button class="ghost tiny" title="Rescan serial ports" @click="send('ports')">Scan</button>
        <button
          class="primary"
          :disabled="!store.socketOpen || isPending('connect') || !device"
          @click="connect"
        >
          {{ isPending('connect') ? 'Connecting...' : 'Connect' }}
        </button>
      </template>

      <template v-else>
        <button class="ghost" title="Return every live gimbal to neutral" @click="send('center')">
          Center
        </button>
        <button class="danger" title="Stop motor tests and drop the levelling loop" @click="stopAll">
          Stop
        </button>
        <button class="ghost" @click="send('disconnect')">Disconnect</button>
      </template>
    </div>
  </header>
</template>

<style scoped>
.bar {
  height: var(--topbar-height);
  flex: none;
  display: flex;
  align-items: center;
  gap: var(--s3);
  padding: 0 var(--s4);
  border-bottom: 1px solid var(--line);
  background: var(--surface-1);
}

.mode {
  font-size: var(--fs-sm);
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.stats {
  display: flex;
  gap: var(--s4);
  font-size: var(--fs-xs);
  color: var(--text-faint);
}

.port {
  width: auto;
  min-width: 170px;
  max-width: 260px;
  font-size: var(--fs-sm);
  padding: 5px var(--s2);
}

@media (max-width: 1000px) {
  .stats {
    display: none;
  }
}
</style>
