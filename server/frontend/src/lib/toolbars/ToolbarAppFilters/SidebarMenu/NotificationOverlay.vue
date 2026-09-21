<script setup>
import { computed } from 'vue'

import OverlayBadge from 'primevue/overlaybadge'

import { useApp } from '@/stores'

const app = useApp()

// Errors and warnings to read: the live ones since the pane was last opened,
// and the kept ones not yet marked read. A kept notification stays counted
// until it is marked read, whether or not the pane has been opened.
const errors = computed(() => app.ui.notification.recentErrors + app.ui.inbox.unreadErrors)
const warnings = computed(
  () => app.ui.notification.recentWarnings + app.ui.inbox.unread.length - app.ui.inbox.unreadErrors
)

/**
 * The badge shows the errors when there are any, else the warnings, and is
 * hidden when there are neither.
 *
 *  @returns {String} The badge value as a string.
 */
const badgeValue = computed(() =>
  errors.value > 0 ? String(errors.value) : warnings.value > 0 ? String(warnings.value) : ''
)

/**
 * 'danger' when anything to read is an error, else 'warn'.
 *
 * @returns {String} The badge severity ('danger' or 'warn').
 */
const badgeSeverity = computed(() => (errors.value > 0 ? 'danger' : 'warn'))

/**
 * Hidden when there is nothing to read.
 *
 * @returns {Boolean} True if the badge should be hidden, otherwise false.
 */
const hiddenBadge = computed(() => errors.value === 0 && warnings.value === 0)
</script>

<template>
  <slot v-if="hiddenBadge"></slot>
  <OverlayBadge v-else :value="badgeValue" :severity="badgeSeverity" size="small">
    <slot></slot>
  </OverlayBadge>
</template>
