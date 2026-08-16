<!--
  Overview: the one screen that answers "is the vehicle healthy and what is it doing".

  Layout is deliberate. Attitude and frame sit side by side at the top because the
  whole point of this airframe is the relationship between the two: body tilt on the
  left, what the gimbals did about it on the right. The arm roster below is a table
  because comparing four arms column by column is how a wiring fault gets spotted.
-->
<script setup lang="ts">
import { computed } from 'vue'

import AttitudeDisc from '../components/AttitudeDisc.vue'
import EventFeed from '../components/EventFeed.vue'
import FrameDiagram from '../components/FrameDiagram.vue'
import PanelCard from '../components/PanelCard.vue'
import StatTile from '../components/StatTile.vue'
import StatusPill from '../components/StatusPill.vue'
import { age, armColor, num, signedDeg, us } from '../lib/format'
import store, { arms, linkUp } from '../lib/store'

const vehicle = computed(() => store.state?.vehicle ?? null)
const controller = computed(() => store.state?.controller ?? null)
const frame = computed(() => store.config?.frame ?? null)

const tilt = computed(() =>
  vehicle.value ? Math.hypot(vehicle.value.rollDeg, vehicle.value.pitchDeg) : 0,
)

/** Tightest correction envelope across live arms; the honest authority number. */
const envelope = computed(() => {
  const workspaces = store.config?.workspaces ?? {}
  const live = arms.value.filter((arm) => arm.live).map((arm) => workspaces[arm.id]?.uniformTiltDeg)
  const values = live.filter((value): value is number => typeof value === 'number')
  return values.length ? Math.min(...values) : null
})

const batteryTone = computed(() => {
  const volts = vehicle.value?.voltage ?? 0
  if (!volts) return 'dim'
  // 6s li-ion: 3.2 V/cell is the floor worth flying to, 3.4 is getting low.
  if (volts < 19.2) return 'danger'
  if (volts < 20.4) return 'warn'
  return 'ok'
})

const escs = computed(() => store.state?.escs ?? [])
</script>

<template>
  <div class="stack">
    <!-- top row: attitude vs. what the arms are doing about it -->
    <div class="top">
      <PanelCard title="Attitude" note="How far the body is off vertical, and which way.">
        <template #actions>
          <StatusPill :tone="tilt < 3 ? 'ok' : tilt < 12 ? 'warn' : 'danger'">
            {{ num(tilt, 1) }}&#176;
          </StatusPill>
        </template>
        <AttitudeDisc
          :roll-deg="vehicle?.rollDeg ?? 0"
          :pitch-deg="vehicle?.pitchDeg ?? 0"
          :lean="controller?.active ? controller.target : controller?.preview ?? null"
          :envelope-deg="envelope"
        />
        <div class="legend faint">
          <span><span class="key body" /> body tilt</span>
          <span><span class="key lean" /> gimbal lean {{ controller?.active ? '(commanded)' : '(preview)' }}</span>
          <span><span class="key env" /> correction envelope</span>
        </div>
      </PanelCard>

      <PanelCard
        title="Airframe"
        :note="
          frame
            ? `${frame.layout.toUpperCase()} layout, nose along the ${frame.noseArm} arm, ${frame.rotorDiagonalM} m rotor diagonal.`
            : undefined
        "
      >
        <FrameDiagram :arms="arms" />
        <p class="faint hint">
          Arrows are each pair's horizontal thrust component. Dashed booms are arms in the
          config that are not wired up yet.
        </p>
      </PanelCard>

      <div class="stack">
        <PanelCard title="Vehicle">
          <div class="tiles two">
            <StatTile
              label="Pack"
              :value="num(vehicle?.voltage, 2)"
              unit="V"
              :tone="batteryTone"
              hint="6s2p li-ion: 25.2 V full, 19.2 V empty"
            />
            <StatTile label="Current" :value="num(vehicle?.current, 1)" unit="A" />
            <StatTile label="Mode" :value="vehicle?.mode || '--'" />
            <StatTile
              label="Armed"
              :value="vehicle?.armed ? 'YES' : 'no'"
              :tone="vehicle?.armed ? 'danger' : 'ok'"
            />
            <StatTile label="FC load" :value="num(vehicle?.loadPercent, 0)" unit="%" />
            <StatTile
              label="Telemetry"
              :value="age(vehicle?.ageS)"
              :tone="(vehicle?.ageS ?? 99) > 3 ? 'warn' : 'dim'"
            />
          </div>
        </PanelCard>

        <PanelCard
          title="Rates"
          note="Body rates, the input the lead term extrapolates from."
        >
          <div class="tiles three">
            <StatTile label="Roll" :value="num(vehicle?.rollRateDegS, 0)" unit="&#176;/s" />
            <StatTile label="Pitch" :value="num(vehicle?.pitchRateDegS, 0)" unit="&#176;/s" />
            <StatTile label="Yaw" :value="num(vehicle?.yawRateDegS, 0)" unit="&#176;/s" />
          </div>
        </PanelCard>
      </div>
    </div>

    <!-- arm roster -->
    <PanelCard
      title="Arms"
      :note="`${arms.filter((a) => a.live).length} live, ${arms.length} configured. Adding an arm means one entry in vector.json.`"
      flush
    >
      <template #actions>
        <RouterLink to="/arms"><button class="ghost tiny">Open Arms</button></RouterLink>
      </template>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Arm</th>
              <th>State</th>
              <th class="r">Azimuth</th>
              <th class="r">Thrust fwd</th>
              <th class="r">Thrust right</th>
              <th class="r">Outer</th>
              <th class="r">Inner</th>
              <th class="r">Motors</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(arm, index) in arms" :key="arm.id" :class="{ planned: !arm.live }">
              <td>
                <span class="swatch" :style="{ background: armColor(arm.id, index) }" />
                {{ arm.label }}
              </td>
              <td>
                <StatusPill :tone="arm.live ? 'ok' : 'idle'">{{ arm.status }}</StatusPill>
              </td>
              <td class="r mono">{{ num(arm.azimuthDeg, 0) }}&#176;</td>
              <td class="r mono">{{ signedDeg(arm.thrust.forwardDeg) }}</td>
              <td class="r mono">{{ signedDeg(arm.thrust.rightDeg) }}</td>
              <td class="r mono">
                <span :class="{ warn: arm.axes.outer.atLimit }">
                  {{ us(arm.axes.outer.pwm) }}
                </span>
                <span class="faint sub">ch{{ arm.axes.outer.channel }}</span>
              </td>
              <td class="r mono">
                <span :class="{ warn: arm.axes.inner.atLimit }">
                  {{ us(arm.axes.inner.pwm) }}
                </span>
                <span class="faint sub">ch{{ arm.axes.inner.channel }}</span>
              </td>
              <td class="r mono faint">
                {{ Object.values(arm.motors).map((m) => `ch${m?.channel}`).join(' / ') || '--' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </PanelCard>

    <div class="bottom">
      <PanelCard
        title="ESC telemetry"
        :note="
          escs.length
            ? `${escs.length} ESCs reporting.`
            : 'No ESC telemetry yet. Check the telemetry wire and SERIAL_PROTOCOL = 16.'
        "
        flush
      >
        <div v-if="escs.length" class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>ESC</th>
                <th class="r">RPM</th>
                <th class="r">Temp</th>
                <th class="r">Volts</th>
                <th class="r">Amps</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="esc in escs" :key="esc.index">
                <td>{{ esc.label }}</td>
                <td class="r mono">{{ num(esc.rpm, 0) }}</td>
                <td class="r mono">{{ num(esc.temperatureC, 0) }}&#176;C</td>
                <td class="r mono">{{ num(esc.voltage, 2) }}</td>
                <td class="r mono">{{ num(esc.current, 1) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="pad faint">
          Nothing received. ESC telemetry is optional; everything else works without it.
        </p>
      </PanelCard>

      <PanelCard title="Activity" note="Commands sent and messages received, newest first." flush>
        <EventFeed :limit="40" />
      </PanelCard>
    </div>

    <p v-if="!linkUp" class="faint">
      Not connected. The layout above shows the configured vehicle so the channel map can
      be reviewed offline.
    </p>
  </div>
</template>

<style scoped>
.top {
  display: grid;
  grid-template-columns: minmax(240px, 1fr) minmax(240px, 1fr) minmax(280px, 1.15fr);
  gap: var(--s4);
  align-items: start;
}

.bottom {
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

.tiles.three {
  grid-template-columns: repeat(3, 1fr);
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

.hint {
  margin: var(--s2) 0 0;
  font-size: var(--fs-xs);
  line-height: 1.5;
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

tbody tr:hover {
  background: var(--surface-2);
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

.sub {
  font-size: 10px;
  margin-left: var(--s2);
}

.pad {
  padding: var(--s3) var(--s4);
  margin: 0;
}

@media (max-width: 1200px) {
  .top,
  .bottom {
    grid-template-columns: 1fr;
  }
}
</style>
