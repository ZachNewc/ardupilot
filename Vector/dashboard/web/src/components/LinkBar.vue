<!--
  Top bar: link state, armed state, and the two controls that must always be one
  click away no matter which page is open.

  "Stop" cuts motor tests, drops the levelling loop, and stops a circling sweep; "Center" returns every live
  gimbal to neutral. On a bench with propellers fitted, hunting through pages for
  those is not acceptable, so they live here.
-->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import { age, num, pct } from '../lib/format'
import { isMotorKillKey, isTypingTarget } from '../lib/keys'
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
  send('oscillate', { active: false })
  send('level', { active: false })
  send('stop_motors')
  send('live_end')
}

function killMotors() {
  send('stop_motors')
}

function onKeydown(event: KeyboardEvent) {
  if (!isMotorKillKey(event)) return
  // A kill switch has to work even while a field is focused. Do not steal the
  // character from the field; just cut the motors.
  if (!isTypingTarget(event.target)) {
    event.preventDefault()
  }
  killMotors()
}

onMounted(() => window.addEventListener('keydown', onKeydown, true))
onUnmounted(() => window.removeEventListener('keydown', onKeydown, true))
</script>

<template>
  <header class="bar">
    <div class="row" style="gap: var(--s3)">
      <StatusPill :tone="linkTone" :pulse="linkTone === 'warn'">{{ linkText }}</StatusPill>

      <StatusPill v-if="vehicle?.armed" tone="danger" pulse>armed</StatusPill>
      <StatusPill v-else-if="linkUp" tone="ok">disarmed</StatusPill>

      <StatusPill v-if="controller?.active" tone="accent" pulse>levelling</StatusPill>
      <StatusPill v-if="store.state?.outputs.oscillateActive" tone="accent" pulse>circling</StatusPill>

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
        <button
          class="danger"
          title="Stop motor tests, circling, and the levelling loop. X stops motors immediately from any page."
          @click="stopAll"
        >
          Stop
        </button>
        <span class="hint faint" title="Press X on any page to zero every motor test immediately">
          <kbd>X</kbd> motors
        </span>
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

.hint {
  font-size: var(--fs-xs);
  display: inline-flex;
  align-items: center;
  gap: 4px;
  user-select: none;
}

.hint kbd {
  display: inline-grid;
  place-items: center;
  min-width: 1.4em;
  padding: 1px 5px;
  border: 1px solid var(--line-strong);
  border-bottom-width: 2px;
  border-radius: 4px;
  background: var(--surface-2);
  color: var(--text);
  font-size: 11px;
  line-height: 1.4;
}

@media (max-width: 1000px) {
  .stats {
    display: none;
  }
}

@media (max-width: 820px) {
  .hint {
    display: none;
  }
}
</style>
