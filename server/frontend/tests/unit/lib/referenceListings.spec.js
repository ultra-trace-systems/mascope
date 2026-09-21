import { describe, it, expect } from 'vitest'

import { listingName, listingOf, listingSource, listingTooltip } from '@/lib/referenceListings'

const DMF = { name: 'N,N-Dimethylformamide', source: 'contaminants-list' }
const ACROLEIN = { name: 'Acrolein', source: 'organics-list' }

describe('listingOf', () => {
  it('reads the identities the run matched as a match', () => {
    expect(listingOf({ reference_identities: [DMF] })).toEqual({
      matched: true,
      identities: [DMF],
      total: 1
    })
  })

  it("takes a row's matched identities from where they are recorded", () => {
    // A row keeps them in its provenance, not on the record itself.
    expect(listingOf({ known_compounds: [ACROLEIN] }, [DMF])).toEqual({
      matched: true,
      identities: [DMF],
      total: 1
    })
  })

  it('reads what a list holds for the formula as a lead, where the run matched none', () => {
    expect(listingOf({ known_compounds: [ACROLEIN] })).toEqual({
      matched: false,
      identities: [ACROLEIN],
      total: 1
    })
    expect(listingOf({ reference_identities: [], known_compounds: [ACROLEIN] })).toEqual({
      matched: false,
      identities: [ACROLEIN],
      total: 1
    })
  })

  it('is nothing where no list names the formula', () => {
    expect(listingOf({ known_compounds: [] })).toBeNull()
    expect(listingOf({})).toBeNull()
    expect(listingOf(null)).toBeNull()
  })
})

describe('listingName and listingSource', () => {
  it('name the first compound and its list', () => {
    const listing = listingOf({ known_compounds: [DMF] })
    expect(listingName(listing)).toBe('N,N-Dimethylformamide')
    expect(listingSource(listing)).toBe('contaminants-list')
  })

  it('count the compounds that share the formula', () => {
    expect(listingName(listingOf({ known_compounds: [DMF, ACROLEIN, DMF] }))).toBe(
      'N,N-Dimethylformamide +2'
    )
  })

  it('say a compound a list left unnamed is unnamed', () => {
    expect(listingName(listingOf({ known_compounds: [{ source: 'x' }] }))).toBe('Unnamed compound')
  })

  it('are empty for no listing', () => {
    expect(listingName(null)).toBe('')
    expect(listingSource(null)).toBe('')
  })
})

describe('listingTooltip', () => {
  it("says a match is the run's, and names every compound with its list", () => {
    const text = listingTooltip(listingOf({ reference_identities: [DMF, ACROLEIN] }))
    expect(text.split('\n')).toEqual([
      'The run matched this formula from a reference list.',
      'N,N-Dimethylformamide (contaminants-list)',
      'Acrolein (organics-list)',
      'A formula match names candidate compounds; it is not an identification.'
    ])
  })

  it('says a listing the run did not match is a lead', () => {
    const text = listingTooltip(listingOf({ known_compounds: [ACROLEIN] }))
    expect(text.split('\n')[0]).toBe(
      'A reference list holds this formula. The run did not match it from the list, so the name is a lead to check.'
    )
  })

  it('is empty for no listing', () => {
    expect(listingTooltip(null)).toBe('')
  })
})

// A listing carries at most as many names as a run keeps; the lookup counts
// every record that names the formula, and the listing says so.
describe('a listing past the names it carries', () => {
  const many = { known_compounds: [DMF, ACROLEIN], known_compounds_total: 300 }

  it('counts every record that names the formula', () => {
    expect(listingOf(many).total).toBe(300)
    expect(listingName(listingOf(many))).toBe('N,N-Dimethylformamide +299')
  })

  it('says on hover how many it does not list', () => {
    const lines = listingTooltip(listingOf(many)).split('\n')
    expect(lines).toContain('and 298 more the lists hold for it')
  })

  it("counts the run's own match by the lookup too", () => {
    const listing = listingOf(many, [DMF])
    expect(listing.matched).toBe(true)
    expect(listingName(listing)).toBe('N,N-Dimethylformamide +299')
  })

  it('never counts fewer than the names it carries', () => {
    const listing = listingOf({ known_compounds: [DMF, ACROLEIN], known_compounds_total: 1 })
    expect(listing.total).toBe(2)
    expect(listingTooltip(listing)).not.toContain('more the lists hold')
  })
})
