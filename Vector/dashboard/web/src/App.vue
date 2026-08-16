<!--
  App shell: sidebar, top bar, and the routed page.

  Every page gets its title and one-line description from the route table, so a page
  component only ever renders its own content.
-->
<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import AppNav from './components/AppNav.vue'
import LinkBar from './components/LinkBar.vue'
import store, { commandBlockedReason } from './lib/store'
import type { NavMeta } from './router'

const route = useRoute()
const meta = computed(() => route.meta as Partial<NavMeta>)
const ready = computed(() => store.config !== null)
</script>

<template>
  <div class="shell">
    <AppNav />

    <div class="main">
      <LinkBar />

      <main class="content">
        <div class="inner">
          <div v-if="!store.socketOpen" class="notice danger-notice">
            <strong>No connection to the dashboard server.</strong>
            Start it with <code>Vector/start.sh</code>. This page retries on its own.
          </div>
          <div v-else-if="commandBlockedReason" class="notice">
            {{ commandBlockedReason }} &mdash; readouts are live, controls are disabled.
          </div>

          <header class="page-head">
            <h1>{{ meta.title ?? 'Vector' }}</h1>
            <p v-if="meta.blurb" class="blurb">{{ meta.blurb }}</p>
          </header>

          <RouterView v-if="ready" v-slot="{ Component }">
            <component :is="Component" />
          </RouterView>
          <p v-else class="faint">Loading vehicle configuration&hellip;</p>
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  height: 100%;
  overflow: hidden;
}

.main {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
  /* Without this a flex item refuses to shrink below its content, and .content's
     overflow never engages. */
  min-height: 0;
}

.content {
  flex: 1;
  overflow-y: auto;
  padding: var(--s5) var(--s5) var(--s7);
}

.inner {
  max-width: var(--content-max);
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--s4);
}

.page-head h1 {
  font-size: var(--fs-2xl);
  letter-spacing: -0.01em;
}

.blurb {
  margin: var(--s1) 0 0;
  color: var(--text-dim);
  font-size: var(--fs-md);
}

.notice {
  padding: var(--s2) var(--s3);
  border-radius: var(--radius-sm);
  border: 1px solid var(--line-strong);
  background: var(--surface-2);
  color: var(--text-dim);
  font-size: var(--fs-sm);
}

.danger-notice {
  border-color: color-mix(in srgb, var(--danger) 45%, transparent);
  background: var(--danger-wash);
  color: var(--text);
}

/*
  Narrow layouts stack the nav above the content, which means the shell can no longer
  be pinned to the viewport: a 100%-height shell with `overflow: hidden` would put the
  nav in the visible area and clip the entire page below it, with nothing left to
  scroll. So the document becomes the scroll container instead.
*/
@media (max-width: 820px) {
  .shell {
    flex-direction: column;
    height: auto;
    min-height: 100%;
    overflow: visible;
  }

  .content {
    overflow-y: visible;
    padding: var(--s4) var(--s3) var(--s6);
  }
}
</style>
