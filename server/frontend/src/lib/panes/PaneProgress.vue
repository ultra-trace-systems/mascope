<script setup>
import { useApp } from '@/stores'
import { notificationTitle } from '@/lib/toastTitles'

const app = useApp()

function color(type) {
  if (type.includes('calibrat')) {
    return 'orange'
  }
  if (type.includes('acquisition')) {
    return 'indigo'
  }
  if (type.includes('conversion')) {
    return 'pink'
  }
  if (type == 'process_sample_item') {
    return 'teal'
  }
  return 'sky'
}
</script>

<template>
  <div id="progress">
    <template v-for="process of app.ui.notification.progress" :key="process.process_id">
      <div
        v-if="process.progress > 0"
        v-tooltip.top="`${notificationTitle(process.type)}: ${process.message}`"
        class="progress-bar"
        :style="`
          background-color: var(--p-${color(process.type)}-500);
          width: ${process.progress}%;
          z-index: -${Math.floor(process.progress)};
        `"
      />
    </template>
  </div>
</template>

<style scoped>
#progress {
  position: relative;
  grid-area: progress;
  opacity: 0.7;
}

.progress-bar {
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  outline: 3px solid var(--p-panel-background);
  border-radius: 2.5px;
  transition: width 500ms;
}
</style>
