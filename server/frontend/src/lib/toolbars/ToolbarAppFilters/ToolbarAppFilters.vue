<script setup>
import { ref } from 'vue'

import Toolbar from 'primevue/toolbar'

import { useApp } from '@/stores'
import { BaseBrandLogo } from '@/lib/base'

import AppFilterChips from './AppFilterChips.vue'
import LocationShareButton from './LocationShareButton.vue'

import { SidebarMenu } from './SidebarMenu'

const app = useApp()

const filtering = ref(false)
</script>

<template>
  <Toolbar
    class="filters"
    :pt="
      app.ui.help.bottom(
        `
        <h1>Topbar</h1>
        <p>
          On the left, there is dataset navigation and home menu with settings
          and notification log.
        </p>
        <p>
          On the right, there are filter chips reflecting the current data selection.
        </p>
      `,
        { doc: app.ui.help.docUrl('getting-started/first-steps/#the-dashboard-at-a-glance') }
      )
    "
  >
    <template #start>
      <div class="row">
        <SidebarMenu />
        <LocationShareButton />
      </div>
    </template>
    <template #center>
      <BaseBrandLogo v-show="!app.data.batch.focused" />
    </template>
    <template #end>
      <div class="row">
        <AppFilterChips v-model:filtering="filtering" v-show="filtering" />
      </div>
    </template>
  </Toolbar>
</template>

<style scoped>
.filters :deep(*) {
  font-size: small;
}

.filters :deep(.p-toolbar) {
  padding: 0.3rem;
}
</style>
