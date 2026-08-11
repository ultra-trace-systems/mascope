<script setup>
import { computed } from 'vue'

import { BaseBrandLogo } from '@/lib/base'

import Dashboard from './Dashboard.vue'
import ProgressSpinner from 'primevue/progressspinner'

import { useApp } from '@/stores'

const app = useApp()
const ready = computed(() => !app.data.workspace.pending || app.data.workspace.list.length > 0)
</script>

<template>
  <!-- App Dashboard. Not id="app": that is the Vue mount div in index.html,
       and nesting a second element with the same id is invalid HTML. -->
  <div id="dashboard" v-if="ready">
    <Dashboard />
  </div>
  <!-- Loading Spinner  -->
  <div id="loading" v-else>
    <div class="col">
      <BaseBrandLogo />
      <ProgressSpinner />
      <strong>Loading...</strong>
    </div>
  </div>
</template>
