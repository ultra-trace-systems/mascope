import { describe, it, expect } from 'vitest'

import { sourceLabel, sourceLine } from '@/lib/assignmentSources'

// Every source the API serves (the backend's AssignmentSource literal) has to
// be named in words: the inspector's line under the formula and the Peak
// Browser's source mark printed the raw value until each read it here.
const API_SOURCES = ['database', 'untargeted', 'manual', 'reagent', 'artifact']

describe('sourceLabel', () => {
  it('names every source the API serves', () => {
    expect(API_SOURCES.map(sourceLabel)).toEqual([
      'list match',
      'formula search',
      'by hand',
      'reagent',
      'artifact'
    ])
  })

  it('shows a source this build does not know as it came', () => {
    expect(sourceLabel('ms2')).toBe('ms2')
  })

  it('says nothing for a row with no source', () => {
    expect(sourceLabel(null)).toBeNull()
    expect(sourceLabel(undefined)).toBeNull()
    expect(sourceLabel('')).toBeNull()
  })

  it('does not take an inherited property for a source', () => {
    expect(sourceLabel('constructor')).toBe('constructor')
    expect(sourceLine('constructor')).toBeNull()
  })
})

describe('sourceLine', () => {
  it('gives every source the API serves a line of its own', () => {
    const lines = API_SOURCES.map(sourceLine)
    expect(lines.every((line) => typeof line === 'string' && line.length > 0)).toBe(true)
    expect(new Set(lines).size).toBe(API_SOURCES.length)
  })

  it('keeps the hover lines the chip and the inspector already said', () => {
    expect(sourceLine('database')).toBe('Matched from a target or reference list')
    expect(sourceLine('untargeted')).toBe('Found by the formula search')
    expect(sourceLine('manual')).toBe('Assigned by hand')
  })

  it('says nothing for a source it does not know', () => {
    expect(sourceLine('ms2')).toBeNull()
    expect(sourceLine(null)).toBeNull()
  })
})
