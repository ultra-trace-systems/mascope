import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

// The chip has a spec of its own; here it says which level it was handed.
vi.mock('@/lib/base', () => ({
  BaseLevelTag: {
    props: ['level', 'evidence', 'lift'],
    template: '<span class="level-tag" :data-level="level" />'
  }
}))

const { default: LedgerLevelSources } = await import('@/lib/ledger/LedgerLevelSources.vue')

const ButtonStub = {
  props: ['label', 'disabled'],
  emits: ['click'],
  template:
    '<button class="p-button" :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>'
}

const member = (id, level, intensity, extra = {}) => ({
  sample_item_id: id,
  sample_item_name: `Sample ${id}`,
  datetime_utc: '2026-07-04T12:00:00+00:00',
  sample_peak_id: `p-${id}`,
  peak_assignment_id: `pa-${id}`,
  intensity,
  level,
  level_evidence: `evidence of ${level}`,
  level_lift: `lift of ${level}`,
  ...extra
})

// The read's body: 4a in two samples, 5a in three, one 5a member saying
// something else; and members that state none.
const sources = (extra = {}) => ({
  batch_peak_id: 'bp-1',
  consensus_formula: 'C6H12O6',
  n_members: 21,
  member_levels: { '4a': 2, '5a': 3 },
  member_level: '5a',
  data: [
    member('s1', '5a', 900),
    member('s2', '4a', 800),
    member('s3', '5a', 700, { level_evidence: 'another line' }),
    member('s4', '5a', 600),
    member('s5', '4a', 100)
  ],
  unstated: { no_run: 12, no_formula: 0, other_formula: 3, no_level: 1 },
  ...extra
})

const mountIt = (props = {}) =>
  mount(LedgerLevelSources, {
    props: { memberLevel: '5a', memberLevels: { '4a': 2, '5a': 3 }, sources: sources(), ...props },
    global: { stubs: { Button: ButtonStub }, directives: { tooltip: {} } }
  })

const testid = (wrapper, id) => wrapper.find(`[data-testid="${id}"]`)

describe('LedgerLevelSources', () => {
  it("renders the split in the scale's order, the shown level marked", () => {
    const wrapper = mountIt()
    const rows = wrapper.findAll('[data-testid="level-split-row"]')

    expect(rows.map((row) => row.attributes('data-level'))).toEqual(['4a', '5a'])
    expect(rows.map((row) => row.find('.count').text())).toEqual(['2 samples', '3 samples'])
    expect(rows[0].find('[data-testid="level-shown-mark"]').exists()).toBe(false)
    expect(rows[1].find('[data-testid="level-shown-mark"]').text()).toBe('shown')
  })

  it("gives the shown level's evidence and lift, the most common of each and how many say it", () => {
    const wrapper = mountIt()

    const evidence = testid(wrapper, 'level-level_evidence').text()
    expect(evidence).toContain('Why: evidence of 5a')
    expect(evidence).toContain('(2 of 3 samples at 5a say this)')
    // Every member at 5a wrote the same lift: no count.
    const lift = testid(wrapper, 'level-level_lift').text()
    expect(lift).toContain('Would lift it: lift of 5a')
    expect(lift).not.toContain('say this')
  })

  it('calls out a better level some members state, with a way to it', async () => {
    const wrapper = mountIt()
    const better = testid(wrapper, 'level-better')

    expect(better.text()).toContain('2 samples read 4a')
    await better.find('button').trigger('click')
    const [opened, members, level] = wrapper.emitted('open-member')[0]
    expect(opened.sample_item_id).toBe('s2')
    expect(members.map((m) => m.sample_item_id)).toEqual(['s2', 's5'])
    expect(level).toBe('4a')
  })

  it('calls out nothing where the shown level is the best any member states', () => {
    const wrapper = mountIt({
      memberLevel: '4a',
      sources: sources({ member_levels: { '4a': 3, '5a': 2 }, member_level: '4a' })
    })

    expect(testid(wrapper, 'level-better').exists()).toBe(false)
  })

  it("'Open a sample' asks for the brightest member at that level", async () => {
    const wrapper = mountIt()
    const rows = wrapper.findAll('[data-testid="level-split-row"]')

    await rows[1].find('[data-testid="level-open"]').trigger('click')

    const [opened, members, level] = wrapper.emitted('open-member')[0]
    expect(opened.sample_item_id).toBe('s1')
    expect(members.map((m) => m.sample_item_id)).toEqual(['s1', 's3', 's4'])
    expect(level).toBe('5a')
  })

  it('lists every member at a level, to open another than the brightest', async () => {
    const wrapper = mountIt()
    const rows = wrapper.findAll('[data-testid="level-split-row"]')

    await rows[0].find('[data-testid="level-list"]').trigger('click')

    const listed = wrapper.findAll('[data-testid="level-member"]')
    expect(listed.map((row) => row.findAll('td')[0].text())).toEqual(['Sample s2', 'Sample s5'])
    const cells = listed[1].findAll('td').map((td) => td.text())
    expect(cells[1]).not.toBe('-')
    expect(cells[2]).toBe('1.000E2')
    expect(cells[3]).toBe('4a')
    await listed[1].find('button').trigger('click')
    const [opened, members, level] = wrapper.emitted('open-member')[0]
    expect(opened.sample_item_id).toBe('s5')
    expect(members.map((m) => m.sample_item_id)).toEqual(['s2', 's5'])
    expect(level).toBe('4a')
  })

  it('counts the members that state no level, by why, and offers Assign batch to an editor', async () => {
    const wrapper = mountIt({ canAssign: true })
    const unstated = testid(wrapper, 'level-unstated')

    expect(unstated.findAll('p').map((p) => p.text())).toEqual([
      '12 samples have no run of their own.',
      "3 samples' runs commit another formula.",
      "1 sample's run states no level for it."
    ])
    await testid(wrapper, 'level-assign').trigger('click')
    expect(wrapper.emitted('assign')).toHaveLength(1)
  })

  it('offers no Assign batch to a reader who may not run it, and holds it while blocked', () => {
    expect(testid(mountIt(), 'level-assign').exists()).toBe(false)

    const blocked = mountIt({ canAssign: true, assignBlocked: 'A pass is running' })
    expect(testid(blocked, 'level-assign').attributes('disabled')).toBeDefined()
  })

  it("says a live claim first, with its evidence and lift, the members' split under it", () => {
    const wrapper = mountIt({
      claim: { level: '1', evidence: 'a reference standard', lift: null }
    })
    const claim = testid(wrapper, 'level-claim')

    expect(claim.find('.level-tag').attributes('data-level')).toBe('1')
    expect(claim.text()).toContain('A batch-level verdict states this level')
    expect(claim.text()).toContain('Why: a reference standard')
    expect(claim.text()).not.toContain('Would lift it')
    // The claim comes before the split; the members' level is marked as theirs.
    const html = wrapper.html()
    expect(html.indexOf('level-claim')).toBeLessThan(html.indexOf('level-split'))
    expect(testid(wrapper, 'level-shown-mark').text()).toBe("members' level")
    // Nothing is better than a level-1 claim.
    expect(testid(wrapper, 'level-better').exists()).toBe(false)
  })

  it("says the batch's own level first, with its lines, the members' own split under it", () => {
    const wrapper = mountIt({
      batchLevel: {
        level: '4b',
        evidence: 'the bromine lock puts out C8H9NO4; 3 of 5 members read 4b',
        lift: 'MS/MS'
      }
    })
    const own = testid(wrapper, 'level-batch')

    expect(own.find('.level-tag').attributes('data-level')).toBe('4b')
    expect(own.text()).toContain('The batch states this level')
    expect(own.text()).toContain('Why: the bromine lock puts out C8H9NO4')
    expect(own.text()).toContain('Would lift it: MS/MS')
    const html = wrapper.html()
    expect(html.indexOf('level-batch')).toBeLessThan(html.indexOf('level-split'))
    // The members' 5a is theirs, not the one the row shows.
    expect(testid(wrapper, 'level-shown-mark').text()).toBe("members' level")
    // The batch states a level, so the note that it states none is gone.
    expect(testid(wrapper, 'level-no-batch').exists()).toBe(false)
    expect(testid(mountIt(), 'level-no-batch').exists()).toBe(true)
  })

  it("measures a better level some members state against the batch's own", () => {
    // 4a is better than the batch's 4b: still the nearest way to raise the row.
    const lifted = mountIt({ batchLevel: { level: '4b', evidence: null, lift: null } })
    expect(testid(lifted, 'level-better').text()).toContain('2 samples read 4a')
    expect(testid(lifted, 'level-batch').text()).not.toContain('Why')

    // At the batch's 4a, no member's run states a better one.
    const best = mountIt({ batchLevel: { level: '4a', evidence: null, lift: null } })
    expect(testid(best, 'level-better').exists()).toBe(false)
  })

  it("puts a live claim before the batch's own level, and says both are above the split", () => {
    const wrapper = mountIt({
      claim: { level: '1', evidence: 'a reference standard', lift: null },
      batchLevel: { level: '4a', evidence: 'the lock', lift: null }
    })
    const html = wrapper.html()

    expect(html.indexOf('level-claim')).toBeLessThan(html.indexOf('level-batch'))
    expect(testid(wrapper, 'level-claim').text()).toContain("the batch's own level and what")
  })

  it("measures a better level against the claim's, which the row shows, not the batch's", () => {
    // The members' 4a is better than the batch's 4b, not than the claim's 1.
    const wrapper = mountIt({
      claim: { level: '1', evidence: 'a reference standard', lift: null },
      batchLevel: { level: '4b', evidence: null, lift: null }
    })

    expect(testid(wrapper, 'level-better').exists()).toBe(false)
  })

  it("shows the ledger's split while the read is asked, with no way to a member yet", () => {
    const wrapper = mountIt({ sources: null, loading: true })

    expect(wrapper.text()).toContain("Reading the members' levels")
    expect(wrapper.findAll('[data-testid="level-split-row"]')).toHaveLength(2)
    expect(testid(wrapper, 'level-open').attributes('disabled')).toBeDefined()
  })

  it('says a failed read', () => {
    const wrapper = mountIt({ sources: null, error: 'Could not read' })

    expect(testid(wrapper, 'level-sources-error').text()).toBe('Could not read')
  })

  it("says an earlier run's ledger keeps no member levels, and lists none", () => {
    const wrapper = mountIt({ sources: null, snapshot: true, memberLevels: null })

    expect(testid(wrapper, 'level-snapshot').exists()).toBe(true)
    expect(testid(wrapper, 'level-split').exists()).toBe(false)
  })
})
