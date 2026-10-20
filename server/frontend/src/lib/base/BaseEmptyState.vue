<script setup>
// Shown in place of a list with nothing in it, so an empty pane says what it
// is for and how it fills instead of reading as broken. Muted like
// BaseLoadError, which it sits beside; the way forward (a create button, a
// link) goes in the actions slot.
defineProps({
  // Icon classes, as the panel headers take them ("pi ph ph-folders").
  icon: {
    type: String,
    required: false,
    default: null
  },
  title: {
    type: String,
    required: true
  },
  // One line on what fills the list.
  hint: {
    type: String,
    required: false,
    default: null
  }
})
</script>

<template>
  <div class="empty-state">
    <div>
      <span v-if="icon" :class="['empty-icon', icon]" />
      <strong class="title">{{ title }}</strong>
      <span v-if="hint" class="hint">{{ hint }}</span>
      <div v-if="$slots.actions" class="actions">
        <slot name="actions" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.empty-state {
  display: grid;
  place-items: center;
  padding: 1.5rem 1rem;
  height: 100%;
  color: var(--p-text-muted-color);
}

.empty-state > div {
  display: grid;
  place-items: center;
  gap: 0.4rem;
  max-width: 40ch;
  text-align: center;
}

.empty-icon {
  font-size: 1.4rem;
  opacity: 0.6;
}

.hint {
  font-size: 0.9rem;
  line-height: 1.4;
}

.actions {
  display: flex;
  gap: 0.4rem;
}
</style>
