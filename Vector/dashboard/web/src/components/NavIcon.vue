<!-- Inline icon set. Small enough to keep here rather than pull in an icon package. -->
<script setup lang="ts">
defineProps<{ name: string }>()

const PATHS: Record<string, string> = {
  grid: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  arms: 'M12 12v0M12 12L5 5M12 12l7-7M12 12l-7 7M12 12l7 7',
  level: 'M3 12h18M7 12a5 5 0 0 1 10 0M12 3v3',
  accel: 'M9 12a3 3 0 1 0 6 0a3 3 0 1 0 -6 0M12 3v3M12 18v3M3 12h3M18 12h3',
  wave: 'M2 12h3l2-6 3 12 3-9 2 5h7',
  cube: 'M12 2 3 7v10l9 5 9-5V7zM3 7l9 5 9-5M12 12v10',
  sliders: 'M4 6h16M4 12h16M4 18h16M9 6v0M15 12v0M7 18v0',
  book: 'M4 4h7a2 2 0 0 1 2 2v14a2 2 0 0 0-2-2H4zM20 4h-7a2 2 0 0 0-2 2v14a2 2 0 0 1 2-2h7z',
}

const DOTS: Record<string, [number, number][]> = {
  arms: [[12, 12]],
  sliders: [
    [9, 6],
    [15, 12],
    [7, 18],
  ],
}
</script>

<template>
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
    <path :d="PATHS[name] ?? PATHS.grid" stroke-linecap="round" stroke-linejoin="round" />
    <circle
      v-for="([cx, cy], i) in DOTS[name] ?? []"
      :key="i"
      :cx="cx"
      :cy="cy"
      r="2.2"
      fill="currentColor"
      stroke="none"
    />
  </svg>
</template>

<style scoped>
svg {
  width: 16px;
  height: 16px;
  flex: none;
}
</style>
