<script setup>
import { nextTick, ref, computed, toRaw, watch } from 'vue'

import Tag from 'primevue/tag'

import { BaseMatchTag } from '@/lib/base'
import { clone } from '@/lib/utils'
import { num } from '@/lib/formatters'
import { formatIsotopeFormula } from '@/lib/chem'
import { useApp } from '@/stores'

import BaseChartPlotly from '../BaseChartPlotly.vue'
import { useChartData } from './data.js'

const app = useApp()
const data = useChartData()

const plots = ref({})

const props = defineProps({
  sidebarOpen: {
    type: Boolean,
    required: true
  },
  height: {
    type: Number,
    required: false
  }
})

const scale = defineModel()

const sampleLength = computed(() =>
  app.data.sample.selected.length != 1 ? null : app.data.sample.selected[0].length
)

const traces = computed(() => {
  // Scale trace y-values based on "average / sum" toggle
  if (sampleLength.value === null) {
    return []
  }
  return scale.value.mode == 'average'
    ? data.traces
    : // Shallow copy with scaled y; unchanged fields keep sharing the
      // store's arrays
      data.traces.map((trace) => ({
        ...toRaw(trace),
        y: trace.y.map((value) => value * sampleLength.value)
      }))
})

// transform raw visualiation data into seperate charts
const isotopeCharts = computed(() => {
  // Build array with first isotope and selected isotope (if different)
  if (app.data.match.visualized.isotopes === null) return []

  const isotopeList = []

  if (app.data.match.visualized.isotopes?.[0]) {
    isotopeList.push(app.data.match.visualized.isotopes[0])
  }
  if (
    app.data.match.visualized.isotopeSelected &&
    app.data.match.visualized.isotopeSelected !== isotopeList[0]
  ) {
    isotopeList.push(app.data.match.visualized.isotopeSelected)
  }

  // Map over the limited isotope list
  return isotopeList.map((isotope) => {
    // split up the chart's traces by isotope
    const start = traces.value?.findIndex(
      (trace) => trace.target_isotope_id === isotope.target_isotope_id
    )
    const nextStart = traces.value?.findIndex(
      ({ target_isotope_id }, index) => target_isotope_id && index > start
    )
    const end =
      nextStart !== -1 // if next isotope found
        ? nextStart // use it as the end of isotope trace data
        : traces.value?.length // otherwise use all remaining data
    const isotopeTraces = traces.value?.slice(start, end)
    return {
      // all match isotope fields
      ...isotope,
      // and our computed data
      traces: isotopeTraces
    }
  })
})

// The isotope's compact label, counted from the ion's M0, which the ion formula
// names for a labelled ion (see formatIsotopeFormula). The isotope rows carry no
// ion formula of their own; they are the visualized ion's.
const isotopeLabel = (isotope) =>
  formatIsotopeFormula(
    isotope.target_isotope_formula,
    app.data.match.visualized.ion?.target_ion_formula
  )

// compute match category with UI match params
const getIsotopeCategory = (isotope) => {
  if (!isotope?.match) return 0
  // This accesses app.data.match.params.ui inside the function,
  return app.data.match.params.uiCategory(isotope.match)
}

// auto vs manual scale
const rangeY = computed(
  () =>
    scale.value.max // if user set the scale
      ? { range: [0, scale.value.max], autorange: false } // use set scale
      : { range: null, autorange: true } // otherwise auto set scale
)

// Watch for changes in number of isotope charts and resize all plots
watch(
  () => isotopeCharts.value.length,
  async (newLength, oldLength) => {
    // Clear stale refs when chart count decreases
    if (newLength < oldLength) {
      for (let i = newLength; i < oldLength; i++) {
        delete plots.value[i]
      }
    }
    // Wait for DOM to update and refs to be assigned
    await nextTick()
    Object.values(plots.value).forEach((plot) => {
      if (plot?.resize) {
        plot.resize()
      }
    })
  }
)

// Resize plots when container dimensions change
watch([() => app.ui.split.right, () => props.height], async () => {
  await nextTick()
  Object.values(plots.value).forEach((plot) => plot?.resize?.())
})

// standard plotly layout, a clone of this is used for each chart
const layout = computed(() => {
  return {
    yaxis: {
      title: {
        text: `Signal intensity [${scale.value.mode == 'average' ? 'counts/s' : 'counts'}]`
      },
      gridcolor: '#33333399',
      rangemode: 'nonnegative',
      type: 'lin',
      ...rangeY.value
    },
    xaxis: {
      title: { text: 'm/z [Th]' },
      gridcolor: '#33333399',
      autorange: true
    },
    margin: { l: 50, r: 20, t: 40, b: 40 },
    dragmode: 'zoom',
    showlegend: false
  }
})
</script>

<template>
  <div class="spectra-container">
    <div class="row spectra-scroll">
      <figure
        v-for="(isotopeChart, index) of isotopeCharts"
        :key="`${isotopeChart.target_isotope_id}-${height}`"
        class="spectra-figure"
        :class="{ sidebarOpen }"
      >
        <h3 :style="`margin: 0`">
          <BaseMatchTag
            :match-score="isotopeChart.match?.match_score"
            :match-category="getIsotopeCategory(isotopeChart)"
            :alarming="isotopeChart.match?.alarming"
          />
          {{ isotopeLabel(isotopeChart) }}:
          {{ num.mz.format(isotopeChart.mz) }}
        </h3>
        <!--
            This chart uses a *function ref* to enable dynamically
            assigning refs to a reactive object. We use the `index`
            rather than the `target_isotope_id` to ensure that we
            can persist or reset zoom correctly as we switch targets
            and samples.

            See https://vuejs.org/guide/essentials/template-refs.html#function-refs
          -->
        <BaseChartPlotly
          :id="`ChartMatchSpectrum-${isotopeChart.target_isotope_id}`"
          :title="`${isotopeChart.target_isotope_formula}: ${num.mz.format(isotopeChart.mz)}`"
          :ref="(el) => (plots[index] = el)"
          :data="isotopeChart.traces"
          :layout="clone(layout)"
          :config="{ displayModeBar: false }"
          hideTitle
        >
        </BaseChartPlotly>
        <div class="float">
          <Tag
            :value="`Peak ${scale.mode} intensity: ${num.peakIntensity.format(scale.mode == 'average' ? isotopeChart.match.sample_peak_intensity : isotopeChart.match.sample_peak_intensity * sampleLength)}`"
            :severity="
              isotopeChart.match.sample_peak_intensity < app.data.match.params.ui.peak_min_intensity
                ? 'warn'
                : 'info'
            "
          />
          <Tag
            :value="`m/z error (ppm): ${num.mzError.format(isotopeChart.match.match_mz_error)}`"
            :severity="
              Math.abs(isotopeChart.match.match_mz_error) > app.data.match.params.ui.mz_tolerance
                ? 'warn'
                : 'info'
            "
          />

          <Tag
            :value="`Abundance error: ${num.relativeAbundanceError.format(isotopeChart.match.match_abundance_error)}`"
            :severity="
              Math.abs(isotopeChart.match.match_abundance_error) >
              app.data.match.params.ui.isotope_ratio_tolerance
                ? 'warn'
                : 'info'
            "
          />
        </div>
      </figure>
    </div>
  </div>
</template>

<style scoped>
.spectra-container {
  flex: 1;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  overflow-x: auto;
  overflow-y: hidden;
}

.spectra-scroll {
  display: inline-flex;
  flex-flow: row nowrap;
  gap: 0;
  align-items: flex-start;
  justify-content: flex-start;
  min-width: 100%;
  height: 100%;
  padding: 0;
  margin: 0;
}

.spectra-figure {
  flex-shrink: 1;
  flex-grow: 1;
  min-width: 200px;
  height: 100%;
  min-height: 0;
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.spectra-figure h3 {
  flex-shrink: 0;
  margin: 0;
  padding: 0.25rem 0;
}

.spectra-figure :deep(.chart-wrapper) {
  flex: 1;
  min-height: 0;
}

#chart-spectrum-controls :global(fieldset) {
  margin: 0 !important;
}

@layer override {
  .float {
    flex-flow: column nowrap;
    gap: 0.5rem;
    align-items: flex-end;
    position: absolute;
    top: 5rem;
    right: 1rem;
    display: none;
    :deep(*) {
      font-size: smaller;
    }
  }

  figure:hover .float,
  .sidebarOpen .float {
    display: flex;
  }
}
</style>
