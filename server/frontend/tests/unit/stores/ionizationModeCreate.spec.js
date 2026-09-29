/**
 * The ionization mode store sends every field the form collected.
 *
 * `create()` used to name the fields it forwards one by one, so a field added to
 * the form and to the API was dropped here silently - the POST succeeded and the
 * mode was stored without it. That is how the instrument a mode belongs to
 * (#1463) went missing: the pane passed it, the request did not carry it, and
 * nothing reported anything. It forwards the whole payload now, as `update`
 * always did, and this is what says so.
 *
 * `useData` is stubbed out: it subscribes to the socket, which this has no use
 * for - only the request matters here.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const post = vi.fn(() => Promise.resolve({ data: {} }))

vi.mock('@/api', () => ({
  api: {
    http: {
      get: vi.fn(() => Promise.resolve({ data: [] })),
      post,
      patch: vi.fn(() => Promise.resolve({ data: {} })),
      delete: vi.fn(() => Promise.resolve({}))
    }
  }
}))

vi.mock('@/lib/store', () => ({ useData: () => ({}) }))

const { useIonizationMode } = await import('@/stores/data/modules/ionization/mode')

const FORM = {
  ionization_mode_name: 'Nitrate 15N',
  ionization_mode_token: 'NO3',
  ionization_mode_polarity: '-',
  ionization_mechanism_ids: ['mech-a'],
  calibration_collection_id: 'col-cal',
  diagnostic_collection_id: 'col-diag',
  instrument: 'ORBI-A'
}

describe('the ionization mode store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    post.mockClear()
  })

  it('sends every field the form collected, the instrument included', () => {
    useIonizationMode().create(FORM)

    expect(post).toHaveBeenCalledTimes(1)
    const [url, body] = post.mock.calls[0]
    expect(url).toContain('/ionization/modes')
    expect(body).toEqual(FORM)
  })

  it('sends the instrument as null when the form leaves it empty', () => {
    useIonizationMode().create({ ...FORM, instrument: null })

    expect(post.mock.calls[0][1].instrument).toBeNull()
  })
})
