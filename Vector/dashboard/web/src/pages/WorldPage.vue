<!--
  3D World: scene scaffold, not yet wired to telemetry.

  The vehicle is built from vector.json, so arm count, azimuths, arm length and the
  live/planned split are already correct and will stay correct when arms are added.
  What is deliberately absent is any connection to live state: `setAttitude` and
  `setArmTilt` exist and are unused. Wiring them is the next step, and doing it
  before the firmware mixer exists would mean animating a simulation of a controller
  that has not been written.
-->
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'

import PanelCard from '../components/PanelCard.vue'
import StatTile from '../components/StatTile.vue'
import StatusPill from '../components/StatusPill.vue'
import { num } from '../lib/format'
import store, { arms } from '../lib/store'
import { createScene, type VectorScene } from '../lib/scene'

const canvas = ref<HTMLCanvasElement | null>(null)
const holder = ref<HTMLDivElement | null>(null)
const world = shallowRef<VectorScene | null>(null)
const failure = ref<string | null>(null)

let frameHandle = 0
let observer: ResizeObserver | null = null

function loop() {
  world.value?.render()
  frameHandle = requestAnimationFrame(loop)
}

onMounted(() => {
  const element = canvas.value
  const frame = store.config?.frame
  const armConfigs = store.config?.arms
  if (!element || !frame || !armConfigs) return

  try {
    world.value = createScene(element, frame, armConfigs)
  } catch (error) {
    // A machine without WebGL should get an explanation, not a blank rectangle.
    failure.value = error instanceof Error ? error.message : String(error)
    return
  }

  const fit = () => {
    const box = holder.value?.getBoundingClientRect()
    if (box) world.value?.resize(box.width, box.height)
  }
  fit()

  observer = new ResizeObserver(fit)
  if (holder.value) observer.observe(holder.value)

  loop()
})

onBeforeUnmount(() => {
  cancelAnimationFrame(frameHandle)
  observer?.disconnect()
  world.value?.dispose()
  world.value = null
})
</script>

<template>
  <div class="stack">
    <div class="layout">
      <PanelCard title="Scene" note="Orbit with the left button, zoom with the wheel." flush>
        <template #actions>
          <StatusPill tone="idle">static model</StatusPill>
        </template>
        <div ref="holder" class="viewport">
          <canvas ref="canvas" />
          <div v-if="failure" class="fallback">
            <p><strong>WebGL is unavailable.</strong></p>
            <p class="faint mono small">{{ failure }}</p>
            <p class="faint small">
              Every other page works without it; this is the only one that needs a GPU context.
            </p>
          </div>
        </div>
      </PanelCard>

      <div class="stack">
        <PanelCard title="Built from config" note="Nothing in the model is hardcoded.">
          <div class="tiles two">
            <StatTile label="Arms" :value="arms.length" />
            <StatTile label="Live" :value="arms.filter((a) => a.live).length" />
            <StatTile
              label="Rotor diagonal"
              :value="num(store.config?.frame.rotorDiagonalM, 2)"
              unit="m"
            />
            <StatTile label="Arm length" :value="num(store.config?.frame.armLengthM, 3)" unit="m" />
            <StatTile label="Layout" :value="store.config?.frame.layout ?? '--'" />
            <StatTile label="Nose arm" :value="store.config?.frame.noseArm ?? '--'" />
          </div>
          <p class="faint small mt">
            Add an arm to <code>vector.json</code> and it appears here with the right azimuth,
            drawn dashed until its status is set to live.
          </p>
        </PanelCard>

        <PanelCard title="What is not here yet" note="Stated plainly so the page is not mistaken for a simulator.">
          <ul class="todo">
            <li>
              <strong>Live attitude.</strong> <code>setAttitude()</code> exists and is unused.
              Wiring it is a one-line change once the display is worth trusting.
            </li>
            <li>
              <strong>Live gimbal angles.</strong> Same for <code>setArmTilt()</code>, which
              takes the same gimbal degrees the rest of the dashboard uses.
            </li>
            <li>
              <strong>Physics.</strong> There is no dynamics model here. Flight behaviour
              belongs in SITL, where ArduPilot's own solver runs, not in a browser.
            </li>
            <li>
              <strong>Rotor animation.</strong> The discs are placed and ready; spinning them
              needs ESC RPM, which is optional telemetry.
            </li>
          </ul>
          <p class="faint small">
            The useful next step is SITL: a simulated vehicle running real firmware, with this
            view as its display. See <RouterLink to="/docs/08-roadmap">Roadmap</RouterLink>.
          </p>
        </PanelCard>
      </div>
    </div>
  </div>
</template>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: 1.6fr 1fr;
  gap: var(--s4);
  align-items: start;
}

.viewport {
  position: relative;
  width: 100%;
  height: min(64vh, 620px);
  min-height: 340px;
  border-radius: 0 0 var(--radius) var(--radius);
  overflow: hidden;
}

canvas {
  display: block;
  width: 100%;
  height: 100%;
}

.fallback {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--s2);
  background: var(--surface-1);
  padding: var(--s5);
  text-align: center;
}

.fallback p {
  margin: 0;
  max-width: 40ch;
}

.tiles {
  display: grid;
  gap: var(--s2);
}

.tiles.two {
  grid-template-columns: 1fr 1fr;
}

.todo {
  margin: 0 0 var(--s3);
  padding-left: var(--s5);
  color: var(--text-dim);
  font-size: var(--fs-sm);
  display: flex;
  flex-direction: column;
  gap: var(--s2);
}

.todo strong {
  color: var(--text);
}

.small {
  font-size: var(--fs-xs);
  line-height: 1.5;
}

.mt {
  margin-top: var(--s3);
}

@media (max-width: 1150px) {
  .layout {
    grid-template-columns: 1fr;
  }
}
</style>
