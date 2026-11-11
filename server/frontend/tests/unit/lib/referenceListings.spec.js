import { describe, it, expect } from 'vitest'

import {
  candidatesTooltip,
  ledgerListing,
  listingName,
  listingOf,
  listingSource,
  listingTags,
  listingTooltip,
  targetMore,
  targetName,
  targetOf,
  targetTooltip
} from '@/lib/referenceListings'

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

// A list may say how it reads its compounds: the cyclic siloxanes are a
// background of most inlets and an analyte of indoor air, and their list says
// the first on every row it names.
describe('a tagged listing', () => {
  const D4 = {
    name: 'Octamethylcyclotetrasiloxane (D4)',
    source: 'cyclic-siloxanes',
    xrefs: { reference: '10.1021/es200301j', tags: ['background'] }
  }
  const PUBCHEM_D4 = { name: 'Octamethylcyclotetrasiloxane', source: 'pubchem', xrefs: {} }

  it('carries the tags of the lists that name the formula, each once', () => {
    expect(listingTags(listingOf({ reference_identities: [D4, D4] }))).toEqual(['background'])
    expect(listingTags(listingOf({ reference_identities: [PUBCHEM_D4, D4] }))).toEqual([
      'background'
    ])
  })

  it('carries none where no list tags it', () => {
    expect(listingTags(listingOf({ reference_identities: [DMF, PUBCHEM_D4] }))).toEqual([])
    expect(listingTags(listingOf({ reference_identities: [{ xrefs: { tags: 'x' } }] }))).toEqual([])
    expect(listingTags(null)).toEqual([])
  })

  it('says on hover which list reads it as background, and that the tier does not', () => {
    const lines = listingTooltip(listingOf({ reference_identities: [PUBCHEM_D4, D4] })).split('\n')
    const reading = lines.find((line) => line.startsWith('The cyclic-siloxanes list tags it'))
    expect(reading).toContain('background of laboratory air or of the instrument')
    expect(reading).toContain('the tier does not weigh it')
    expect(lines.at(-1)).toBe(
      'A formula match names candidate compounds; it is not an identification.'
    )
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

// A ledger row carries what the inspector reads off the row's provenance
// flattened into one field (step 3.4d); read back into a listing, the same
// helpers say the same thing of it.
describe('ledgerListing', () => {
  const FLAT = {
    name: 'decamethylcyclopentasiloxane',
    source: 'cyclic-siloxanes',
    tags: ['background'],
    total: 2
  }

  it('carries the InChIKey of the first name, for the verdict forms to offer', () => {
    const listing = ledgerListing({ ...FLAT, inchikey: 'XMSXQFUHVRWGNA-UHFFFAOYSA-N' })

    expect(listing.identities[0].inchikey).toBe('XMSXQFUHVRWGNA-UHFFFAOYSA-N')
  })

  it('reads a flattened listing as the match it is', () => {
    const listing = ledgerListing(FLAT)

    expect(listing.matched).toBe(true)
    expect(listingName(listing)).toBe('decamethylcyclopentasiloxane +1')
    expect(listingSource(listing)).toBe('cyclic-siloxanes')
    expect(listingTags(listing)).toEqual(['background'])
  })

  it('says in the tooltip what the tag means and how many more names there are', () => {
    const tooltip = listingTooltip(ledgerListing(FLAT))

    expect(tooltip).toContain('The run matched this formula from a reference list.')
    expect(tooltip).toContain('decamethylcyclopentasiloxane (cyclic-siloxanes)')
    expect(tooltip).toContain('and 1 more')
    expect(tooltip).toContain('The cyclic-siloxanes list tags it background')
  })

  it('reads a listing with no tags or count as one untagged name', () => {
    const listing = ledgerListing({ name: 'alpha-pinene', source: 'monoterpenes' })

    expect(listingName(listing)).toBe('alpha-pinene')
    expect(listingTags(listing)).toEqual([])
  })

  it.each([null, undefined, 'D5', 3])('reads %s as no listing', (flat) => {
    expect(ledgerListing(flat)).toBeNull()
  })
})

// What the target compound a row was matched to declares, beside what a list
// calls the formula (identification claims, section 4.2).
describe('targetOf', () => {
  const GLUCOSE = 'WQZGKKKJIJFFOK-GASJEMHNSA-N'
  const TARGET = { label: 'target', name: 'glucose', inchikey: GLUCOSE, listed: true }
  const LISTED = { name: 'D-glucose', source: 'sugars', inchikey: GLUCOSE, tags: [], total: 3 }

  it('reads the target a row was matched to, and nothing where it names none', () => {
    expect(targetOf({ ...LISTED, target: TARGET })).toMatchObject({
      name: 'glucose',
      inchikey: GLUCOSE,
      listed: true,
      identity: null,
      unlisted: false
    })
    expect(targetOf(LISTED)).toBeNull()
    expect(targetOf(null)).toBeNull()
  })

  it("is one candidate with the listing's identity of the same structure", () => {
    const flat = { ...LISTED, target: TARGET }
    const listing = ledgerListing(flat)
    const target = targetOf(flat, listing)

    expect(target.identity).toBe(listing.identities[0])
    expect(targetMore(target, listing)).toBe(2)
    expect(targetTooltip(target)).toContain(
      'The sugars list names the same structure: one candidate, shown once.'
    )
  })

  it("is a candidate of its own beside another structure's listing", () => {
    const flat = { ...LISTED, inchikey: 'OTHERKEYOTHERK-UHFFFAOYSA-N', target: TARGET }
    const listing = ledgerListing(flat)
    const target = targetOf(flat, listing)

    expect(target.identity).toBeNull()
    expect(targetMore(target, listing)).toBe(0)
    expect(targetTooltip(target)).toContain('A reference list carries its InChIKey.')
  })

  it('reads a key its own listing names as listed, whatever the lookup said', () => {
    // A list the run matched from may have gone since; the row still names it.
    const flat = { ...LISTED, target: { ...TARGET, listed: false } }
    const target = targetOf(flat, ledgerListing(flat))

    expect(target.unlisted).toBe(false)
    expect(targetTooltip(target)).not.toContain('No reference list carries')
  })

  it('reads a key no list carries as unlisted', () => {
    const target = targetOf({ total: 0, target: { ...TARGET, listed: false } })

    expect(target.unlisted).toBe(true)
    expect(targetTooltip(target)).toContain(
      'No reference list carries its InChIKey: a compound the lists do not know.'
    )
  })

  it('shows a target with no structure by its name and no key', () => {
    const target = targetOf({
      total: 0,
      target: { label: 'target', name: 'glucose', inchikey: null, listed: null }
    })

    expect(targetName(target)).toBe('glucose')
    expect(target.unlisted).toBe(false)
    const lines = targetTooltip(target).split('\n')
    expect(lines[1]).toBe('glucose. The target declares no structure.')
    expect(lines.join('\n')).not.toContain('InChIKey')
  })

  it('says which candidate is which on hover', () => {
    const target = targetOf({ total: 0, target: { ...TARGET, name: null } })

    expect(targetName(target)).toBe('Unnamed compound')
    expect(targetTooltip(target).split('\n')[0]).toBe(
      'The target compound this row was matched to: what the target stands for, not what the peak is.'
    )
    expect(targetTooltip(target)).toContain(`Unnamed compound (InChIKey ${GLUCOSE})`)
  })

  it("reads a listing that only the target makes as no list's", () => {
    expect(
      ledgerListing({ name: null, source: null, tags: [], total: 0, target: TARGET })
    ).toBeNull()
  })

  it('puts both candidates in one tooltip, the target first', () => {
    const flat = { ...LISTED, target: TARGET }
    const listing = ledgerListing(flat)
    const [first, second] = candidatesTooltip(targetOf(flat, listing), listing).split('\n\n')

    expect(first).toBe(targetTooltip(targetOf(flat, listing)))
    expect(second).toBe(listingTooltip(listing))
    expect(candidatesTooltip(null, listing)).toBe(listingTooltip(listing))
  })
})
