import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { computed, ref } from 'vue'

import { useSelection } from '@/lib/store/selection'

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

// The options the store asks useData for are captured, so that the selection
// they describe can be driven for real below.
const dataOptions = vi.hoisted(() => ({ value: null }))
vi.mock('@/lib/store', () => ({
  useData: (name, fetcher, options) => {
    dataOptions.value = options
    return { list: computed(() => records.value) }
  }
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

// Raw files lists every instrument at once when none is focused, so the
// instrument selection has to be allowed to be empty and to stay empty. The
// mode it asks for is what decides that, and nothing above would notice it
// changing - so drive the real selection with the very options the store
// passes, rather than asserting the mode's name.
describe('instrument store: no instrument is a state of its own', () => {
  const selectionFor = () => {
    useInstrument()
    return useSelection(
      'instrument-under-test',
      'instrument',
      () => records.value,
      dataOptions.value.selection
    )
  }

  beforeEach(() => {
    records.value = [{ instrument: 'Orbi-1' }, { instrument: 'Tof-2' }]
  })

  it('leaves the selection empty when a reload refocuses', () => {
    const selection = selectionFor()
    selection.unfocus()

    // What the data loader calls after every load; 'single' reassigns the
    // first record here, which would put an instrument back under the user.
    selection.prepRefocus()()

    expect(selection.focused.value).toBe(null)
  })

  it('still holds one instrument at a time', () => {
    const selection = selectionFor()

    selection.focus({ instrument: 'Orbi-1' })
    selection.focus({ instrument: 'Tof-2' })

    expect(selection.singleselect).toBe(true)
    expect(selection.focused.value).toEqual({ instrument: 'Tof-2' })
  })

  it('keeps a focused instrument across a reload', () => {
    const selection = selectionFor()
    selection.focus({ instrument: 'Tof-2' })

    selection.prepRefocus()()

    expect(selection.focused.value).toEqual({ instrument: 'Tof-2' })
  })
})
