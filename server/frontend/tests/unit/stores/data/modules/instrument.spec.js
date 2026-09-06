import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { computed, ref } from 'vue'

// An instrument's name no longer says whether it is an Orbitrap or a TOF -
// the reader records that when it converts the file, and the instrument list
// carries it. `typeOf` is the one place the app asks the question, so that
// what the upload dialog offers is what the server will accept.

const records = ref([])

vi.mock('@/api', () => ({
  api: {
    http: { get: vi.fn() },
    socket: { on: vi.fn(), off: vi.fn(), addSubscription: vi.fn(), removeSubscription: vi.fn() }
  }
}))

vi.mock('@/lib/store', () => ({
  useData: () => ({ list: computed(() => records.value) })
}))

let useInstrument

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  records.value = []
  ;({ useInstrument } = await import('@/stores/data/modules/instrument'))
})

describe('instrument store: typeOf', () => {
  it('takes the class the listed instrument recorded, whatever its name', () => {
    records.value = [{ instrument: 'Test', type: 'orbi' }]

    expect(useInstrument().typeOf('Test')).toBe('orbi')
  })

  it('prefers the recorded class over what the name looks like it says', () => {
    // "Rapid" contains "api", so the old name rule reads it as a TOF.
    records.value = [{ instrument: 'Rapid', type: 'orbi' }]

    expect(useInstrument().typeOf('Rapid')).toBe('orbi')
  })

  it('falls back to the name rule for an instrument not in the list', () => {
    expect(useInstrument().typeOf('KORBI2')).toBe('orbi')
    expect(useInstrument().typeOf('KLTOF1')).toBe('tof')
  })

  it('answers nothing for a name that is neither listed nor self-describing', () => {
    expect(useInstrument().typeOf('Test')).toBe(null)
    expect(useInstrument().typeOf(null)).toBe(null)
  })
})
