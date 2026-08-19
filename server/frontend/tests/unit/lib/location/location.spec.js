import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// A mutable holder so the mocked useApp() returns whatever the test sets up.
const { app, flags } = vi.hoisted(() => ({
  app: { current: null },
  flags: { peakAssignment: false }
}))
vi.mock('@/stores', () => ({ useApp: () => app.current }))
// The store registers a shared-link import hook on login; stub auth so the
// real auth store (and its api/runtime imports) stay out of this unit test.
vi.mock('@/stores/auth', () => ({ useAuth: () => ({ onLogin: vi.fn() }) }))
// The feature flag gates the visualization restore (the Match tab is retired
// with peak-centric assignment on); a getter keeps it settable per test.
vi.mock('@/lib/features', () => ({
  get peakAssignmentEnabled() {
    return flags.peakAssignment
  }
}))

import { useLocation } from '@/lib/location'

const makeStore = (overrides = {}) => ({
  focusedId: null,
  selectedIds: [],
  list: [],
  focus: vi.fn(),
  unfocus: vi.fn(),
  select: vi.fn(),
  lazyFocus: vi.fn(),
  lazySelect: vi.fn(),
  ...overrides
})

const makeApp = () => ({
  data: {
    workspace: makeStore(),
    dataset: makeStore(),
    batch: makeStore(),
    sample: makeStore(),
    peak: makeStore(),
    match: {
      collection: makeStore(),
      ion: makeStore(),
      visualized: { ion: null, isotopeSelected: null, set: vi.fn() }
    }
  },
  ui: { tab: { active: 'raw files', hydrate: vi.fn(), endHydrate: vi.fn() } }
})

describe('useLocation.read', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('derives a Location from the live stores', () => {
    app.current = makeApp()
    Object.assign(app.current.data.workspace, { focusedId: 'w1' })
    Object.assign(app.current.data.dataset, { focusedId: 'd1' })
    Object.assign(app.current.data.batch, { focusedId: 'b1' })
    Object.assign(app.current.data.sample, { selectedIds: ['s1', 's2'] })
    Object.assign(app.current.data.match.collection, { focusedId: 'c1' })
    Object.assign(app.current.data.match.ion, { selectedIds: ['i1', 'i2'] })
    app.current.data.match.visualized.ion = { target_ion_id: 'i2' }
    app.current.data.match.visualized.isotopeSelected = { target_isotope_id: 'iso1' }
    app.current.ui.tab.active = 'match'

    const loc = useLocation().read()

    expect(loc).toMatchObject({
      workspace: 'w1',
      dataset: 'd1',
      batch: 'b1',
      samples: ['s1', 's2'],
      collection: 'c1',
      ions: ['i1', 'i2'],
      visualizedIon: 'i2', // the visualized ion, distinct from the table selection
      isotope: 'iso1',
      tab: 'match'
    })
  })
})

describe('useLocation.apply', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('applies a resident level immediately and queues absent ones', () => {
    app.current = makeApp()
    const { data } = app.current
    // Only the workspace has loaded its records at apply time.
    data.workspace.list = [{ workspace_id: 'w1' }]

    useLocation().apply({ workspace: 'w1', dataset: 'd1', batch: 'b1' })

    // resident -> immediate focus
    expect(data.workspace.focus).toHaveBeenCalledWith({ workspace_id: 'w1' })
    expect(data.workspace.lazyFocus).not.toHaveBeenCalled()
    // not yet loaded -> queued
    expect(data.dataset.lazyFocus).toHaveBeenCalledWith({ dataset_id: 'd1' })
    expect(data.batch.lazyFocus).toHaveBeenCalledWith({ sample_batch_id: 'b1' })
  })

  it('queues a multi-select level via lazySelect when not resident', () => {
    app.current = makeApp()
    const { data } = app.current

    useLocation().apply({ samples: ['s1', 's2'] })

    expect(data.sample.lazySelect).toHaveBeenCalledWith(['s1', 's2'])
    expect(data.sample.select).not.toHaveBeenCalled()
  })

  it('selects a resident multi-select level immediately', () => {
    app.current = makeApp()
    const { data } = app.current
    data.sample.list = [{ sample_item_id: 's1' }, { sample_item_id: 's2' }]

    useLocation().apply({ samples: ['s1', 's2'] })

    expect(data.sample.unfocus).toHaveBeenCalled()
    expect(data.sample.select).toHaveBeenCalledWith(
      { sample_item_id: 's1' },
      { sample_item_id: 's2' }
    )
    expect(data.sample.lazySelect).not.toHaveBeenCalled()
  })

  it('clears a level whose target is empty', () => {
    app.current = makeApp()
    const { data } = app.current
    data.workspace.list = [{ workspace_id: 'w1' }]

    useLocation().apply({ workspace: 'w1' }) // no dataset/batch/...

    expect(data.dataset.unfocus).toHaveBeenCalled()
    expect(data.batch.unfocus).toHaveBeenCalled()
  })

  it('restores the visualization for the visualized ion once its context is ready', () => {
    app.current = makeApp()
    const { data } = app.current
    // Pretend the whole context is already resident and focused.
    data.sample.focusedId = 's1'
    data.match.collection.focusedId = 'c1'
    data.match.ion.list = [{ target_ion_id: 'i2' }]

    useLocation().apply({
      samples: ['s1'],
      collection: 'c1',
      ions: ['i1', 'i2'],
      visualizedIon: 'i2',
      isotope: 'iso1'
    })

    expect(data.match.visualized.set).toHaveBeenCalledWith({
      sampleId: 's1',
      collectionId: 'c1',
      ionId: 'i2',
      isotopeId: 'iso1'
    })
  })

  it('skips the visualization restore when peak-centric assignment is on', () => {
    // The Match tab is retired with the flag on (#1736): a shared link minted
    // on a flag-off deployment degrades gracefully to the chain focus.
    flags.peakAssignment = true
    try {
      app.current = makeApp()
      const { data } = app.current
      data.sample.focusedId = 's1'
      data.match.collection.focusedId = 'c1'
      data.match.ion.list = [{ target_ion_id: 'i2' }]

      useLocation().apply({
        samples: ['s1'],
        collection: 'c1',
        ions: ['i1', 'i2'],
        visualizedIon: 'i2',
        isotope: 'iso1'
      })

      expect(data.match.visualized.set).not.toHaveBeenCalled()
    } finally {
      flags.peakAssignment = false
    }
  })

  it('does not open a visualization when ions are only selected, not visualized', () => {
    app.current = makeApp()
    const { data } = app.current
    data.sample.focusedId = 's1'
    data.match.collection.focusedId = 'c1'
    data.match.ion.list = [{ target_ion_id: 'i1' }, { target_ion_id: 'i2' }]

    // Ions selected in the table, but no visualization was open.
    useLocation().apply({ samples: ['s1'], collection: 'c1', ions: ['i1', 'i2'] })

    expect(data.match.visualized.set).not.toHaveBeenCalled()
  })

  it('hydrates a non-match tab and leaves match to the visualization', () => {
    app.current = makeApp()
    useLocation().apply({ tab: 'batch' })
    expect(app.current.ui.tab.hydrate).toHaveBeenCalledWith('batch')

    app.current = makeApp()
    useLocation().apply({ tab: 'match' })
    expect(app.current.ui.tab.hydrate).not.toHaveBeenCalled()
  })
})
