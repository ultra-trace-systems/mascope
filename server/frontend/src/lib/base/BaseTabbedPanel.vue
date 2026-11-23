<script setup>
import { computed, ref, watch, onUnmounted } from 'vue'
import Panel from 'primevue/panel'
import Button from 'primevue/button'
import ProgressSpinner from 'primevue/progressspinner'

import { BaseBreadcrumb, BaseLoadError, BaseStatusIcon } from '@/lib/base'

const props = defineProps({
  label: {
    type: String,
    required: false
  },
  icon: {
    type: String,
    required: false
  },
  clear: {
    type: Function,
    required: false
  },
  backLabel: {
    type: String,
    required: false,
    default: 'Back'
  },
  loading: {
    type: Boolean,
    required: false,
    default: false
  },
  // Last load failure, if any. When set the panel explains itself instead of
  // rendering an empty body the user cannot account for.
  error: {
    type: [Error, Object, String],
    required: false,
    default: null
  },
  // Optional retry action offered alongside the error message
  onRetry: {
    type: Function,
    required: false,
    default: null
  },
  contextMenu: {
    type: Object,
    required: false
  },
  pt: {
    type: Object,
    required: false
  },
  status: {
    type: Object,
    required: false
  },
  // Breadcrumb configuration: { items: [...] }
  // Each item: { icon, label?, action?, tooltip?, contextMenu? }
  breadcrumb: {
    type: Object,
    required: false,
    default: null
  }
})

// Debounced loading state
const showSpinner = ref(false)
let loadingTimeout = null

// Watch for loading prop changes and debounce the spinner
watch(
  () => props.loading,
  (newLoading) => {
    if (loadingTimeout) {
      clearTimeout(loadingTimeout)
      loadingTimeout = null
    }

    if (newLoading) {
      // Show spinner after 300ms of loading
      loadingTimeout = setTimeout(() => {
        showSpinner.value = true
      }, 300)
    } else {
      // Hide spinner immediately when loading stops
      showSpinner.value = false
    }
  },
  { immediate: true }
)

// Whether the slot has been shown since the panel mounted or since the last
// error took its place. Until it has, the spinner stands where the content
// will be (the first load). After that a reload keeps the slot mounted and
// draws the spinner over it: unmounting it would drop the focused row (focus
// falls to <body>, so a key press reaches no table) and the scroll position.
const contentShown = ref(false)
watch(
  () => [props.loading, props.error],
  ([loading, error]) => {
    if (error) contentShown.value = false
    else if (!loading) contentShown.value = true
  },
  { immediate: true }
)

const overlay = computed(() => showSpinner.value && contentShown.value)

// The panel's body says it is busy while the spinner shows, merged into the
// caller's pass-through options (the help cards put theirs on the root).
const panelPt = computed(() => ({
  ...props.pt,
  content: {
    ...props.pt?.content,
    'aria-busy': showSpinner.value ? 'true' : undefined
  }
}))

// Cleanup timeout on unmount
onUnmounted(() => {
  if (loadingTimeout) {
    clearTimeout(loadingTimeout)
  }
})
</script>

<template>
  <Panel
    class="browser"
    style="border: none"
    @contextmenu.prevent.stop="
      (event) => {
        contextMenu?.onClick(event)
      }
    "
    :pt="panelPt"
  >
    <template #header>
      <!-- Breadcrumb navigation (when provided) -->
      <BaseBreadcrumb v-if="breadcrumb" :items="breadcrumb.items" />

      <!-- Traditional label (when no breadcrumb) -->
      <div v-else-if="label" class="label">
        <Button
          v-if="clear"
          v-tooltip.right="backLabel"
          icon="pi ph ph-caret-left"
          @click="clear()"
          text
          severity="secondary"
          size="small"
          class="back-button"
        />
        <span v-if="icon" :class="icon" />
        {{ label }}
        <BaseStatusIcon
          v-if="status"
          :status="status.status"
          :config="status.config"
          :onAction="status.onRematch"
        />
      </div>
    </template>
    <template #icons>
      <slot name="menu"></slot>
    </template>
    <div v-if="showSpinner && !overlay" class="spinner">
      <div>
        <ProgressSpinner strokeWidth="5px" />
        <span>loading...</span>
      </div>
    </div>
    <BaseLoadError v-else-if="error" :error="error" :onRetry="onRetry" />
    <template v-else>
      <slot></slot>
      <div v-if="overlay" class="spinner overlay">
        <div>
          <ProgressSpinner strokeWidth="5px" />
          <span>loading...</span>
        </div>
      </div>
    </template>
  </Panel>
</template>

<style scoped>
.label {
  display: flex;
  flex-flow: row nowrap;
  align-items: center;
  gap: 0.5rem;
  color: var(--p-primary-color);
  font-weight: bold;
  margin: 0.6rem;
  height: 25.15px;
}

/* Drawn over the content during a reload: it covers the whole body, so a
   click lands on it and not on a row, while the content stays mounted under
   it. Opaque, so a reload reads as the first load does and an empty list's
   text never shows through. Above the tables' row expansions (z-index 100 in
   style.css). */
.spinner.overlay {
  position: absolute;
  inset: 0;
  z-index: 101;
  background: var(--p-panel-background, var(--p-content-background));
}

.status-button {
  opacity: 0.8;
  margin-left: -0.2rem;
}

.back-button {
  opacity: 0.4;
}

.status-button:hover,
.back-button:hover {
  opacity: 1;
}
</style>
