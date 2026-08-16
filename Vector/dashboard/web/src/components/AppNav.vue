<!--
  Sidebar navigation, built from the route table so pages cannot fall out of sync.

  The arm roster underneath is the modularity made visible: it lists whatever is in
  the config, marks which arms are wired up, and links straight to each one.
-->
<script setup lang="ts">
import { computed } from 'vue'

import { armColor } from '../lib/format'
import store, { arms, vehicleName } from '../lib/store'
import { navRoutes, type NavMeta } from '../router'
import NavIcon from './NavIcon.vue'

const groups = computed(() => {
  const order: NavMeta['group'][] = ['Fly', 'Hardware', 'Reference']
  return order
    .map((group) => ({
      group,
      items: navRoutes.filter((route) => (route.meta as NavMeta).group === group),
    }))
    .filter((entry) => entry.items.length)
})

const liveCount = computed(() => arms.value.filter((arm) => arm.live).length)
const configPath = computed(() => store.config?.path ?? '')
</script>

<template>
  <nav class="nav">
    <RouterLink to="/" class="brand">
      <span class="mark">V</span>
      <span class="grow">
        <span class="name">{{ vehicleName }}</span>
        <span class="sub">{{ liveCount }} of {{ arms.length }} arms live</span>
      </span>
    </RouterLink>

    <div v-for="entry in groups" :key="entry.group" class="group">
      <div class="label group-label">{{ entry.group }}</div>
      <RouterLink
        v-for="route in entry.items"
        :key="route.path"
        :to="route.path.replace('/:slug?', '')"
        class="item"
        :title="(route.meta as NavMeta).blurb"
      >
        <NavIcon :name="(route.meta as NavMeta).icon" />
        <span>{{ (route.meta as NavMeta).title }}</span>
      </RouterLink>
    </div>

    <div class="group">
      <div class="label group-label">Arms</div>
      <RouterLink
        v-for="(arm, index) in arms"
        :key="arm.id"
        :to="`/arms?arm=${arm.id}`"
        class="item arm"
      >
        <span class="swatch" :style="{ background: armColor(arm.id, index) }" />
        <span class="grow">{{ arm.label }}</span>
        <span v-if="!arm.live" class="status">{{ arm.status }}</span>
      </RouterLink>
    </div>

    <div class="foot">
      <div class="faint path" :title="configPath">{{ configPath || 'config not loaded' }}</div>
    </div>
  </nav>
</template>

<style scoped>
.nav {
  width: var(--nav-width);
  flex: none;
  border-right: 1px solid var(--line);
  background: var(--surface-1);
  display: flex;
  flex-direction: column;
  gap: var(--s5);
  padding: var(--s4) var(--s3);
  overflow-y: auto;
}

.brand {
  display: flex;
  align-items: center;
  gap: var(--s3);
  color: var(--text);
  text-decoration: none;
  padding: 0 var(--s2);
}

.brand:hover {
  text-decoration: none;
}

.mark {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  background: linear-gradient(140deg, var(--accent), var(--accent-dim));
  color: #fff;
  display: grid;
  place-items: center;
  font-weight: 700;
  font-size: var(--fs-lg);
  flex: none;
}

.name {
  display: block;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.sub {
  display: block;
  font-size: var(--fs-xs);
  color: var(--text-faint);
}

.group {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.group-label {
  padding: 0 var(--s2) var(--s1);
}

.item {
  display: flex;
  align-items: center;
  gap: var(--s3);
  padding: 7px var(--s2);
  border-radius: var(--radius-sm);
  color: var(--text-dim);
  text-decoration: none;
  font-size: var(--fs-md);
  border-left: 2px solid transparent;
}

.item:hover {
  background: var(--surface-2);
  color: var(--text);
  text-decoration: none;
}

.item.router-link-active {
  background: var(--accent-wash);
  color: var(--text);
  border-left-color: var(--accent);
}

.swatch {
  width: 8px;
  height: 8px;
  border-radius: 2px;
  flex: none;
  margin: 0 4px;
}

.status {
  font-size: var(--fs-xs);
  color: var(--text-faint);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.foot {
  margin-top: auto;
  padding: var(--s2);
  border-top: 1px solid var(--line);
}

.path {
  font-size: 10px;
  font-family: var(--mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  direction: rtl;
  text-align: left;
}

/*
  Narrow layouts put the nav above the content rather than beside it, so a
  fixed-width full-height column would push the page off the bottom of the screen.
  It becomes a wrapping horizontal bar: the group headings and the config path drop
  away, leaving just the destinations.
*/
@media (max-width: 820px) {
  .nav {
    width: auto;
    flex-wrap: wrap;
    flex-direction: row;
    align-items: center;
    gap: var(--s2) var(--s3);
    border-right: none;
    border-bottom: 1px solid var(--line);
    overflow-y: visible;
  }

  .brand {
    width: 100%;
  }

  .group {
    flex-direction: row;
    flex-wrap: wrap;
    gap: var(--s1);
  }

  .group-label,
  .foot {
    display: none;
  }

  .item {
    border-left: none;
    border-bottom: 2px solid transparent;
  }

  .item.router-link-active {
    border-left-color: transparent;
    border-bottom-color: var(--accent);
  }
}
</style>
