<script setup>
import { computed, ref, watch } from 'vue'

import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import Select from 'primevue/select'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'

// From their files, not the base index: the index loads components that read
// the runtime config on import.
import BaseEmptyState from '@/lib/base/BaseEmptyState.vue'
import BaseLoadError from '@/lib/base/BaseLoadError.vue'
import BaseChartPlotly from '@/lib/charts/BaseChartPlotly.vue'
import { api } from '@/api'
import { useApp } from '@/stores'
import {
  EXPORT_MAX_FILES,
  MARK_COLOR,
  OTHER_COLOR,
  TREND_TIMEOUT_MS,
  TREND_WINDOWS,
  changeLabel,
  trendExportUrl,
  trendFigure,
  trendQuery
} from '@/lib/calibrationTrend'

// The calibration trend of one polarity of a TOF instrument: what its files'
// calibration records say over a window of acquisition times, drawn as small
// multiples on one time axis (see calibrationTrend.js). Opened from the Raw
// files tab for the instrument it has focused.

const app = useApp()

const visible = defineModel('visible')

const props = defineProps({
  // The instrument's name, as its files spell it.
  instrument: {
    type: String,
    required: false,
    default: null
  },
  // The polarity first shown.
  polarity: {
    type: String,
    required: false,
    default: '-'
  }
})

const POLARITIES = [
  { label: 'Positive (+)', value: '+' },
  { label: 'Negative (-)', value: '-' }
]

const polarity = ref(props.polarity)
const days = ref(90)
const trend = ref(null)
const loading = ref(false)
const error = ref(null)
let query = null
let request = 0
// The instrument whose trend `trend` holds.
let shownFor = null

const load = async () => {
  if (!props.instrument) return
  const current = ++request
  const instrument = props.instrument
  // Another instrument's chart must not stand under this one's name while
  // the read runs: the spinner shows instead. A re-read of the same
  // instrument, another polarity or window, keeps its chart, faded.
  if (shownFor !== instrument) trend.value = null
  query = trendQuery(instrument, polarity.value, days.value)
  loading.value = true
  error.value = null
  try {
    const { data } = await api.http.get('/calibration/trend', {
      params: query,
      type: 'calibration_trend',
      errors: 'inline',
      timeout: TREND_TIMEOUT_MS
    })
    if (current === request) {
      trend.value = data?.data ?? null
      shownFor = instrument
    }
  } catch (caught) {
    if (current === request) {
      error.value = caught
      trend.value = null
    }
  } finally {
    if (current === request) loading.value = false
  }
}

// Each time it opens, from the polarity it is opened with: files may have
// been calibrated since, and another instrument may be focused.
watch(
  visible,
  (shown) => {
    if (!shown) return
    // A new polarity loads through its own watcher below.
    if (polarity.value !== props.polarity) polarity.value = props.polarity
    else load()
  },
  { immediate: true }
)
watch([polarity, days], () => {
  if (visible.value) load()
})

const dark = computed(() => Boolean(app.ui.darkmode?.active))
const figure = computed(() => (trend.value ? trendFigure(trend.value, { dark: dark.value }) : null))
const files = computed(() => trend.value?.files ?? [])
// A window of more files than the route serves one by one comes in bins.
const binned = computed(() => Boolean(trend.value?.binned))
const bins = computed(() => trend.value?.bins ?? [])
// How many files the trend shows, one by one or summed up in its bins.
const fileCount = computed(() => trend.value?.n_files ?? 0)
const count = (n) => n.toLocaleString('en-US')
const theme = computed(() => (dark.value ? 'dark' : 'light'))
const per = computed(() => (trend.value?.bin === 'day' ? 'day' : 'hour'))

// Files fitted before the records listed their lines: on the counts and the
// mean residual only.
const withoutLines = computed(() =>
  binned.value
    ? bins.value.reduce((sum, bin) => sum + bin.n_without_lines, 0)
    : files.value.filter((file) => file.status !== 'failed' && file.n_candidates == null).length
)

// Fits whose record names no collection, drawn in the "other" colour.
const unnamed = computed(() =>
  binned.value
    ? bins.value.some((bin) => bin.collection_id == null && bin.n_files > bin.n_failed)
    : files.value.some((file) => file.collection == null && file.status !== 'failed')
)

const anyFailed = computed(() =>
  binned.value
    ? bins.value.some((bin) => bin.n_failed > 0)
    : files.value.some((file) => file.status === 'failed')
)

// Which polarity and window came back empty, and where else to look: the
// polarity it opens on is a guess when the list shows no file of the
// instrument.
const emptyHint = computed(() => {
  const read = polarity.value === '+' ? 'positive' : 'negative'
  const other = polarity.value === '+' ? 'negative' : 'positive'
  return (
    `No ${read} TOF file of this instrument was calibrated in this window. ` +
    `Try the ${other} polarity or a longer window.`
  )
})

const changes = computed(() =>
  (trend.value?.changes ?? []).map((change, index) => ({
    number: index + 1,
    when: change.datetime_utc.slice(0, 16).replace('T', ' '),
    label: changeLabel(change, trend.value)
  }))
)

const exportCsv = () => {
  if (!query) return
  const link = document.createElement('a')
  link.href = trendExportUrl(query)
  link.download = ''
  link.click()
}
</script>

<template>
  <Dialog
    v-model:visible="visible"
    :header="`Calibration trend: ${instrument ?? ''}`"
    modal
    dismissableMask
    maximizable
    style="width: min(72rem, 96vw)"
  >
    <menu class="controls">
      <Select
        v-model="polarity"
        :options="POLARITIES"
        optionLabel="label"
        optionValue="value"
        aria-label="Polarity"
      />
      <Select
        v-model="days"
        :options="TREND_WINDOWS"
        optionLabel="label"
        optionValue="days"
        aria-label="Acquired in"
      />
      <Button
        label="Export CSV"
        icon="pi pi-download"
        severity="secondary"
        text
        :disabled="!fileCount"
        @click="exportCsv"
      />
    </menu>
    <p class="intro">
      What each TOF file's calibration record says, by acquisition time: how far its axis was off
      before the fit, the fit's mean residual, how many calibrant lines it kept, and each line's
      residual. Times are UTC.
    </p>
    <BaseLoadError
      v-if="error"
      :error="error"
      fallback="Could not load the calibration trend."
      :onRetry="load"
    />
    <ProgressSpinner v-else-if="loading && !trend" />
    <BaseEmptyState
      v-else-if="trend && !fileCount"
      icon="pi pi-chart-line"
      title="No calibrated files"
      :hint="emptyHint"
    />
    <template v-else-if="figure">
      <Message v-if="trend.truncated" severity="warn" size="small" :closable="false">
        The window holds more fitted or failed files than one trend reads: the latest
        {{ count(fileCount) }} are shown.
      </Message>
      <Message v-if="binned" severity="info" size="small" :closable="false">
        {{ count(fileCount) }} fitted or failed files, more than the chart draws one by one: each
        panel shows their median per {{ per }} and, shaded, the middle half of them. Choose a
        shorter window to see each file.
        <template v-if="fileCount > EXPORT_MAX_FILES">
          Export CSV lists the latest {{ count(EXPORT_MAX_FILES) }} files one by one.
        </template>
      </Message>
      <ul class="legend" aria-label="Legend">
        <li v-for="group in figure.groups" :key="group.id">
          <span class="swatch" :style="{ background: group.color }" />{{ group.label }}
        </li>
        <li v-if="unnamed">
          <span class="swatch" :style="{ background: OTHER_COLOR[theme] }" />No collection on record
        </li>
        <li v-if="figure.points">
          <span class="dot" :style="{ background: OTHER_COLOR[theme] }" />Line kept by the fit
        </li>
        <li v-if="figure.points">
          <span class="dot open" :style="{ borderColor: OTHER_COLOR[theme] }" />Line left out by the
          fit
        </li>
        <li v-if="figure.summary">
          <span class="median" :style="{ background: OTHER_COLOR[theme] }" />Median per {{ per }},
          the middle half shaded
        </li>
        <li v-if="anyFailed">
          <span class="cross" :style="{ color: MARK_COLOR[theme] }">×</span>Calibration failed
        </li>
      </ul>
      <ol v-if="changes.length" class="changes" aria-label="Collection changes">
        <li v-for="change in changes" :key="change.number">
          <strong>{{ change.number }}</strong> {{ change.when }} · {{ change.label }}
        </li>
      </ol>
      <p v-if="withoutLines" class="muted">
        {{ withoutLines }} file{{ withoutLines === 1 ? ' was' : 's were' }} fitted before records
        listed their lines, and show{{ withoutLines === 1 ? 's' : '' }} on the mean residual and the
        counts only.
      </p>
      <div class="chart" :style="{ height: `${figure.height}px` }">
        <BaseChartPlotly
          id="calibration-trend"
          :title="`Calibration trend ${instrument}`"
          hideTitle
          :data="figure.data"
          :layout="figure.layout"
          :loading="loading"
        />
      </div>
    </template>
  </Dialog>
</template>

<style scoped>
.controls {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  flex-wrap: wrap;
  margin: 0 0 0.5rem;
  padding: 0;
}

.intro,
.muted {
  margin: 0.25rem 0 0.5rem;
  color: var(--p-text-muted-color);
}

.legend,
.changes {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem 1.25rem;
  margin: 0.5rem 0;
  padding: 0;
  list-style: none;
  font-size: 0.875rem;
}

.legend li,
.changes li {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.swatch {
  display: inline-block;
  width: 0.9rem;
  height: 0.9rem;
  border-radius: 3px;
}

.dot {
  display: inline-block;
  width: 0.6rem;
  height: 0.6rem;
  border-radius: 50%;
}

.dot.open {
  background: transparent;
  border: 1.5px solid;
}

.cross {
  font-weight: 700;
}

.median {
  display: inline-block;
  width: 1.1rem;
  height: 2px;
}

.chart {
  width: 100%;
  min-width: 0;
}
</style>
