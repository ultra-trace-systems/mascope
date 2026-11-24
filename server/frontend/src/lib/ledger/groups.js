// The rows a reading spans (docs/dev/assignment_ledger_and_card.md, section
// 3.5): a homologous series, the channels of one neutral, a co-variation
// record's partners, a batch check's pair.
//
// A group is the server's (`batch_groups`, served on the batch-peaks read and
// on a sample's detail row by one derivation, `batch_series.batch_groups`), so
// the ledger, its lens and the card count the same members. This module only
// orders the members, labels them and adds a series' gaps: a step the series
// spans with no batch peak at it. It is pure; the panes hand it their rows.
//
// The isotopologue family is no group here: it stays the ledger's fold.

import { neutralKey } from '@/lib/chem'

export const GROUP_SERIES = 'series'
export const GROUP_CHANNELS = 'channels'
export const GROUP_CO_VARIATION = 'co_variation'
export const GROUP_CHECK_PAIR = 'check_pair'

/** A lens entry: a batch peak the ledger holds a row for. */
export const ENTRY_MEMBER = 'member'
/** A lens entry: a series step with no batch peak at it. */
export const ENTRY_GAP = 'gap'
/** A lens entry: a member the ledger holds no row for (the sample lacks it). */
export const ENTRY_ABSENT = 'absent'

/** What a batch check calls the batch peak it names beside its own. */
const CHECK_PARTNERS = Object.freeze({
  halogen_lock: 'halogen line',
  label_twin: '14N twin',
  neutral_pair: 'urea pair'
})

const MINUS = '−'
const signedStep = (step) => (step < 0 ? `${MINUS}${-step}` : `+${step}`)

// --- Formulas -------------------------------------------------------------------

/**
 * A neutral formula's element counts, or null where it does not read as one.
 * A labelled atom (`[15N]`) counts as its own symbol.
 *
 * @param {string|null|undefined} formula
 * @returns {Map<string, number>|null}
 */
export function formulaCounts(formula) {
  const text = typeof formula === 'string' ? formula.trim() : ''
  if (!text) return null
  const token = /^(\[\d+[A-Z][a-z]?\]|[A-Z][a-z]?)(\d*)/
  const counts = new Map()
  let rest = text
  while (rest) {
    const match = token.exec(rest)
    if (!match) return null
    const [whole, symbol, count] = match
    counts.set(symbol, (counts.get(symbol) ?? 0) + (count ? Number(count) : 1))
    rest = rest.slice(whole.length)
  }
  return counts
}

/**
 * A formula written from its counts in Hill order: C, then H, then the rest
 * alphabetically; or alphabetically throughout where there is no carbon.
 *
 * @param {Map<string, number>} counts
 * @returns {string}
 */
export function hillFormula(counts) {
  const symbols = [...counts.keys()].filter((symbol) => counts.get(symbol) > 0)
  const carbon = symbols.includes('C')
  const rank = (symbol) => (carbon ? (symbol === 'C' ? 0 : symbol === 'H' ? 1 : 2) : 2)
  symbols.sort((a, b) => rank(a) - rank(b) || (a < b ? -1 : a > b ? 1 : 0))
  return symbols
    .map((symbol) => `${symbol}${counts.get(symbol) === 1 ? '' : counts.get(symbol)}`)
    .join('')
}

/**
 * The formula a series step stands at: the root's plus `step` units. Null where
 * either formula does not read, or the step takes an element below zero.
 *
 * @param {string} rootFormula
 * @param {string} unit
 * @param {number} step
 * @returns {string|null}
 */
export function stepFormula(rootFormula, unit, step) {
  const root = formulaCounts(rootFormula)
  const per = formulaCounts(unit)
  if (!root || !per || !Number.isInteger(step)) return null
  const counts = new Map(root)
  for (const [symbol, n] of per) {
    const total = (counts.get(symbol) ?? 0) + n * step
    if (total < 0) return null
    counts.set(symbol, total)
  }
  return hillFormula(counts)
}

// --- Labels ---------------------------------------------------------------------

/**
 * A series step's label: `root`, `+1 CH2`, `−1 CH2`.
 *
 * @param {number} step - whole units from the root
 * @param {string} unit
 * @returns {string}
 */
export function stepLabel(step, unit) {
  if (step === 0) return 'root'
  return `${signedStep(step)} ${unit || 'unit'}`
}

/**
 * How a group's member is labelled in the lens's Step or Channel column and in
 * the card's member lists: a series member by its step from the root, a check
 * pair's partner by what the check calls it, any other by its channel, else
 * its formula.
 *
 * @param {object} member - one of a group's `members`
 * @param {object} group
 * @returns {string}
 */
export function memberLabel(member, group) {
  if (group?.kind === GROUP_SERIES && Number.isInteger(member?.step)) {
    return stepLabel(member.step, group.unit)
  }
  if (
    group?.kind === GROUP_CHECK_PAIR &&
    member?.batch_peak_id !== group.anchor_batch_peak_id &&
    CHECK_PARTNERS[group.check]
  ) {
    return CHECK_PARTNERS[group.check]
  }
  return member?.mechanism || member?.ion_formula || member?.formula || 'another row'
}

/** The header of the lens's label column: Step for a series, Channel otherwise. */
export const lensColumnLabel = (group) => (group?.kind === GROUP_SERIES ? 'Step' : 'Channel')

// --- Which groups a row is in ---------------------------------------------------

/**
 * The groups a batch peak is a member of, in the read's order.
 *
 * @param {Array<object>|null} groups - `batch_groups`
 * @param {string|null} batchPeakId
 * @param {string} [kind] - only groups of this kind
 * @returns {Array<object>}
 */
export function groupsOf(groups, batchPeakId, kind = null) {
  if (!Array.isArray(groups) || batchPeakId == null) return []
  return groups.filter(
    (group) =>
      (kind == null || group?.kind === kind) &&
      (group?.members ?? []).some((member) => member.batch_peak_id === batchPeakId)
  )
}

/**
 * The group a badge of a row opens: of the reading's kind, and for a series the
 * one the row's own series record names (its unit and mechanism) where the row
 * sits in several.
 *
 * @param {Array<object>|null} groups - `batch_groups`
 * @param {string} batchPeakId
 * @param {string} kind
 * @param {object|null} [record] - the row's series record, for a series
 * @returns {object|null}
 */
export function groupFor(groups, batchPeakId, kind, record = null) {
  const found = groupsOf(groups, batchPeakId, kind)
  if (kind !== GROUP_SERIES || found.length < 2 || !record?.unit) return found[0] ?? null
  return (
    found.find(
      (group) =>
        group.unit === record.unit &&
        (record.ionization_mechanism_id == null ||
          group.ionization_mechanism_id == null ||
          String(group.ionization_mechanism_id) === String(record.ionization_mechanism_id))
    ) ?? found[0]
  )
}

/** Each batch peak's groups, by id, for a ledger that asks of every row. */
export function groupsByMember(groups) {
  const index = new Map()
  for (const group of Array.isArray(groups) ? groups : []) {
    for (const member of group?.members ?? []) {
      const list = index.get(member.batch_peak_id)
      if (list) list.push(group)
      else index.set(member.batch_peak_id, [group])
    }
  }
  return index
}

// --- The lens -------------------------------------------------------------------

/**
 * A group's entries, in the group's order, each with its label: a series by
 * step, with a gap at each step between its lightest and heaviest member that
 * no batch peak stands at (the root's own step included, where the root is no
 * batch peak); any other in the order the server lists it.
 *
 * `rowOf` finds the ledger's row for a member. A member it finds none for is
 * an absent entry: in the sample scope, a member the sample holds no peak for.
 *
 * @param {object} group
 * @param {(member: object) => object|null} [rowOf]
 * @returns {Array<{entry: string, key: string, label: string, member: object|null,
 *   row: object|null, step: number|null, formula: string|null}>}
 */
export function lensEntries(group, rowOf = () => null) {
  if (!group) return []
  const memberEntry = (member) => {
    const row = rowOf(member) ?? null
    return {
      entry: row ? ENTRY_MEMBER : ENTRY_ABSENT,
      key: `${row ? ENTRY_MEMBER : ENTRY_ABSENT}:${member.batch_peak_id}`,
      label: memberLabel(member, group),
      member,
      row,
      step: Number.isInteger(member.step) ? member.step : null,
      formula: member.formula ?? null
    }
  }
  const members = group.members ?? []
  if (group.kind !== GROUP_SERIES) return members.map(memberEntry)

  const stepped = members.filter((member) => Number.isInteger(member.step))
  if (!stepped.length) return members.map(memberEntry)
  const byStep = new Map()
  for (const member of stepped) {
    const list = byStep.get(member.step)
    if (list) list.push(member)
    else byStep.set(member.step, [member])
  }
  const steps = [...byStep.keys(), 0]
  const low = Math.min(...steps)
  const high = Math.max(...steps)
  const entries = []
  for (let step = low; step <= high; step++) {
    const at = byStep.get(step)
    if (at) {
      for (const member of [...at].sort((a, b) => (a.mz ?? 0) - (b.mz ?? 0))) {
        entries.push(memberEntry(member))
      }
      continue
    }
    entries.push({
      entry: ENTRY_GAP,
      key: `${ENTRY_GAP}:${step}`,
      label: stepLabel(step, group.unit),
      member: null,
      row: null,
      step,
      formula: stepFormula(group.root_formula, group.unit, step)
    })
  }
  return entries
}

const NUMBER_WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine']
const counted = (n, one, many) => `${n < 10 ? NUMBER_WORDS[n] : n} ${n === 1 ? one : many}`

/** The mechanism a group reads through, where all its members share one. */
function sharedMechanism(group) {
  const names = new Set((group.members ?? []).map((member) => member.mechanism).filter(Boolean))
  return names.size === 1 ? [...names][0] : null
}

/** What a group is, in a few words: its name, without the counts. */
export function groupName(group) {
  if (!group) return ''
  const members = group.members ?? []
  const first = members[0]
  if (group.kind === GROUP_SERIES) {
    const through = sharedMechanism(group)
    return (
      `${group.unit || 'Unit'} series from ${group.root_formula || 'its root'}` +
      (through ? `, through ${through}` : '')
    )
  }
  if (group.kind === GROUP_CHANNELS) {
    const formula = group.formula || first?.formula || 'one neutral'
    return `${formula} through ${counted(members.length, 'channel', 'channels')}`
  }
  if (group.kind === GROUP_CO_VARIATION) {
    const anchor =
      members.find((member) => member.batch_peak_id === group.anchor_batch_peak_id) ?? first
    return `Co-variation of ${anchor?.formula || 'a batch peak'}`
  }
  if (group.kind === GROUP_CHECK_PAIR) {
    const anchor =
      members.find((member) => member.batch_peak_id === group.anchor_batch_peak_id) ?? first
    const partner = CHECK_PARTNERS[group.check] ?? 'pair'
    return `${anchor?.formula || 'A batch peak'} and its ${partner}`
  }
  return 'Group'
}

/**
 * The lens bar's line: the group's name, how many batch peaks it holds, and
 * apart from them the gaps and, in the sample scope, the members the sample
 * does not hold. "CH2 series from C8H13NO9, through [M+NO3]-, 4 batch peaks,
 * one step with no peak".
 *
 * @param {object} group
 * @param {Array<object>} entries - `lensEntries` of it
 * @param {{scope?: string}} [options]
 * @returns {string}
 */
export function lensTitle(group, entries, { scope = 'batch' } = {}) {
  if (!group) return ''
  const peaks = entries.filter((entry) => entry.entry !== ENTRY_GAP).length
  const gaps = entries.filter((entry) => entry.entry === ENTRY_GAP).length
  const absent = entries.filter((entry) => entry.entry === ENTRY_ABSENT).length
  const parts = [groupName(group)]
  // A sample's own rows, where its batch serves no group: rows, not batch peaks.
  parts.push(
    group.local
      ? `${peaks} row${peaks === 1 ? '' : 's'} of this sample`
      : `${peaks} batch peak${peaks === 1 ? '' : 's'}`
  )
  if (gaps) parts.push(counted(gaps, 'step with no peak', 'steps with no peak'))
  if (absent && scope === 'sample') parts.push(`${absent} not in this sample`)
  return parts.join(', ')
}

/**
 * A channel group of a sample's own rows, for a sample whose batch serves no
 * group of the row (its batch has no batch peaks yet): the rows that read the
 * same neutral, each a member as the server would list it, flagged `local`.
 *
 * @param {Array<object>} rows - the sample's rows of one neutral
 * @param {{mechanismOf?: (row: object) => string|null}} [options]
 * @returns {object|null}
 */
export function localChannelGroup(rows, { mechanismOf = () => null } = {}) {
  const list = (rows ?? []).filter((row) => row?.assigned_formula)
  if (list.length < 2) return null
  const sorted = [...list].sort((a, b) => (a.sample_peak_mz ?? 0) - (b.sample_peak_mz ?? 0))
  return {
    group_id: `local:${GROUP_CHANNELS}:${neutralKey(sorted[0].assigned_formula)}`,
    kind: GROUP_CHANNELS,
    formula: sorted[0].assigned_formula,
    local: true,
    members: sorted.map((row) => ({
      batch_peak_id: `local:${row.sample_peak_id}`,
      formula: row.assigned_formula,
      ion_formula: row.ion_formula ?? null,
      ionization_mechanism_id: row.ionization_mechanism_id ?? null,
      mechanism: mechanismOf(row),
      mz: row.sample_peak_mz ?? null,
      consensus_tier: row.tier ?? null,
      sample_peak_id: row.sample_peak_id
    }))
  }
}

// --- Group by -------------------------------------------------------------------

/**
 * The ledger's rows as group lines over their members (Show > Group by), and
 * the rows in no group under an Ungrouped line at the end.
 *
 * Each set is a group: its key, its name, and the ids of its members. A
 * section lists, in the rows' own order (the sort), the rows of its set that
 * the filters left. A row that is in several sets is listed under the first in
 * which it lands and named again in each later one as a reference, so every
 * row is drawn once. Sections are ordered by where their first row lands. A
 * section the filters leave empty is not drawn; the Ungrouped line is, alone,
 * when the filters leave rows in no set.
 *
 * @param {Array<object>} rows - the rows, filtered and sorted
 * @param {Array<{key: string, name: string, ids: Array<string>}>} sets
 * @param {(row: object) => string} idOf - a row's member id
 * @returns {Array<{key: string, name: string, size: number, rows: Array<object>,
 *   references: Array<{row: object, under: string}>, ungrouped?: boolean}>}
 */
export function groupSections(rows, sets, idOf) {
  const position = new Map(rows.map((row, index) => [idOf(row), index]))
  const placed = new Map()
  const ordered = sets
    .map((set) => ({
      set,
      first: Math.min(...set.ids.map((id) => position.get(id) ?? Infinity))
    }))
    .filter(({ first }) => first !== Infinity)
    .sort((a, b) => a.first - b.first)
  const sections = []
  for (const { set } of ordered) {
    const own = []
    const references = []
    const members = set.ids
      .filter((id) => position.has(id))
      .sort((a, b) => position.get(a) - position.get(b))
    for (const id of members) {
      const row = rows[position.get(id)]
      if (placed.has(id)) references.push({ row, under: placed.get(id) })
      else {
        placed.set(id, set.name)
        own.push(row)
      }
    }
    sections.push({ key: set.key, name: set.name, size: set.ids.length, rows: own, references })
  }
  // Drawn also when no set kept a row: the rows the filters keep are listed
  // whatever the grouping, never dropped with the sections they are not in.
  const rest = rows.filter((row) => !placed.has(idOf(row)))
  if (rest.length) {
    sections.push({
      key: 'ungrouped',
      name: 'Ungrouped',
      size: rest.length,
      rows: rest,
      references: [],
      ungrouped: true
    })
  }
  return sections
}

/**
 * The sets Group by draws from the batch's groups: by compound, one per
 * channel group, named by its formula; by series, one per series, named as
 * the lens names it.
 *
 * @param {Array<object>|null} groups - `batch_groups`
 * @param {'compound'|'series'} mode
 * @returns {Array<{key: string, name: string, ids: Array<string>}>}
 */
export function batchGroupSets(groups, mode) {
  const kind = mode === 'series' ? GROUP_SERIES : mode === 'compound' ? GROUP_CHANNELS : null
  if (!kind || !Array.isArray(groups)) return []
  return groups
    .filter((group) => group?.kind === kind)
    .map((group) => ({
      key: group.group_id,
      name: kind === GROUP_SERIES ? groupName(group) : group.formula || group.members?.[0]?.formula,
      ids: (group.members ?? []).map((member) => member.batch_peak_id)
    }))
}

/**
 * The sets Group by draws from a sample's own rows, by compound: one per
 * neutral two or more of the rows read.
 *
 * @param {Array<object>} rows - the sample's rows, isotope lines left out
 * @param {(row: object) => string} keyOf - a row's neutral, '' for none
 * @param {(row: object) => string} idOf
 * @returns {Array<{key: string, name: string, ids: Array<string>}>}
 */
export function sampleGroupSets(rows, keyOf, idOf) {
  const byKey = new Map()
  for (const row of rows) {
    const key = keyOf(row)
    if (!key) continue
    const set = byKey.get(key)
    if (set) set.ids.push(idOf(row))
    else byKey.set(key, { key: `compound:${key}`, name: row.assigned_formula, ids: [idOf(row)] })
  }
  return [...byKey.values()].filter((set) => set.ids.length > 1)
}
