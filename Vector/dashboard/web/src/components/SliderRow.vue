<!--
  Labelled slider with a live readout and an optional explanation.

  Every tunable in the dashboard uses this, so a value always appears next to its
  slider and a gain never sits on screen without saying what it does.
-->
<script setup lang="ts">
withDefaults(
  defineProps<{
    label: string
    modelValue: number
    min: number
    max: number
    step?: number
    unit?: string
    digits?: number
    note?: string
    disabled?: boolean
  }>(),
  { step: 0.01, digits: 2, unit: '', disabled: false },
)

const emit = defineEmits<{ 'update:modelValue': [value: number] }>()

const onInput = (event: Event) => {
  emit('update:modelValue', Number((event.target as HTMLInputElement).value))
}
</script>

<template>
  <label class="slider-row" :class="{ disabled }">
    <div class="row spread">
      <span class="name">{{ label }}</span>
      <span class="readout mono">{{ modelValue.toFixed(digits) }}{{ unit }}</span>
    </div>
    <input
      type="range"
      :min="min"
      :max="max"
      :step="step"
      :value="modelValue"
      :disabled="disabled"
      @input="onInput"
    />
    <p v-if="note" class="note">{{ note }}</p>
  </label>
</template>

<style scoped>
.slider-row {
  display: block;
}

.slider-row.disabled {
  opacity: 0.5;
}

.name {
  font-size: var(--fs-sm);
  color: var(--text-dim);
}

.readout {
  font-size: var(--fs-sm);
  color: var(--text);
}

.note {
  margin: 0;
  font-size: var(--fs-xs);
  color: var(--text-faint);
  line-height: 1.45;
}
</style>
