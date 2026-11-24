import { describe, expect, it } from 'vitest'

import {
  ENTRY_ABSENT,
  ENTRY_GAP,
  ENTRY_MEMBER,
  batchGroupSets,
  groupFor,
  groupSections,
  groupsByMember,
  groupsOf,
  lensColumnLabel,
  lensEntries,
  lensTitle,
  localChannelGroup,
  memberLabel,
  sampleGroupSets,
  stepFormula,
  stepLabel
} from '@/lib/ledger/groups.js'

import {
  BATCH_GROUPS,
  BATCH_PEAKS,
  CHANNEL_GROUP,
  CO_VARIATION_GROUP,
  SERIES_GROUP,
  TWIN_GROUP,
  idOf,
  member
} from './groupBatch.js'

const byId = new Map(BATCH_PEAKS.map((row) => [row.batch_peak_id, row]))
const rowOf = (m) => byId.get(m.batch_peak_id) ?? null
const shape = (entries) => entries.map((entry) => [entry.entry, entry.label, entry.formula])

describe('groups: a series', () => {
  // An assigned root, a proposed step either side, an assigned step up, and
  // no batch peak two steps up.
  const entries = lensEntries(SERIES_GROUP, rowOf)

  it('orders the steps from the lightest and puts a gap where no batch peak stands', () => {
    expect(shape(entries)).toEqual([
      [ENTRY_MEMBER, '−1 CH2', 'C4H6O4'],
      [ENTRY_MEMBER, 'root', 'C5H8O4'],
      [ENTRY_MEMBER, '+1 CH2', 'C6H10O4'],
      [ENTRY_GAP, '+2 CH2', 'C7H12O4'],
      [ENTRY_MEMBER, '+3 CH2', 'C8H14O4']
    ])
    // Each member is the ledger's own row, its tier with it.
    expect(
      entries.filter((e) => e.row).map((e) => [e.row.batch_peak_id, e.row.consensus_tier])
    ).toEqual([
      [idOf('down'), 'candidate'],
      [idOf('root'), 'assigned'],
      [idOf('up'), 'assigned'],
      [idOf('three'), 'candidate']
    ])
  })

  it('names the group and counts the gap apart from the batch peaks', () => {
    expect(lensTitle(SERIES_GROUP, entries)).toBe(
      'CH2 series from C5H8O4, through [M+NO3]-, 4 batch peaks, one step with no peak'
    )
    expect(lensColumnLabel(SERIES_GROUP)).toBe('Step')
  })

  it("draws the root's step as a gap where the root is no batch peak", () => {
    const rootless = { ...SERIES_GROUP, members: SERIES_GROUP.members.filter((m) => m.step) }
    expect(shape(lensEntries(rootless, rowOf)).map(([entry, label]) => [entry, label])).toEqual([
      [ENTRY_MEMBER, '−1 CH2'],
      [ENTRY_GAP, 'root'],
      [ENTRY_MEMBER, '+1 CH2'],
      [ENTRY_GAP, '+2 CH2'],
      [ENTRY_MEMBER, '+3 CH2']
    ])
  })

  it('reaches back to the root where every batch peak stands above it', () => {
    const above = { ...SERIES_GROUP, members: [member('three', { step: 3 })] }
    expect(lensEntries(above, rowOf).map((e) => [e.entry, e.label])).toEqual([
      [ENTRY_GAP, 'root'],
      [ENTRY_GAP, '+1 CH2'],
      [ENTRY_GAP, '+2 CH2'],
      [ENTRY_MEMBER, '+3 CH2']
    ])
  })

  it('lists a member the ledger has no row for as absent, apart from a gap', () => {
    // A sample that holds the root and the step up alone.
    const held = new Set([idOf('root'), idOf('up')])
    const sample = lensEntries(SERIES_GROUP, (m) => (held.has(m.batch_peak_id) ? rowOf(m) : null))
    expect(sample.map((e) => e.entry)).toEqual([
      ENTRY_ABSENT,
      ENTRY_MEMBER,
      ENTRY_MEMBER,
      ENTRY_GAP,
      ENTRY_ABSENT
    ])
    expect(lensTitle(SERIES_GROUP, sample, { scope: 'sample' })).toBe(
      'CH2 series from C5H8O4, through [M+NO3]-, 4 batch peaks, one step with no peak, ' +
        '2 not in this sample'
    )
  })

  it('writes a step formula, and none below zero', () => {
    expect(stepFormula('C5H8O4', 'CH2', 2)).toBe('C7H12O4')
    expect(stepFormula('C5H8O4', 'CH2', -1)).toBe('C4H6O4')
    expect(stepFormula('CH4', 'CH2', -3)).toBeNull()
    expect(stepFormula('C5H8O4', 'O', 1)).toBe('C5H8O5')
    expect(stepLabel(-2, 'O')).toBe('−2 O')
  })
})

describe('groups: two channels of one neutral in the batch scope', () => {
  const entries = lensEntries(CHANNEL_GROUP, rowOf)

  it("lists the channels in the server's order, each labelled by its ion", () => {
    expect(entries.map((e) => [e.row.batch_peak_id, e.label])).toEqual([
      [idOf('up_other'), '[M-H]-'],
      [idOf('up'), '[M+NO3]-']
    ])
    expect(lensTitle(CHANNEL_GROUP, entries)).toBe('C6H10O4 through two channels, 2 batch peaks')
    expect(lensColumnLabel(CHANNEL_GROUP)).toBe('Channel')
  })

  it('counts the channels a row is seen through', () => {
    expect(groupFor(BATCH_GROUPS, idOf('up'), 'channels')?.members).toHaveLength(2)
    // An isotope line is no channel of its family.
    expect(groupFor(BATCH_GROUPS, idOf('up_line'), 'channels')).toBeNull()
  })
})

describe('groups: a co-variation pair', () => {
  it('lists the record and its partner', () => {
    const entries = lensEntries(CO_VARIATION_GROUP, rowOf)
    expect(entries.map((e) => [e.row.batch_peak_id, e.label])).toEqual([
      [idOf('up_other'), '[M-H]-'],
      [idOf('up'), '[M+NO3]-']
    ])
    expect(lensTitle(CO_VARIATION_GROUP, entries)).toBe('Co-variation of C6H10O4, 2 batch peaks')
  })
})

describe('groups: a 14N twin', () => {
  it('names the twin by what the check calls it', () => {
    const entries = lensEntries(TWIN_GROUP, rowOf)
    expect(entries.map((e) => [e.row.batch_peak_id, e.label])).toEqual([
      [idOf('root'), '[M+NO3]-'],
      [idOf('twin'), '14N twin']
    ])
    expect(lensTitle(TWIN_GROUP, entries)).toBe('C5H8O4 and its 14N twin, 2 batch peaks')
  })

  it('opens the check pair the badge is about from the row of either side', () => {
    expect(groupFor(BATCH_GROUPS, idOf('twin'), 'check_pair')).toBe(TWIN_GROUP)
    // The root states two checks; its badge opens the first.
    expect(groupsOf(BATCH_GROUPS, idOf('root'), 'check_pair')).toHaveLength(2)
  })
})

describe('groups: a row in two series', () => {
  // The step up also stands on an O lattice from C6H10O3.
  const OXYGEN = {
    group_id: `series:bp-o:O:mech`,
    kind: 'series',
    unit: 'O',
    ionization_mechanism_id: 'mech',
    root_batch_peak_id: 'bp-o',
    root_formula: 'C6H10O3',
    members: [member('up', { step: 1 })]
  }
  const groups = [SERIES_GROUP, OXYGEN, CHANNEL_GROUP]

  it('is a member of both, and its badge opens the series its own record names', () => {
    expect(groupsOf(groups, idOf('up'), 'series').map((g) => g.unit)).toEqual(['CH2', 'O'])
    expect(groupFor(groups, idOf('up'), 'series', { unit: 'O' })).toBe(OXYGEN)
    expect(groupFor(groups, idOf('up'), 'series', { unit: 'CH2' })).toBe(SERIES_GROUP)
    expect(groupsByMember(groups).get(idOf('up'))).toEqual([SERIES_GROUP, OXYGEN, CHANNEL_GROUP])
  })

  it('is listed once by Group by series, and named again under the other', () => {
    const rows = BATCH_PEAKS.filter((row) => !row.isotopologue_of)
    const sections = groupSections(
      rows,
      batchGroupSets(groups, 'series'),
      (row) => row.batch_peak_id
    )
    expect(
      sections.map((s) => [s.name, s.rows.map((r) => r.batch_peak_id), s.references.length])
    ).toEqual([
      [
        'CH2 series from C5H8O4, through [M+NO3]-',
        [idOf('down'), idOf('root'), idOf('up'), idOf('three')],
        0
      ],
      ['O series from C6H10O3, through [M+NO3]-', [], 1],
      ['Ungrouped', [idOf('up_other'), idOf('two_other'), idOf('twin')], 0]
    ])
    expect(sections[1].references[0].under).toBe('CH2 series from C5H8O4, through [M+NO3]-')
  })
})

describe('groups: Group by', () => {
  it('by compound draws the channel groups and leaves the rest ungrouped', () => {
    const rows = BATCH_PEAKS.filter((row) => !row.isotopologue_of)
    const sections = groupSections(
      rows,
      batchGroupSets(BATCH_GROUPS, 'compound'),
      (r) => r.batch_peak_id
    )
    expect(sections.map((s) => [s.name, s.size, s.rows.length])).toEqual([
      ['C6H10O4', 2, 2],
      ['Ungrouped', 5, 5]
    ])
  })

  it('orders the sections by where their first row lands in the sort', () => {
    const rows = [...BATCH_PEAKS].filter((r) => !r.isotopologue_of).sort((a, b) => b.mz - a.mz)
    const sections = groupSections(
      rows,
      batchGroupSets(BATCH_GROUPS, 'series'),
      (r) => r.batch_peak_id
    )
    expect(sections[0].rows.map((r) => r.mz)).toEqual([236, 208, 194, 180])
  })

  it("puts first the section whose row the sort puts first, whatever the read's order", () => {
    const sets = [
      { key: 'a', name: 'A', ids: ['bp-root', 'bp-down'] },
      { key: 'b', name: 'B', ids: ['bp-three', 'bp-up'] }
    ]
    const rows = [...BATCH_PEAKS].filter((r) => !r.isotopologue_of).sort((a, b) => b.mz - a.mz)
    expect(groupSections(rows, sets, (r) => r.batch_peak_id).map((s) => s.name)).toEqual([
      'B',
      'A',
      'Ungrouped'
    ])
  })

  it('draws no section the filters leave empty, and the rows they keep Ungrouped', () => {
    // The filters keep only the twin, which is in no series: no series section
    // is drawn, and the twin is not dropped with them.
    const rows = BATCH_PEAKS.filter((row) => row.batch_peak_id === idOf('twin'))
    const sections = groupSections(
      rows,
      batchGroupSets(BATCH_GROUPS, 'series'),
      (r) => r.batch_peak_id
    )
    expect(sections.map((s) => [s.name, s.rows.map((r) => r.batch_peak_id)])).toEqual([
      ['Ungrouped', [idOf('twin')]]
    ])
    expect(sections[0].ungrouped).toBe(true)
  })

  it('draws nothing where the filters keep no row', () => {
    expect(
      groupSections([], batchGroupSets(BATCH_GROUPS, 'series'), (r) => r.batch_peak_id)
    ).toEqual([])
  })

  it("by compound in the sample scope groups the sample's own rows by neutral", () => {
    const rows = [
      { sample_peak_id: 'p1', assigned_formula: 'C6H10O4' },
      { sample_peak_id: 'p2', assigned_formula: 'C6H10O4' },
      { sample_peak_id: 'p3', assigned_formula: 'C5H8O4' }
    ]
    const sets = sampleGroupSets(
      rows,
      (r) => r.assigned_formula,
      (r) => r.sample_peak_id
    )
    expect(sets).toEqual([{ key: 'compound:C6H10O4', name: 'C6H10O4', ids: ['p1', 'p2'] }])
  })
})

describe("groups: a sample's own channels", () => {
  it('stand in for the batch where it serves none', () => {
    const rows = [
      { sample_peak_id: 'p2', sample_peak_mz: 208, assigned_formula: 'C6H10O4', tier: 'assigned' },
      { sample_peak_id: 'p1', sample_peak_mz: 145, assigned_formula: 'C6H10O4', tier: 'candidate' }
    ]
    const group = localChannelGroup(rows, {
      mechanismOf: (r) => (r.sample_peak_id === 'p1' ? '[M-H]-' : '[M+NO3]-')
    })
    expect(group.local).toBe(true)
    expect(group.members.map((m) => [m.sample_peak_id, m.mechanism])).toEqual([
      ['p1', '[M-H]-'],
      ['p2', '[M+NO3]-']
    ])
    const entries = lensEntries(group, (m) =>
      rows.find((r) => r.sample_peak_id === m.sample_peak_id)
    )
    expect(lensTitle(group, entries, { scope: 'sample' })).toBe(
      'C6H10O4 through two channels, 2 rows of this sample'
    )
    expect(localChannelGroup(rows.slice(0, 1))).toBeNull()
  })

  it('labels a member by its formula where it names no channel', () => {
    expect(memberLabel({ formula: 'C3H4O4' }, CHANNEL_GROUP)).toBe('C3H4O4')
  })
})
