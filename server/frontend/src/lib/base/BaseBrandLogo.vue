<script setup>
import { runtime } from '@/lib/runtime'
import logoUrl from '@/assets/ultra-trace-logo.png'
</script>

<template>
  <div class="col">
    <div class="row">
      <span class="brand">
        <img :src="logoUrl" alt="Ultra Trace" class="wordmark" />
        <span class="version">{{ runtime.version }}</span>
      </span>
      <b style="font-size: 15px; margin-left: 4px">Mascope</b>
    </div>
  </div>
</template>

<style scoped>
img,
div {
  margin: 0;
  padding: 0;
}

.col {
  gap: 0;
  justify-content: center;
  /* Reserve room for the version, which hangs below the row out of flow. */
  padding-bottom: 0.75rem;
}

.row {
  justify-content: center;
}

.wordmark {
  height: 24px;
  display: block;
}

/* Positioning context for the version label: wraps just the wordmark so
   the absolutely-placed label is centred on it. */
.brand {
  position: relative;
  display: flex;
}

.version {
  /* Anchored to the wordmark, so it stays centred under it for any tag length
     and never affects the logo's layout. */
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  white-space: nowrap;
  opacity: 0.5;
  pointer-events: none; /* Prevents the logo from affecting layout interactions */
  font-size: 10px;
}
</style>
