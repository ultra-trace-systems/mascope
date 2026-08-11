<script setup>
import { ref, computed, watch, nextTick } from 'vue'

import ToggleSwitch from 'primevue/toggleswitch'

import { useApp } from '@/stores'
import { api } from '@/api'
import { num } from '@/lib/formatters'
import { formatIsotopeFormula } from '@/lib/chem'

import BaseChartPlotly from '../BaseChartPlotly.vue'
import { glasbey } from '../colors'

// Time-series of the focused assignment's isotopologue family (M0 + M+1 ...),
// or of the bare focused peak when it carries no assignment. Each peak's trace
// is pulled from the existing per-peak REST endpoint
// (POST /samples/{id}/peaks/timeseries, keyed by peak_id) -- no socket round
// trip, so the chart owns its own request lifecycle. Folded into the Sample
// view beneath the inspector + spectrum.

const app = useApp()

const props = defineProps({
  height: {
    type: Number,
    required: false
  }
})

const plot = ref(null)
const traces = ref([])
const loading = ref(false)
const log = ref(true)

const palette = glasbey.light

// The committed assignment for the focused peak and its isotopologue family.
const focusedAssignment = computed(() =>
  app.data.peakAssignment.peak.forPeak(app.data.peak.focused?.peak_id)
)
const family = computed(() => app.data.peakAssignment.peak.familyOf(focusedAssignment.value))

// Compact substitution label (e.g. "[13C]") from the isotopologue formula, with
// the offset label ("M0", "M+1") as the fallback for untargeted satellites.
const isoLabel = (iso) =>
  iso.isotope_formula ? formatIsotopeFormula(iso.isotope_formula) : iso.isotope_label || null

// The peaks to plot: the assignment family if there is one (so the whole
// envelope's time course shows), otherwise just the focused peak (so even an
// unassigned peak still gets its trace).
//
// Guarded against the sample-switch race: on a sample switch `sample.focusedId`
// flips immediately but the focused peak / family still belong to the previous
// sample until the peak store reloads, so fetching their ids against the new
// sample 404s. Wait for the peak store to settle (`!pending`) and only include
// peaks that exist in the current sample's peak list.
const members = computed(() => {
  if (app.data.peak.pending) return []
  const inSample = new Set(app.data.peak.list.map((peak) => String(peak.peak_id)))
  const fam = family.value
  if (fam.length) {
    return fam
      .filter((iso) => iso.sample_peak_id != null && inSample.has(String(iso.sample_peak_id)))
      .map((iso) => ({
        peakId: iso.sample_peak_id,
        mz: iso.sample_peak_mz,
        label: isoLabel(iso)
          ? `${isoLabel(iso)} ${num.mz.format(iso.sample_peak_mz)}`
          : num.mz.format(iso.sample_peak_mz)
      }))
  }
  const peak = app.data.peak.focused
  return peak && inSample.has(String(peak.peak_id))
    ? [{ peakId: peak.peak_id, mz: peak.mz, label: num.mz.format(peak.mz) }]
    : []
})

const sampleId = computed(() => app.data.sample.focusedId)

// A stable key over (sample, peak-id set): focusing a different member of the
// same family leaves it unchanged, so we don't refetch identical time series.
const membersKey = computed(() =>
  JSON.stringify([sampleId.value, members.value.map((m) => m.peakId)])
)

let requestToken = 0

async function load() {
  const sid = sampleId.value
  const mem = members.value
  const token = ++requestToken
  if (!sid || !mem.length) {
    traces.value = []
    loading.value = false
    return
  }
  loading.value = true
  try {
    const results = await Promise.all(
      mem.map((member) =>
        api.http
          .post(
            `/samples/${sid}/peaks/timeseries`,
            { peak_id: member.peakId },
            { use: 'read', type: 'load_peak_timeseries' }
          )
          .then((response) => ({ member, response }))
      )
    )
    // A newer focus superseded this request while it was in flight.
    if (token !== requestToken) return

    const built = results
      .filter(({ response }) => response?.time?.length)
      .map(({ member, response }, index) => ({
        name: member.label,
        type: 'scatter',
        mode: 'lines',
        line: { color: palette[index % palette.length], width: index === 0 ? 2 : 1.3 },
        x: new Float32Array(response.time),
        y: new Float32Array(response.height),
        hovertemplate:
          [`<i>${member.label}</i>`, 't: <b>%{x:.1f} s</b>', 'intensity: <b>%{y:.3e}</b>'].join(
            '<br>'
          ) + '<extra></extra>'
      }))

    // Sum trace (total for the family) when every member shares a time axis.
    if (built.length > 1) {
      const len = built[0].y.length
      if (built.every((trace) => trace.y.length === len)) {
        const sum = new Float32Array(len)
        for (const trace of built) {
          for (let i = 0; i < len; i += 1) sum[i] += trace.y[i]
        }
        built.push({
          name: 'Sum',
          type: 'scatter',
          mode: 'lines',
          line: { color: '#9aa2b1', width: 1, dash: 'dot' },
          x: built[0].x,
          y: sum,
          hovertemplate:
            ['<i>Sum</i>', 't: <b>%{x:.1f} s</b>', 'intensity: <b>%{y:.3e}</b>'].join('<br>') +
            '<extra></extra>'
        })
      }
    }
    traces.value = built
  } finally {
    if (token === requestToken) loading.value = false
  }
}

watch(membersKey, load, { immediate: true })

watch(
  () => props.height,
  async () => {
    await nextTick()
    plot.value?.resize()
  }
)

watch(
  () => app.ui.tab.active,
  async (tab) => {
    if (tab === 'sample') {
      await nextTick()
      plot.value?.resize()
    }
  }
)

const layout = computed(() => ({
  xaxis: {
    title: { text: 'Time [s]' },
    autorange: true,
    showgrid: true,
    gridcolor: '#33333399',
    gridwidth: 1
  },
  yaxis: {
    title: { text: 'Peak intensity' },
    showgrid: true,
    autorange: true,
    rangemode: 'tozero',
    type: log.value ? 'log' : 'lin',
    gridcolor: '#33333399',
    gridwidth: 1
  },
  margin: { l: 60, r: 10, t: 45, b: 45 },
  dragmode: 'zoom',
  showlegend: true,
  legend: { x: 1, y: 1 }
}))
</script>

<template>
  <BaseChartPlotly
    id="ChartAssignmentTimeseries"
    ref="plot"
    title="Time series"
    :data="traces"
    :layout="layout"
    :loading="loading"
  >
    <template v-slot:settings>
      <div class="row" style="align-items: center; gap: 0.5rem">
        <ToggleSwitch v-model="log" />
        <span>log scale</span>
      </div>
    </template>
  </BaseChartPlotly>
  <div v-if="!loading && !traces.length" class="ts-empty">
    <div class="col" style="gap: 0.5rem; text-align: center; opacity: 0.6">
      <span class="pi ph ph-wave-sine" style="font-size: 1.4rem" />
      <i>Select a peak to see its time series.</i>
    </div>
  </div>
</template>

<style scoped>
.ts-empty {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  pointer-events: none;
}
</style>
