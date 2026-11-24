import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
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
// The visualization restore is deliberately NOT gated on the feature flag - the
// Match tab coexists with assignment. A getter keeps the flag settable per test
// so the restore can be exercised with it on as well as off.
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

  it('restores the visualization with peak-centric assignment on too', () => {
    // The two paradigms coexist, so the Match tab is rendered either way and a
    // shared link carrying a visualized ion opens it either way. Gating this on
    // the flag once made every such link degrade to a bare chain focus on an
    // assignment deployment, which is what this pins against.
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

      expect(data.match.visualized.set).toHaveBeenCalledWith({
        sampleId: 's1',
        collectionId: 'c1',
        ionId: 'i2',
        isotopeId: 'iso1'
      })
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

// The restore holds the tab store's automatic switches off until the link's
// chain settles, then ends the hold. Inside that window only the user can move
// the tab (#3456): a pick of theirs must survive the settle, and an untouched
// tab must still be the link's tab when the hold ends.
describe('useLocation.apply: tab restore while the chain settles', () => {
  const POLL_MS = 700
  const SETTLE_TIMEOUT_MS = 20000

  // A tab store that behaves like the real one where it matters here: hydrate
  // sets the tab and starts the hold, endHydrate ends it.
  const makeTab = () => {
    const tab = { active: 'raw files', hydrating: false }
    tab.hydrate = vi.fn((value) => {
      tab.hydrating = true
      tab.active = value
    })
    tab.endHydrate = vi.fn(() => {
      tab.hydrating = false
    })
    return tab
  }

  // A shared link that names a workspace, a sample, a collection and a tab.
  const LINK = { workspace: 'w1', samples: ['s1'], collection: 'c1', tab: 'sample' }

  const resolveChain = (data) => {
    data.workspace.focusedId = 'w1'
    data.sample.selectedIds = ['s1']
    data.match.collection.focusedId = 'c1'
  }

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
    app.current = makeApp()
    app.current.ui.tab = makeTab()
  })
  afterEach(() => vi.useRealTimers())

  it.each([
    ['opens a match', 'match'],
    ['clicks another tab', 'batch']
  ])('keeps the tab when the user %s before the chain resolves', (_, picked) => {
    const { data, ui } = app.current
    useLocation().apply(LINK)
    expect(ui.tab.active).toBe('sample')
    expect(ui.tab.hydrating).toBe(true)

    // One poll with the chain unresolved: the hold stays on.
    vi.advanceTimersByTime(POLL_MS)
    expect(ui.tab.hydrating).toBe(true)

    ui.tab.active = picked
    resolveChain(data)
    vi.advanceTimersByTime(POLL_MS)

    expect(ui.tab.active).toBe(picked)
    expect(ui.tab.endHydrate).toHaveBeenCalledTimes(1)
    expect(ui.tab.hydrating).toBe(false)
  })

  it("ends the hold on the link's tab when nobody touched it", () => {
    const { data, ui } = app.current
    useLocation().apply(LINK)
    vi.advanceTimersByTime(POLL_MS * 3)
    expect(ui.tab.hydrating).toBe(true)

    resolveChain(data)
    vi.advanceTimersByTime(POLL_MS)

    expect(ui.tab.active).toBe('sample')
    expect(ui.tab.endHydrate).toHaveBeenCalledTimes(1)
    expect(ui.tab.hydrating).toBe(false)
  })

  it('ends the hold at the timeout on a chain that never resolves, setting nothing', () => {
    const { ui } = app.current
    useLocation().apply(LINK)
    // A tab the user picks late in the window, just before the timeout.
    vi.advanceTimersByTime(SETTLE_TIMEOUT_MS - POLL_MS)
    expect(ui.tab.hydrating).toBe(true)
    ui.tab.active = 'batch'

    vi.advanceTimersByTime(POLL_MS * 2)

    expect(ui.tab.endHydrate).toHaveBeenCalledTimes(1)
    expect(ui.tab.hydrating).toBe(false)
    expect(ui.tab.active).toBe('batch')
    // The poll has stopped: nothing runs later.
    vi.advanceTimersByTime(SETTLE_TIMEOUT_MS)
    expect(ui.tab.endHydrate).toHaveBeenCalledTimes(1)
    expect(ui.tab.active).toBe('batch')
  })
})

// The share button copies a link to the current view. navigator.clipboard is
// missing on a page served over plain HTTP from a network address, so the copy
// has to fall back to the copy command and, when that fails too, hand the user
// the link to copy by hand - the address bar no longer shows it.
describe('useLocation.copyShareLink', () => {
  const originalExecCommand = document.execCommand

  beforeEach(() => {
    setActivePinia(createPinia())
    app.current = makeApp()
    app.current.ui.notification = { push: vi.fn() }
    app.current.data.batch.focusedId = 'b1'
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    document.execCommand = originalExecCommand
  })

  it('copies through the copy command where there is no Clipboard API', async () => {
    vi.stubGlobal('navigator', {})
    let copied
    document.execCommand = vi.fn(() => {
      copied = document.querySelector('textarea')?.value
      return true
    })

    const url = await useLocation().copyShareLink()

    expect(url).toContain('b=b1')
    expect(copied).toBe(url)
    expect(app.current.ui.notification.push).toHaveBeenCalledTimes(1)
    expect(app.current.ui.notification.push).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'success' })
    )
  })

  it('says it failed, and shows the link, when nothing could copy it', async () => {
    vi.stubGlobal('navigator', {})
    document.execCommand = vi.fn(() => false)

    const url = await useLocation().copyShareLink()

    expect(app.current.ui.notification.push).toHaveBeenCalledTimes(1)
    const [notification] = app.current.ui.notification.push.mock.calls[0]
    expect(notification.status).toBe('warning')
    expect(notification.message).toContain(url)
  })
})
