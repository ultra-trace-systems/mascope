import { describe, expect, it } from 'vitest'

import {
  allowedValuesLabel,
  deleteBlocked,
  fieldBody,
  fieldForm,
  formProblem,
  keyFromLabel,
  parseAllowedValues
} from '@/lib/metadataFields'

// The registry pane's words for a field, and the request bodies it sends:
// every attribute for a new field, only what changed for an existing one.

const field = (extra = {}) => ({
  metadata_field_id: 'f1',
  key: 'inlet',
  label: 'Inlet',
  type: 'enum',
  unit: null,
  allowed_values: ['ambient', 'chamber'],
  description: null,
  system: false,
  in_use: false,
  retired: false,
  ...extra
})

describe('keyFromLabel', () => {
  it.each([
    ['Inlet height', 'inlet_height'],
    ['  Site / campaign  ', 'site_campaign'],
    ['2nd inlet', 'nd_inlet'],
    ['Höhe', 'hohe'],
    ['', '']
  ])('suggests %j -> %j', (label, key) => {
    expect(keyFromLabel(label)).toBe(key)
  })

  it('stays within the 64 characters of a key', () => {
    expect(keyFromLabel('a'.repeat(100))).toHaveLength(64)
  })
})

describe('parseAllowedValues', () => {
  it('takes one value a line, trimmed, blank lines left out', () => {
    expect(parseAllowedValues(' ambient\n\nchamber \n')).toEqual(['ambient', 'chamber'])
  })
})

describe('allowedValuesLabel', () => {
  it('lists the values of a choice', () => {
    expect(allowedValuesLabel(field())).toBe('ambient, chamber')
  })
  it('says a tags field without values takes any', () => {
    expect(allowedValuesLabel(field({ type: 'tags', allowed_values: [] }))).toBe('Any')
  })
  it('says nothing for a type that has no values', () => {
    expect(allowedValuesLabel(field({ type: 'number', allowed_values: null }))).toBe('')
  })
})

describe('fieldBody', () => {
  it('sends every attribute of a new field, blanks as null', () => {
    const form = { ...fieldForm(), key: ' site ', label: ' Site ', type: 'text' }
    expect(fieldBody(form)).toEqual({
      key: 'site',
      label: 'Site',
      type: 'text',
      unit: null,
      description: null,
      allowed_values: null
    })
  })

  it('sends allowed values only for a choice', () => {
    const form = { ...fieldForm(), key: 'h', label: 'H', type: 'number', allowedValues: 'a' }
    expect(fieldBody(form).allowed_values).toBeNull()
  })

  it('sends only what changed of an existing field', () => {
    const original = field()
    const form = fieldForm(original)
    expect(fieldBody(form, original)).toEqual({})

    form.label = 'Inlet type'
    form.allowedValues = 'ambient\nchamber\nflow tube'
    expect(fieldBody(form, original)).toEqual({
      label: 'Inlet type',
      allowed_values: ['ambient', 'chamber', 'flow tube']
    })
  })

  it('never sends the key of an existing field', () => {
    const original = field()
    const form = { ...fieldForm(original), key: 'other' }
    expect(fieldBody(form, original)).not.toHaveProperty('key')
  })

  it('leaves the shipped field values alone when only its label changes', () => {
    const original = field({ type: 'tags', allowed_values: [], system: true })
    const form = { ...fieldForm(original), label: 'Sampled environment' }
    expect(fieldBody(form, original)).toEqual({ label: 'Sampled environment' })
  })
})

describe('formProblem', () => {
  it.each(['', 'Site', '1site', 'site-name'])('refuses the new key %j', (key) => {
    expect(formProblem({ ...fieldForm(), key, label: 'Site' }, true)).toMatch(/key/)
  })
  it('does not check the key of an existing field', () => {
    expect(formProblem({ ...fieldForm(field()), key: '' }, false)).toBeNull()
  })
  it('needs a label', () => {
    expect(formProblem({ ...fieldForm(), key: 'site', label: ' ' }, true)).toMatch(/label/)
  })
  it('needs a value for a field of one of a list', () => {
    const form = { ...fieldForm(), key: 'inlet', label: 'Inlet', type: 'enum' }
    expect(formProblem(form, true)).toMatch(/allowed value/)
    expect(formProblem({ ...form, type: 'tags' }, true)).toBeNull()
  })
})

describe('deleteBlocked', () => {
  it('keeps the shipped field and a field in use', () => {
    expect(deleteBlocked(field({ system: true }))).toMatch(/Mascope ships/)
    expect(deleteBlocked(field({ in_use: true }))).toMatch(/Retire it instead/)
    expect(deleteBlocked(field())).toBeNull()
  })
})
