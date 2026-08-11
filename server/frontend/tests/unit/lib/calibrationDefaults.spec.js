import { describe, it, expect } from 'vitest'

import { applyInstrumentDefaults } from '@/lib/calibrationDefaults'

const TOF_SHAPED = { refine_window: 100, snr_threshold: 10, match_score_min: 0 }
const ORBI = { refine_window: 50, snr_threshold: 50, mz_error_tolerance: 5, match_score_min: 0 }

describe('applyInstrumentDefaults', () => {
  it('adopts fetched defaults for untouched fields', () => {
    const params = { ...TOF_SHAPED }

    const merged = applyInstrumentDefaults(params, TOF_SHAPED, ORBI)

    expect(merged.refine_window).toBe(50)
    expect(merged.snr_threshold).toBe(50)
  })

  it('keeps user-modified fields', () => {
    const params = { ...TOF_SHAPED, refine_window: 42 }

    const merged = applyInstrumentDefaults(params, TOF_SHAPED, ORBI)

    expect(merged.refine_window).toBe(42)
    expect(merged.snr_threshold).toBe(50)
  })

  it('adopts fields the baseline never had', () => {
    const merged = applyInstrumentDefaults({ ...TOF_SHAPED }, TOF_SHAPED, ORBI)

    expect(merged.mz_error_tolerance).toBe(5)
  })

  it('fills fields the user cleared', () => {
    const params = { ...TOF_SHAPED, snr_threshold: null }

    const merged = applyInstrumentDefaults(params, TOF_SHAPED, ORBI)

    expect(merged.snr_threshold).toBe(50)
  })

  it('is stable when fetched equals the baseline', () => {
    const params = { ...ORBI, refine_window: 42 }

    const merged = applyInstrumentDefaults(params, ORBI, ORBI)

    expect(merged).toEqual(params)
  })
})
