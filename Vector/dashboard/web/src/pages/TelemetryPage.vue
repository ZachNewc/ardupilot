<!--
  Telemetry: everything the vehicle is reporting, plus a short rolling history.

  History is kept in the browser rather than the server: the server's job is to
  report the present, and a few hundred samples per signal is all a bench session
  needs. For real analysis, read the flight controller's log.
-->
<script setup lang="ts">
import { computed, onUnmounted, reactive, watch } from 'vue'

import PanelCard from '../components/PanelCard.vue'
import Sparkline, { type Series } from '../components/Sparkline.vue'
import StatTile from '../components/StatTile.vue'
import StatusPill from '../components/StatusPill.vue'
import { age, armColor, num, pct, signedDeg, us } from '../lib/format'
import store, { arms, isPending, linkUp, send } from '../lib/store'

const HISTORY = 240

const telemProtocolHint = computed(() => {
  const serials = store.config?.link.escTelemetrySerials ?? []
  if (!serials.length) {
    return 'SERIALn_PROTOCOL'
  }
  return serials.map((n) => `SERIAL${n}_PROTOCOL`).join(', ')
})

const history = reactive<Record<string, number[]>>({
  roll: [],
  pitch: [],
  yaw: [],
  rollRate: [],
  pitchRate: [],
  yawRate: [],
  voltage: [],
  current: [],
  leanForward: [],
  leanRight: [],
})

function push(key: string, value: number) {
  const buffer = history[key]
  buffer.push(value)
  if (buffer.length > HISTORY) buffer.shift()
}

const stop = watch(
  () => store.state,
  (state) => {
    if (!state) return
    push('roll', state.vehicle.rollDeg)
    push('pitch', state.vehicle.pitchDeg)
    push('yaw', state.vehicle.yawDeg)
    push('rollRate', state.vehicle.rollRateDegS)
    push('pitchRate', state.vehicle.pitchRateDegS)
    push('yawRate', state.vehicle.yawRateDegS)
    push('voltage', state.vehicle.voltage)
    push('current', state.vehicle.current)
    push('leanForward', state.controller.target.forward)
    push('leanRight', state.controller.target.right)
  },
)

onUnmounted(stop)

const vehicle = computed(() => store.state?.vehicle ?? null)
const link = computed(() => store.state?.link ?? null)
const escs = computed(() => store.state?.escs ?? [])

const attitudeSeries = computed<Series[]>(() => [
  { label: 'roll', color: 'var(--arm-north)', values: history.roll },
  { label: 'pitch', color: 'var(--arm-east)', values: history.pitch },
])

const rateSeries = computed<Series[]>(() => [
  { label: 'roll', color: 'var(--arm-north)', values: history.rollRate },
  { label: 'pitch', color: 'var(--arm-east)', values: history.pitchRate },
  { label: 'yaw', color: 'var(--arm-south)', values: history.yawRate },
])

const leanSeries = computed<Series[]>(() => [
  { label: 'fwd', color: 'var(--accent)', values: history.leanForward },
  { label: 'right', color: 'var(--arm-west)', values: history.leanRight },
])

const powerSeries = computed<Series[]>(() => [
  { label: 'amps', color: 'var(--warn)', values: history.current },
])

/** Cell voltage is the number that actually matters for a li-ion pack. */
const cellVolts = computed(() => {
  const volts = vehicle.value?.voltage ?? 0
  const cells = 6
  return volts ? volts / cells : 0
})

const cellTone = computed(() => {
  const value = cellVolts.value
  if (!value) return 'dim'
  if (value < 3.2) return 'danger'
  if (value < 3.4) return 'warn'
  return 'ok'
})

/** Message name to count, busiest first: a missing stream stands out as an absence. */
const messageCounts = computed(() =>
  Object.entries(store.state?.messageCounts ?? {}).sort((a, b) => b[1] - a[1]),
)

/**
 * Colour each reporting ESC by the arm that owns its channel, so a telemetry wire
 * plugged into the wrong ESC shows up as a colour that does not match its label.
 */
const escColors = computed(() => {
  const colors: Record<number, string> = {}
  arms.value.forEach((arm, index) => {
    Object.values(arm.motors).forEach((motor) => {
      if (motor) colors[motor.channel] = armColor(arm.id, index)
    })
  })
  return colors
})
</script>

<template>
  <div class="stack">
    <div class="tiles top">
      <StatTile
        label="Cell"
        :value="num(cellVolts, 2)"
        unit="V"
        :tone="cellTone"
        hint="Pack voltage divided by 6 cells in series"
        wide
      />
      <StatTile label="Pack" :value="num(vehicle?.voltage, 2)" unit="V" wide />
      <StatTile label="Current" :value="num(vehicle?.current, 1)" unit="A" wide />
      <StatTile
        label="Remaining"
        :value="num(vehicle?.batteryRemaining, 0)"
        unit="%"
        hint="Reported by the flight controller; only as good as its capacity setting"
        wide
      />
      <StatTile label="FC load" :value="num(vehicle?.loadPercent, 0)" unit="%" wide />
      <StatTile
        label="Heartbeat"
        :value="age(link?.heartbeatAgeS)"
        :tone="(link?.heartbeatAgeS ?? 99) > 3 ? 'warn' : 'dim'"
        wide
      />
    </div>

    <div class="charts">
      <PanelCard title="Attitude" note="Roll and pitch, the deviation the gimbals exist to cancel.">
        <template #actions>
          <span class="mono faint small">{{ history.roll.length }} samples</span>
        </template>
        <Sparkline :series="attitudeSeries" unit="&#176;" :height="110" />
        <div class="tiles three mt">
          <StatTile label="Roll" :value="signedDeg(vehicle?.rollDeg)" />
          <StatTile label="Pitch" :value="signedDeg(vehicle?.pitchDeg)" />
          <StatTile label="Yaw" :value="num(vehicle?.yawDeg, 0)" unit="&#176;" />
        </div>
      </PanelCard>

      <PanelCard title="Body rates" note="The gyro signal the lead term extrapolates from.">
        <Sparkline :series="rateSeries" unit="&#176;/s" :height="110" />
        <div class="tiles three mt">
          <StatTile label="Roll" :value="num(vehicle?.rollRateDegS, 1)" unit="&#176;/s" />
          <StatTile label="Pitch" :value="num(vehicle?.pitchRateDegS, 1)" unit="&#176;/s" />
          <StatTile label="Yaw" :value="num(vehicle?.yawRateDegS, 1)" unit="&#176;/s" />
        </div>
      </PanelCard>

      <PanelCard
        title="Commanded lean"
        note="What the levelling loop asked the gimbals for. Flat at zero when it is not running."
      >
        <Sparkline :series="leanSeries" unit="&#176;" :range="store.state?.controller.tiltCapDeg ?? 22.5" :height="110" />
        <div class="tiles three mt">
          <StatTile label="Forward" :value="signedDeg(store.state?.controller.target.forward)" />
          <StatTile label="Right" :value="signedDeg(store.state?.controller.target.right)" />
          <StatTile
            label="Saturated"
            :value="store.state?.controller.saturated ? 'yes' : 'no'"
            :tone="store.state?.controller.saturated ? 'warn' : 'dim'"
          />
        </div>
      </PanelCard>

      <PanelCard title="Current draw" note="Pack current. Spikes here line up with servo and motor activity.">
        <Sparkline :series="powerSeries" unit=" A" :height="110" />
        <div class="tiles three mt">
          <StatTile label="Now" :value="num(vehicle?.current, 1)" unit="A" />
          <StatTile
            label="Peak seen"
            :value="num(Math.max(0, ...history.current), 1)"
            unit="A"
          />
          <StatTile label="Mode" :value="vehicle?.mode || '--'" />
        </div>
      </PanelCard>
    </div>

    <PanelCard
      title="Link"
      note="The serial budget matters here: eight servos streamed at speed will not fit on a telemetry radio, though USB has ample room."
    >
      <div class="tiles four">
        <StatTile label="Device" :value="link?.device || '--'" />
        <StatTile label="Baud" :value="link?.baud ?? '--'" />
        <StatTile
          label="Servo channels"
          :value="link?.budget.channels ?? 0"
          hint="Live gimbal outputs the dashboard may drive"
        />
        <StatTile
          label="Budget used"
          :value="pct(link?.budget.utilisation)"
          :tone="link?.budget.overBudget ? 'danger' : 'ok'"
          :hint="`${num(link?.budget.bytesPerSecond, 0)} of ${num(link?.budget.capacityBytesPerSecond, 0)} bytes/s at ${num(link?.budget.rateHz, 0)} Hz`"
        />
      </div>
      <div class="row mt">
        <button class="ghost" :disabled="!linkUp || isPending('refresh_streams')" @click="send('refresh_streams')">
          Re-request streams
        </button>
        <button class="ghost" :disabled="!linkUp" @click="send('assert_mapping')">
          Re-apply output mapping
        </button>
        <span v-if="link?.lastError" class="danger small">{{ link.lastError }}</span>
      </div>
      <div v-if="messageCounts.length" class="counts mt">
        <span v-for="[name, count] in messageCounts" :key="name" class="count">
          <span class="faint">{{ name }}</span>
          <span class="mono">{{ count }}</span>
        </span>
      </div>
      <p v-else class="faint small mt">
        No messages counted yet. If this stays empty while connected, the stream rates were
        never granted &mdash; try re-requesting them.
      </p>
    </PanelCard>

    <PanelCard
      title="ESC telemetry"
      :note="
        escs.length
          ? `${escs.length} of ${arms.length * 2} ESCs reporting. Labels come from the arm that owns each output channel.`
          : 'Nothing reporting. Optional: the rest of the dashboard works without it.'
      "
      flush
    >
      <div v-if="escs.length" class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Channel</th>
              <th>Owner</th>
              <th class="r">RPM</th>
              <th class="r">Temp</th>
              <th class="r">Volts</th>
              <th class="r">Amps</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="esc in escs" :key="esc.index">
              <td class="mono">{{ esc.index }}</td>
              <td>
                <span
                  v-if="escColors[esc.index]"
                  class="swatch"
                  :style="{ background: escColors[esc.index] }"
                />
                {{ esc.label }}
              </td>
              <td class="r mono">{{ num(esc.rpm, 0) }}</td>
              <td
                class="r mono"
                :class="{
                  warn: (esc.temperatureC ?? 0) > 80,
                  danger: (esc.temperatureC ?? 0) > 100,
                }"
              >
                {{ num(esc.temperatureC, 0) }}&#176;C
              </td>
              <td class="r mono">{{ num(esc.voltage, 2) }}</td>
              <td class="r mono">{{ num(esc.current, 1) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="pad stack">
        <p class="faint" style="margin: 0">
          To enable it: connect each DShot ESC's T wire to RX3 (SERIAL4) and RX4
          (SERIAL6), set those ports'
          <code>{{ telemProtocolHint }}</code>
          to 16 (ESC Telemetry), and reboot. CAN ESCs report over DroneCAN, not these pins.
        </p>
        <StatusPill tone="idle">optional</StatusPill>
      </div>
    </PanelCard>

    <PanelCard title="Servo outputs" note="Pulse widths as the flight controller reports them, not as the dashboard last asked for. Divergence means something else is writing." flush>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Arm</th>
              <th>Axis</th>
              <th class="r">Channel</th>
              <th class="r">Pulse</th>
              <th class="r">Servo</th>
              <th class="r">Tilt</th>
              <th class="r">Limit</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="(arm, index) in arms" :key="arm.id">
              <tr v-for="axis in (['outer', 'inner'] as const)" :key="axis" :class="{ planned: !arm.live }">
                <td>
                  <span class="swatch" :style="{ background: armColor(arm.id, index) }" />
                  {{ arm.label }}
                </td>
                <td class="faint">{{ axis }}</td>
                <td class="r mono">{{ arm.axes[axis].channel }}</td>
                <td class="r mono">{{ us(arm.axes[axis].pwm) }}</td>
                <td class="r mono">{{ signedDeg(arm.axes[axis].servoDeg) }}</td>
                <td class="r mono">{{ signedDeg(arm.axes[axis].tiltDeg) }}</td>
                <td class="r mono">
                  <StatusPill v-if="arm.axes[axis].atLimit" tone="warn">at limit</StatusPill>
                  <span v-else class="faint">&plusmn;{{ num(arm.axes[axis].limitDeg, 1) }}&#176;</span>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </PanelCard>
  </div>
</template>

<style scoped>
.tiles {
  display: grid;
  gap: var(--s2);
}

.tiles.top {
  grid-template-columns: repeat(6, 1fr);
}

.tiles.three {
  grid-template-columns: repeat(3, 1fr);
}

.tiles.four {
  grid-template-columns: repeat(4, 1fr);
}

.charts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--s4);
}

.mt {
  margin-top: var(--s3);
}

.small {
  font-size: var(--fs-xs);
}

.table-scroll {
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-sm);
}

th {
  text-align: left;
  font-size: var(--fs-xs);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-faint);
  font-weight: 600;
  padding: var(--s2) var(--s3);
  border-bottom: 1px solid var(--line);
  white-space: nowrap;
}

td {
  padding: var(--s2) var(--s3);
  border-bottom: 1px solid var(--line);
  white-space: nowrap;
}

tbody tr:last-child td {
  border-bottom: none;
}

.r {
  text-align: right;
}

.planned {
  color: var(--text-faint);
}

.swatch {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 2px;
  margin-right: var(--s2);
}

.counts {
  display: flex;
  flex-wrap: wrap;
  gap: var(--s2);
}

.count {
  display: flex;
  gap: var(--s2);
  align-items: baseline;
  padding: 2px var(--s2);
  border: 1px solid var(--line);
  border-radius: var(--r1);
  font-size: var(--fs-xs);
}

.pad {
  padding: var(--s3) var(--s4);
}

@media (max-width: 1150px) {
  .charts {
    grid-template-columns: 1fr;
  }

  .tiles.top {
    grid-template-columns: repeat(3, 1fr);
  }

  .tiles.four {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
