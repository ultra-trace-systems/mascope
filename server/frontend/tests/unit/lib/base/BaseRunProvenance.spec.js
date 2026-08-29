import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'

import BaseRunProvenance from '@/lib/base/BaseRunProvenance.vue'

// The badge that makes an imported assignment run distinguishable from one this
// deployment computed. It is load-bearing rather than decorative: the ledger
// defaults to the newest completed run whatever engine produced it, and an
// imported run bypasses the m/z verification gate an in-app run must pass. If
// this renders the same for both, neither of those is visible to a reader.

// PrimeVue's Tag stands in as a plain span carrying its value and severity, so
// assertions read the text and the severity the component chose.
const TagStub = {
  props: ['value', 'severity', 'icon'],
  template: '<span class="tag" :data-severity="severity" :data-icon="icon">{{ value }}</span>'
}

const mountBadge = (run, props = {}) =>
  mount(BaseRunProvenance, {
    props: { run, ...props },
    global: {
      stubs: { Tag: TagStub },
      directives: { tooltip: {} }
    }
  })

const IN_APP_RUN = {
  peak_assignment_run_id: 'run-1',
  engine: 'mascope',
  engine_version: '0.2.0',
  status: 'completed',
  tier_bands: { assigned: 0.8, candidate: 0.5 },
  calibration: null
}

const IMPORTED_RUN = {
  peak_assignment_run_id: 'run-2',
  engine: 'peaky',
  engine_version: '0.6.0',
  status: 'completed',
  tier_bands: { assigned: 0.6, candidate: 0.3 },
  calibration: { method: 'offset-aware', mascope_mz_verified: false }
}

describe('BaseRunProvenance', () => {
  it('renders nothing without a run', () => {
    expect(mountBadge(null).find('.tag').exists()).toBe(false)
  })

  it('names the in-app engine and shows no calibration chip', () => {
    const wrapper = mountBadge(IN_APP_RUN)
    const tags = wrapper.findAll('.tag')

    // One chip only: an in-app run's calibration state is the sample's own, and
    // the engine already refuses a sample whose m/z calibration is unverified.
    expect(tags).toHaveLength(1)
    expect(tags[0].text()).toBe('Mascope 0.2.0')
    expect(tags[0].attributes('data-severity')).toBe('secondary')
  })

  it('names the external engine and shows its calibration disclosure', () => {
    const wrapper = mountBadge(IMPORTED_RUN)
    const tags = wrapper.findAll('.tag')

    expect(tags).toHaveLength(2)
    expect(tags[0].text()).toBe('peaky 0.6.0')
    // Visually distinct from in-app: the whole point is that a reader can tell.
    expect(tags[0].attributes('data-severity')).toBe('info')
    expect(tags[1].text()).toBe('calibration · offset-aware')
  })

  it('reads the reserved in-app name case-insensitively, as the server reserves it', () => {
    // The server rejects 'Mascope' from an import for exactly this reason, so a
    // stored value in any casing is the in-app engine, never a foreign one.
    const wrapper = mountBadge({ ...IN_APP_RUN, engine: 'Mascope' })

    expect(wrapper.findAll('.tag')).toHaveLength(1)
    expect(wrapper.text()).toContain('Mascope')
  })

  it('treats a run with no engine as in-app, not as foreign', () => {
    // Rows predating the engine column were backfilled to the in-app identity;
    // a missing value means an old row, so badging it as external would be a lie.
    const wrapper = mountBadge({ ...IN_APP_RUN, engine: null })

    expect(wrapper.findAll('.tag')).toHaveLength(1)
    expect(wrapper.text()).toContain('Mascope')
  })

  it('warns when an imported run disclosed no calibration', () => {
    const wrapper = mountBadge({ ...IMPORTED_RUN, calibration: null })
    const tags = wrapper.findAll('.tag')

    expect(tags[1].text()).toBe('no calibration')
    expect(tags[1].attributes('data-severity')).toBe('warn')
  })

  it('says only that a disclosure exists when it names no method', () => {
    // `calibration` is stored verbatim and the server never reads into it, so
    // nothing guarantees a `method` key. Claiming one would be a wrong label.
    const wrapper = mountBadge({
      ...IMPORTED_RUN,
      calibration: { drift_ppm: 1.4, reference: 'internal standards' }
    })

    expect(wrapper.findAll('.tag')[1].text()).toBe('calibration disclosed')
  })

  it('ignores a calibration that is not an object', () => {
    const wrapper = mountBadge({ ...IMPORTED_RUN, calibration: 'yes' })

    expect(wrapper.findAll('.tag')[1].text()).toBe('no calibration')
  })

  it('treats an empty disclosure as no disclosure', () => {
    // The server accepts {} - it only refuses null - but it says nothing about
    // what was calibrated, so badging it "disclosed" would reassure falsely.
    const wrapper = mountBadge({ ...IMPORTED_RUN, calibration: {} })
    const chip = wrapper.findAll('.tag')[1]

    expect(chip.text()).toBe('no calibration')
    expect(chip.attributes('data-severity')).toBe('warn')
  })

  it('drops the version in compact mode, keeping the engine and calibration', () => {
    const wrapper = mountBadge(IMPORTED_RUN, { compact: true })
    const tags = wrapper.findAll('.tag')

    expect(tags[0].text()).toBe('peaky')
    expect(tags[1].text()).toBe('calibration')
  })

  it('puts the run tier bands on the engine tooltip, as percentages', () => {
    // A tier is only comparable across engines under the thresholds that
    // produced it, so the bands travel with the engine name.
    const wrapper = mountBadge(IMPORTED_RUN)

    expect(wrapper.vm.tierBandsText).toBe('Tier bands: assigned ≥ 60% · candidate ≥ 30%')
  })

  it('omits the bands rather than inventing them when the run carries none', () => {
    const wrapper = mountBadge({ ...IMPORTED_RUN, tier_bands: null })

    expect(wrapper.vm.tierBandsText).toBeNull()
  })

  it('says an import writes no Mascope P(correct), only for imported runs', () => {
    expect(mountBadge(IMPORTED_RUN).vm.engineTooltip).toContain('no Mascope-calibrated P(correct)')
    expect(mountBadge(IN_APP_RUN).vm.engineTooltip).not.toContain('P(correct)')
  })

  it('survives a calibration blob it cannot serialize', () => {
    const circular = { method: 'loopy' }
    circular.self = circular

    const wrapper = mountBadge({ ...IMPORTED_RUN, calibration: circular })

    // The chip still names the method; the dump degrades rather than throwing
    // or rendering "[object Object]" as though it were the disclosure.
    expect(wrapper.findAll('.tag')[1].text()).toBe('calibration · loopy')
    expect(wrapper.vm.calibrationDump).toBe('(could not be displayed)')
  })

  it('bounds a very large disclosure', () => {
    const wrapper = mountBadge({
      ...IMPORTED_RUN,
      calibration: { method: 'big', blob: 'x'.repeat(5000) }
    })

    expect(wrapper.vm.calibrationDump.length).toBeLessThanOrEqual(601)
    expect(wrapper.vm.calibrationDump.endsWith('…')).toBe(true)
  })
})
