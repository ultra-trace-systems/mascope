import { ref } from 'vue'
import { defineStore } from 'pinia'

import { useApp } from '@/stores'

import { api } from '@/api'

export const useChartData = defineStore('chart.match.spectra', () => {
  const traces = ref([])
  const length = ref()
  const unit = ref('')

  const app = useApp()

  app.ui.chart.register({
    name: 'ChartMatchSpectra',
    clear: () => {
      traces.value = []
      length.value = 0
    }
  })

  api.socket.on('visualization_signal_sum_spectrum', ({ sid, data }) => {
    // Ignore if SID does not match (user has multiple sessions open)
    if (sid !== api.socket.id) return
    for (let trace of data) {
      length.value = length.value + trace.x.length
      unit.value = trace.unit ? trace.unit : unit.value
      trace.x = new Float32Array(trace.x)
      trace.y = new Float32Array(trace.y)

      // Check if the trace has target_isotope_id and update the corresponding isotope in activeIsotopes
      if (trace.target_isotope_id) {
        const isotope = app.data.match.visualized.isotopes?.find(
          (iso) => iso.target_isotope_id === trace.target_isotope_id
        )
        if (isotope) {
          // The trace colour is already a CSS rgb() string, so the isotope
          // swatch in the rating dialog can take it as-is. It used to be
          // rescaled here because the backend sent colorcet's 0-1 floats
          // verbatim; it now scales them to 0-255 itself, since plotly.js 4
          // parses colours per the CSS spec and reads an unscaled component
          // as near-zero.
          isotope.color = trace.line.color
        }
      }
    }
    traces.value = [...traces.value, ...data]
  })
  return { traces, length, unit }
})
