<template>
  <section
    class="agent-mode-picker"
    :class="{ compact }"
    aria-label="Support approach"
  >
    <h3 v-if="!compact">Support approach</h3>
    <div class="theme-options" role="group" aria-label="Agent mode">
      <button
        type="button"
        :aria-pressed="modelValue === 'single'"
        :class="{ selected: modelValue === 'single' }"
        :disabled="disabled"
        @click="$emit('update:modelValue', 'single')"
      >
        Single agent · Default
      </button>
      <button
        type="button"
        :aria-pressed="modelValue === 'multi'"
        :class="{ selected: modelValue === 'multi' }"
        :disabled="disabled"
        @click="$emit('update:modelValue', 'multi')"
      >
        Multi-agent · Optional
      </button>
    </div>
    <p v-if="!compact" class="field-hint">
      Both keep your full case context. Applies to your next analysis. Offline
      comparisons found no task-completion benefit from multiple agents.
    </p>
  </section>
</template>
<script setup>
defineProps({
  modelValue: { type: String, default: "single" },
  disabled: Boolean,
  compact: Boolean,
});
defineEmits(["update:modelValue"]);
</script>
<style scoped>
.agent-mode-picker {
  margin: 20px 0;
}
.compact {
  margin: 0 0 10px;
}
h3 {
  margin: 0 0 12px;
  font-size: 16px;
}
.theme-options {
  gap: 8px;
}
.theme-options button {
  min-height: 48px;
  font-size: 13px;
  padding: 12px 8px;
}
</style>
