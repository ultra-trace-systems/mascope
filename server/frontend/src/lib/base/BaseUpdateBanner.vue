<script setup>
import { computed, ref } from 'vue'

import Button from 'primevue/button'

import { isBuildVersion, releaseNotesUrl } from '@/lib/about'
import { useUpdate } from '@/lib/update'

// A non-blocking notice that a newer build is available. Reloading is cheap
// because the UI restores its location on load, so the user can reload at a
// moment that suits them rather than being interrupted. It names the build when
// the new index.html does, and links what changed, so "now or later" is an
// informed choice.
const update = useUpdate()
const dismissed = ref(false)
const named = computed(() => isBuildVersion(update.version))
</script>

<template>
  <div v-if="update.available && !dismissed" class="update-banner" role="status">
    <span class="ph ph-arrow-clockwise" />
    <span class="text">{{
      named ? `Mascope ${update.version} is available.` : 'A new version of Mascope is available.'
    }}</span>
    <a
      class="notes"
      :href="releaseNotesUrl(update.version)"
      target="_blank"
      rel="noopener noreferrer"
      >What's new</a
    >
    <Button size="small" label="Reload" @click="update.reload()" />
    <Button
      size="small"
      text
      rounded
      aria-label="Dismiss"
      icon="ph ph-x"
      @click="dismissed = true"
    />
  </div>
</template>

<style scoped>
.update-banner {
  position: fixed;
  top: 1rem;
  left: 50%;
  transform: translateX(-50%);
  z-index: 9998;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.5rem 0.5rem 0.5rem 1rem;
  border-radius: 999px;
  background: var(--p-primary-color, #10b981);
  color: var(--p-primary-contrast-color, #fff);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
}
.update-banner .text,
.update-banner .notes {
  font-size: small;
}
.update-banner .notes {
  color: inherit;
}
</style>
