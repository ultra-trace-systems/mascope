import { describe, it, expect } from 'vitest'

import {
  addKeyword,
  metadataForm,
  metadataInvalid,
  metadataPayload,
  moveUp,
  orcidInvalid
} from '@/lib/dialogs/datasetMetadata.js'

const STORED = {
  dataset_id: 'ds-1',
  license: 'CC-BY-4.0',
  authors: [
    { name: 'Author One', orcid: '0000-0002-1825-009X', affiliation: 'Lab A' },
    { name: 'Author Two', orcid: null, affiliation: null }
  ],
  keywords: ['aerosol'],
  related_identifiers: [
    { identifier: '10.1234/x', relation: 'IsSupplementTo', identifier_type: 'DOI' }
  ],
  funding: [{ funder: 'Funder A', award: null }]
}

describe('metadataForm', () => {
  it('is empty for a new dataset', () => {
    expect(metadataForm(null)).toEqual({
      license: '',
      authors: [],
      keywords: [],
      related_identifiers: [],
      funding: []
    })
  })

  it('round-trips a stored dataset through the payload unchanged', () => {
    expect(metadataPayload(metadataForm(STORED))).toEqual({
      license: STORED.license,
      authors: STORED.authors,
      keywords: STORED.keywords,
      related_identifiers: STORED.related_identifiers,
      funding: STORED.funding
    })
  })

  it('copies the lists, so editing the form leaves the dataset alone', () => {
    const form = metadataForm(STORED)
    form.authors[0].name = 'Changed'
    form.keywords.push('more')
    expect(STORED.authors[0].name).toBe('Author One')
    expect(STORED.keywords).toEqual(['aerosol'])
  })
})

describe('metadataPayload', () => {
  it('sends every field, null when empty, so an update clears it', () => {
    expect(metadataPayload(metadataForm(null))).toEqual({
      license: null,
      authors: null,
      keywords: null,
      related_identifiers: null,
      funding: null
    })
  })

  it('trims, drops rows without their required part and sends blanks as null', () => {
    const payload = metadataPayload({
      license: '  CC0-1.0 ',
      authors: [
        { name: ' Ann ', orcid: ' ', affiliation: ' Lab ' },
        { name: '  ', orcid: '0000-0002-1825-0097', affiliation: '' }
      ],
      keywords: [' a ', ' '],
      related_identifiers: [
        { identifier: '10.1/x', relation: '', identifier_type: '' },
        { identifier: '', relation: 'Cites', identifier_type: '' },
        { identifier: ' 10.1/y ', relation: 'Cites', identifier_type: '' }
      ],
      funding: [
        { funder: '', award: '1' },
        { funder: 'F', award: ' ' }
      ]
    })
    expect(payload).toEqual({
      license: 'CC0-1.0',
      authors: [{ name: 'Ann', orcid: null, affiliation: 'Lab' }],
      keywords: ['a'],
      related_identifiers: [{ identifier: '10.1/y', relation: 'Cites', identifier_type: null }],
      funding: [{ funder: 'F', award: null }]
    })
  })
})

describe('validation', () => {
  it('accepts a blank ORCID and a well-formed one, refuses a malformed one', () => {
    expect(orcidInvalid('')).toBe(false)
    expect(orcidInvalid(null)).toBe(false)
    expect(orcidInvalid('0000-0002-1825-009X')).toBe(false)
    expect(orcidInvalid('0000-0002-1825')).toBe(true)
  })

  it('holds the form back for a malformed ORCID or an over-long licence', () => {
    const form = metadataForm(STORED)
    expect(metadataInvalid(form)).toBe(false)
    expect(metadataInvalid({ ...form, license: 'x'.repeat(65) })).toBe(true)
    form.authors[1].orcid = '1234'
    expect(metadataInvalid(form)).toBe(true)
  })
})

describe('list helpers', () => {
  it('adds a keyword once, trimmed, and never a blank one', () => {
    expect(addKeyword(['a'], ' b ')).toEqual(['a', 'b'])
    expect(addKeyword(['a'], 'a')).toEqual(['a'])
    expect(addKeyword(['a'], '  ')).toEqual(['a'])
  })

  it('moves an author up, leaving the first in place', () => {
    expect(moveUp(['a', 'b', 'c'], 2)).toEqual(['a', 'c', 'b'])
    expect(moveUp(['a', 'b'], 0)).toEqual(['a', 'b'])
  })
})
