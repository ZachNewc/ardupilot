<!-- Labelled checkbox with an optional explanation underneath. -->
<script setup lang="ts">
defineProps<{
  label: string
  modelValue: boolean
  note?: string
  disabled?: boolean
}>()

const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
</script>

<template>
  <label class="toggle-row" :class="{ disabled }">
    <input
      type="checkbox"
      :checked="modelValue"
      :disabled="disabled"
      @change="emit('update:modelValue', ($event.target as HTMLInputElement).checked)"
    />
    <span class="grow">
      <span class="name">{{ label }}</span>
      <span v-if="note" class="note">{{ note }}</span>
    </span>
  </label>
</template>

<style scoped>
.toggle-row {
  display: flex;
  gap: var(--s3);
  align-items: flex-start;
  cursor: pointer;
}

.toggle-row.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

input {
  margin-top: 2px;
  flex: none;
}

.name {
  display: block;
  font-size: var(--fs-sm);
  color: var(--text);
}

.note {
  display: block;
  font-size: var(--fs-xs);
  color: var(--text-faint);
  line-height: 1.45;
}
</style>
