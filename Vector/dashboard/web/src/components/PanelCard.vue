<!--
  The one container every page uses. Optional header slot for controls on the right,
  optional note under the title for the "why" of a panel.
-->
<script setup lang="ts">
defineProps<{
  title?: string
  note?: string
  /** Removes the body padding, for panels that hold a canvas or a table. */
  flush?: boolean
  accent?: string
}>()
</script>

<template>
  <section class="panel" :style="accent ? { '--panel-accent': accent } : undefined">
    <header v-if="title || $slots.actions" class="panel-head">
      <div class="grow">
        <h2 v-if="title" class="panel-title">
          <span v-if="accent" class="dot" />
          {{ title }}
        </h2>
        <p v-if="note" class="panel-note">{{ note }}</p>
      </div>
      <div v-if="$slots.actions" class="row">
        <slot name="actions" />
      </div>
    </header>
    <div :class="['panel-body', { flush }]">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.panel {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.panel-head {
  display: flex;
  align-items: flex-start;
  gap: var(--s3);
  padding: var(--s3) var(--s4);
  border-bottom: 1px solid var(--line);
}

.panel-title {
  font-size: var(--fs-md);
  letter-spacing: 0.01em;
  display: flex;
  align-items: center;
  gap: var(--s2);
}

.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--panel-accent, var(--accent));
  flex: none;
}

.panel-note {
  margin: 2px 0 0;
  font-size: var(--fs-sm);
  color: var(--text-faint);
  max-width: 62ch;
}

.panel-body {
  padding: var(--s4);
  flex: 1;
  min-width: 0;
}

.panel-body.flush {
  padding: 0;
}
</style>
