<!-- One labelled number. The unit sits outside the value so columns line up. -->
<script setup lang="ts">
defineProps<{
  label: string
  value: string | number
  unit?: string
  tone?: 'ok' | 'warn' | 'danger' | 'dim'
  hint?: string
  wide?: boolean
}>()
</script>

<template>
  <div class="tile" :class="{ wide }" :title="hint">
    <div class="label">{{ label }}</div>
    <div class="value mono" :class="tone">
      {{ value }}<span v-if="unit" class="unit">{{ unit }}</span>
    </div>
  </div>
</template>

<style scoped>
.tile {
  background: var(--surface-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: var(--s2) var(--s3);
  min-width: 0;
}

.value {
  font-size: var(--fs-lg);
  line-height: 1.35;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.wide .value {
  font-size: var(--fs-xl);
}

.unit {
  font-size: var(--fs-sm);
  color: var(--text-faint);
  margin-left: 3px;
}
</style>
