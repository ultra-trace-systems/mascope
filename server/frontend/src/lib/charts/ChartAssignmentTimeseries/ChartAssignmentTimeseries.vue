<script setup>
import { ref, shallowRef, computed, watch, nextTick } from 'vue'

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
// The fetched time series, one per member peak: { peakId, response }.
const series = shallowRef([])
const loading = ref(false)
const log = ref(true)

const palette = glasbey.light

// The committed assignment for the focused peak and its isotopologue family.
const focusedAssignment = computed(() =>
  app.data.peakAssignment.peak.forPeak(app.data.peak.focused?.peak_id)
)
const family = computed(() => app.data.peakAssignment.peak.familyOf(focusedAssignment.value))

// A sample the batch ledger serves: its rows store no isotope labels or
// formulas, so read alone they name each line by its m/z. The inspector card
// measures the family's composition against this sample's peaks on focus
// (`loadEvidence`, through the M0) and names the lines from that measurement;
// the chart reads the same record from the store and asks for none of its own.
// Until it arrives the lines keep their m/z.
const ledgerServed = computed(() => app.data.peakAssignment.peak.run?.engine === 'batch')
const familyM0 = computed(() => app.data.peakAssignment.peak.m0Of(focusedAssignment.value))
const measured = computed(() =>
  ledgerServed.value && familyM0.value
    ? (app.data.peakAssignment.peak.evidenceOf(familyM0.value.peak_assignment_id) ?? null)
    : null
)
// The measured isotopologues by the peak each paired to.
const measuredByPeak = computed(() => {
  const map = new Map()
  for (const iso of measured.value?.isotopologues ?? []) {
    if (iso.sample_peak_id != null) map.set(String(iso.sample_peak_id), iso)
  }
  return map
})
// A family row with the measured label filled in where it has none of its own.
// The stored label always wins, so a run's own row reads exactly as it did.
const withMeasured = (iso) => {
  const record = measured.value
  if (!record || iso.isotope_formula || iso.isotope_label) return iso
  if (iso.peak_assignment_id === familyM0.value?.peak_assignment_id) {
    return { ...iso, isotope_label: 'M0' }
  }
  const found = measuredByPeak.value.get(String(iso.sample_peak_id))
  return found
    ? {
        ...iso,
        isotope_label: found.isotope_label ?? null,
        isotope_formula: found.isotope_formula ?? null
      }
    : iso
}

// Compact substitution label (e.g. "[13C]") from the isotopologue formula, with
// the offset label ("M0", "M+1") as the fallback for untargeted isotopologues.
// Counted from the family's M0, which the ion formula names for a labelled ion
// (see formatIsotopeFormula): the row's own, or its M0's when it recorded none.
const isoLabel = (iso) =>
  iso.isotope_formula
    ? formatIsotopeFormula(
        iso.isotope_formula,
        iso.ion_formula ?? app.data.peakAssignment.peak.m0Of(iso)?.ion_formula
      )
    : iso.isotope_label || null

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
      .map(withMeasured)
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
// A label that changes (the ledger's measurement arriving) leaves it unchanged
// too: the traces take their names from `members` when they are drawn, not
// when they are fetched.
const membersKey = computed(() =>
  JSON.stringify([sampleId.value, members.value.map((m) => m.peakId)])
)

let requestToken = 0

async function load() {
  const sid = sampleId.value
  const mem = members.value
  const token = ++requestToken
  if (!sid || !mem.length) {
    series.value = []
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

    series.value = results
      .filter(({ response }) => response?.time?.length)
      .map(({ member, response }) => ({
        peakId: member.peakId,
        mz: member.mz,
        x: new Float32Array(response.time),
        y: new Float32Array(response.height)
      }))
  } finally {
    if (token === requestToken) loading.value = false
  }
}

// The fetched series as Plotly traces, each named by its member's current
// label, plus the family's sum when every member shares a time axis.
const traces = computed(() => {
  const labels = new Map(members.value.map((m) => [String(m.peakId), m.label]))
  const built = series.value.map(({ peakId, mz, x, y }, index) => {
    const label = labels.get(String(peakId)) ?? num.mz.format(mz)
    return {
      name: label,
      type: 'scatter',
      mode: 'lines',
      line: { color: palette[index % palette.length], width: index === 0 ? 2 : 1.3 },
      x,
      y,
      hovertemplate:
        [`<i>${label}</i>`, 't: <b>%{x:.1f} s</b>', 'intensity: <b>%{y:.3e}</b>'].join('<br>') +
        '<extra></extra>'
    }
  })
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
  return built
})

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
  // Inside the plot, at its top right: outside it, the legend takes its width
  // off the plot, and the time series no longer lines up with the spectrum
  // above it, which has the same margins and no legend.
  legend: { x: 1, xanchor: 'right', y: 1, yanchor: 'top' }
}))

// For a container that changes width while its height stays: a splitter
// divider beside the chart, which the `height` watcher never sees.
defineExpose({ resize: () => plot.value?.resize?.() })
</script>

<template>
  <div
    class="ts-wrap"
    v-help.top="{
      message: `
        <h1>Assignment Time Series</h1>
        <p>
        Intensity over the acquisition for the focused peak. For an assigned
        peak the whole isotopologue family is drawn &mdash; one line per
        isotopologue, the main peak (M0) thickest, with a dotted grey
        <b>Sum</b> of the family.
        </p>
        <p>
        The y-axis is log-scaled by default; change it from the chart settings
        (top left).
        </p>`,
      doc: app.ui.help.docUrl('how-it-works/peak-assignment/')
    }"
  >
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
        <span class="pi ph ph-cursor-click" style="font-size: 1.4rem" />
        <i>Select a peak to see its time series.</i>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ts-wrap {
  position: relative;
  height: 100%;
  width: 100%;
}
.ts-empty {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  pointer-events: none;
}
</style>
