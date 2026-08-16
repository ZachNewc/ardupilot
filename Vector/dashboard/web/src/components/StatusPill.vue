<!-- Small state badge. Colour is the message, so the tone set is deliberately short. -->
<script setup lang="ts">
withDefaults(
  defineProps<{
    tone?: 'ok' | 'warn' | 'danger' | 'idle' | 'accent'
    pulse?: boolean
  }>(),
  { tone: 'idle', pulse: false },
)
</script>

<template>
  <span class="pill" :class="[tone, { pulse }]">
    <span class="led" />
    <slot />
  </span>
</template>

<style scoped>
.pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-xs);
  font-weight: 600;
  letter-spacing: 0.03em;
  text-transform: uppercase;
  padding: 3px var(--s2);
  border-radius: 999px;
  border: 1px solid var(--line-strong);
  background: var(--surface-2);
  color: var(--text-dim);
  white-space: nowrap;
}

.led {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  flex: none;
}

.ok {
  color: var(--ok);
  border-color: color-mix(in srgb, var(--ok) 45%, transparent);
  background: var(--ok-wash);
}

.warn {
  color: var(--warn);
  border-color: color-mix(in srgb, var(--warn) 45%, transparent);
  background: var(--warn-wash);
}

.danger {
  color: var(--danger);
  border-color: color-mix(in srgb, var(--danger) 45%, transparent);
  background: var(--danger-wash);
}

.accent {
  color: var(--accent);
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
  background: var(--accent-wash);
}

.pulse .led {
  animation: blink 1.4s ease-in-out infinite;
}

@keyframes blink {
  50% {
    opacity: 0.25;
  }
}

@media (prefers-reduced-motion: reduce) {
  .pulse .led {
    animation: none;
  }
}
</style>
