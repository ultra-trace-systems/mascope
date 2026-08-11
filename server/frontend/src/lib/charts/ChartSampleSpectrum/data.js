import { ref, shallowRef, watch, computed } from 'vue'
import { defineStore } from 'pinia'

import { useApp } from '@/stores'
import { api } from '@/api'
import { usePreview } from '@/lib/panes'
import { peakAssignmentEnabled } from '@/lib/features'

// Peak coloring by confidence tier for the annotated spectrum. One Plotly trace
// per tier; role reagent/artifact is grouped separately (orthogonal to tier).
const TIER_TRACES = [
  { key: 'identified', name: 'Identified', color: '#1f9d63' },
  { key: 'candidate', name: 'Candidate', color: '#d99a2b' },
  { key: 'below_assignability', name: 'Below assignability', color: '#8a94a6' },
  { key: 'reagent', name: 'Reagent / artifact', color: '#8a5ed0' },
  { key: 'unassigned', name: 'Unassigned', color: 'grey' }
]

// Which tier bucket a peak falls in, given the run's assignments.
function bucketOf(assignments, peak) {
  const assignment = assignments.forPeak(peak.peak_id)
  if (!assignment) return 'unassigned'
  if (assignment.role === 'reagent' || assignment.role === 'artifact') return 'reagent'
  const tier = assignment.tier
  return tier === 'identified' || tier === 'candidate' || tier === 'below_assignability'
    ? tier
    : 'unassigned'
}

// Build a vertical-stick Plotly trace for a set of peaks. Three points per peak
// (0 -> height -> gap) so the hover tooltip triggers along the whole marker.
// customdata carries [height, area, mz, formula]; [height, area] also lets
// ChartSampleSpectrum rescale for "average" instead of "sum".
function peakTrace(name, color, peaks, assignments = null) {
  return {
    name,
    type: 'scatter',
    mode: 'lines',
    line: { color },
    x: peaks.map(({ mz }) => [mz, mz, null]).flat(),
    y: peaks.map(({ height }) => [0, height, null]).flat(),
    customdata: peaks
      .map((peak) => {
        const formula = assignments?.forPeak(peak.peak_id)?.assigned_formula ?? ''
        const point = [peak.height, peak.area, peak.mz, formula]
        return [point, point, null]
      })
      .flat(),
    hovertemplate:
      [
        `<i>${name}</i>`,
        'mz: <b>%{customdata[2]:.4f}</b>',
        assignments ? 'formula: <b>%{customdata[3]}</b>' : null,
        'height: <b>%{customdata[0]:.3e}</b>',
        'area: <b>%{customdata[1]:.3e}</b>'
      ]
        .filter(Boolean)
        .join('<br>') + '<extra></extra>'
  }
}

export const useChartData = defineStore('chart.sample.spectrum', () => {
  const spectrumData = shallowRef(null)
  const length = ref()
  const unit = ref('')
  const loading = ref(false)

  const app = useApp()
  const preview = usePreview()

  app.ui.chart.register({
    name: 'ChartSampleSpectrum',
    clear: () => {
      // not needed
    }
  })

  // Watch for sample change - clear data and reset chart
  watch(
    () => app.data.sample.focusedId,
    (sampleId, oldSampleId) => {
      if (sampleId !== oldSampleId) {
        console.debug('🔄 [chart.sample.spectrum] sample changed - resetting chart')
        unload()
      }
      if (sampleId) {
        load()
      }
    }
  )

  const gl = ''

  // load spectrum data
  async function load() {
    const sampleItemId = app.data.sample.focusedId
    // start loading
    loading.value = true
    // get spectrum data from the backend
    spectrumData.value = await api.http.get(`/samples/${sampleItemId}/spectrum`, {
      use: 'read',
      type: 'get_spectrum'
    })

    unit.value = spectrumData.value.intensity_unit
    length.value = spectrumData.value.intensity.length
    loading.value = false
  }

  const mainTraces = computed(() => {
    const traces = []
    // add spectrum trace
    if (spectrumData.value) {
      traces.push({
        name: 'Signal',
        line: {
          color: 'green'
        },
        mode: 'lines',
        type: 'scatter' + gl,
        x: new Float32Array(spectrumData.value.mz),
        y: new Float32Array(spectrumData.value.intensity),
        hovertemplate:
          ['<i>Signal</i>', 'm/z: <b>%{x:.4f}</b>', `intensity: <b>%{y:.3e}</b>`].join('<br>') +
          '<extra></extra>' // use "<extra></extra>" to get rid of extra block from the hoverbox
      })
    }
    // add peak traces, colored by assignment tier
    if (!app.data.peak.pending && app.data.peak.list.length > 0) {
      const assignments = app.data.peakAssignment.peak
      // Tier colouring is part of the peak-centric feature; with it off the
      // spectrum stays the single grey trace even if a run exists from an
      // explicit assignment.
      if (!peakAssignmentEnabled || !assignments.run) {
        // No assignment run: keep the original single grey peak trace.
        traces.push(peakTrace('Peak', 'grey', app.data.peak.list))
      } else {
        // Annotated spectrum: one trace per confidence tier.
        for (const { key, name, color } of TIER_TRACES) {
          const peaks = app.data.peak.list.filter((peak) => bucketOf(assignments, peak) === key)
          if (peaks.length > 0) traces.push(peakTrace(name, color, peaks, assignments))
        }
      }
    }
    return traces
  })

  const focusTrace = computed(() => {
    const focused = app.data.peak.focused
    return focused
      ? [
          {
            name: 'Focused Peak',
            type: 'scatter' + gl,
            mode: 'lines+markers',
            line: {
              color: '#fb8f74'
            },
            x: [focused.mz, focused.mz], // *
            y: [0, focused.height], // *
            customdata: [
              [focused.height, focused.area, focused.mz],
              [focused.height, focused.area, focused.mz]
            ],
            hovertemplate:
              [
                '<i>Peak</i>',
                'mz: <b>%{customdata[2]:.4f}</b>',
                'height: <b>%{customdata[0]:.3e}</b>',
                'area: <b>%{customdata[1]:.3e}</b>'
              ].join('<br>') + '<extra></extra>'
          }
        ]
      : []
  })
  const previewTrace = computed(() =>
    preview.peak
      ? [
          {
            name: 'Peak Preview',
            line: {
              color: 'red'
            },
            mode: 'lines',
            type: 'scatter' + gl,
            x: [...Array(3).keys()].map(() => preview.peak.mz), // *
            y: [...Array(3).keys()].map(
              (index) =>
                (preview.peak.relative_abundance / preview.peak.abundance_reference) *
                preview.peak.intensity_reference *
                (index / 2)
            ),
            hovertemplate: ['<i>Peak</i>', 'mz: <b>%{x:.4f}</b>'].join('<br>') + '<extra></extra>'
          }
        ]
      : []
  )

  // Theoretical isotopologue envelope of the focused assignment, for visual
  // verification against the measured peaks. Position and expected height are
  // recovered from the stored errors: theoretical m/z = measured / (1 + ppm/1e6)
  // and expected height = intensity / (1 + abundance_error) (= M0 intensity x
  // predicted relative abundance). Named "…Peak" so the intensity-scale toggle
  // scales it like the measured peaks.
  //
  // Part of the peak-centric feature, like the tier colouring above: assignment
  // rows can exist without the flag (written by the CLI, by processing, or by a
  // prior flag-on deployment), and drawing them would put an unexplained
  // theoretical envelope in a spectrum that opted into nothing.
  const envelopeTrace = computed(() => {
    if (!peakAssignmentEnabled) return []
    const focused = app.data.peak.focused
    if (!focused) return []
    const assignments = app.data.peakAssignment.peak
    const assignment = assignments.forPeak(focused.peak_id)
    if (!assignment) return []
    const familyMembers = assignments.familyOf(assignment)
    if (familyMembers.length < 2) return []

    const points = familyMembers
      .map((iso) => {
        if (iso.sample_peak_intensity == null) return null
        const theoreticalMz =
          iso.mz_error_ppm != null
            ? iso.sample_peak_mz / (1 + iso.mz_error_ppm / 1e6)
            : iso.sample_peak_mz
        const denom = 1 + (iso.abundance_error ?? 0)
        if (denom <= 0) return null
        return { mz: theoreticalMz, height: iso.sample_peak_intensity / denom }
      })
      .filter(Boolean)
    if (!points.length) return []

    return [
      {
        name: 'Theoretical Peak',
        type: 'scatter' + gl,
        mode: 'lines+markers',
        line: { color: '#d1345b', width: 1.5 },
        marker: { color: '#d1345b', size: 6, symbol: 'circle-open' },
        x: points.map(({ mz }) => [mz, mz, null]).flat(),
        y: points.map(({ height }) => [0, height, null]).flat(),
        customdata: points
          .map(({ height, mz }) => {
            const point = [height, height, mz]
            return [point, point, null]
          })
          .flat(),
        hovertemplate:
          [
            '<i>Theoretical</i>',
            'mz: <b>%{customdata[2]:.4f}</b>',
            'expected height: <b>%{customdata[0]:.3e}</b>'
          ].join('<br>') + '<extra></extra>'
      }
    ]
  })

  const traces = computed(() => [
    ...mainTraces.value,
    ...focusTrace.value,
    ...previewTrace.value,
    ...envelopeTrace.value
  ])

  // unload data and switch tab if necessary
  function unload() {
    spectrumData.value = null
  }

  return { traces, length, unit, loading }
})
