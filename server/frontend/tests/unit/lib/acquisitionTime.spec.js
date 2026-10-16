import { describe, it, expect } from 'vitest'

import {
  ALL_TIME,
  DEFAULT_TIME_MODE,
  TIME_MODES,
  daysOf,
  describeTimeMode,
  widerTimeMode
} from '@/lib/acquisitionTime'

// The Raw files list is narrowed to a time range, and used to open on the
// last day only: on a server whose files were older than that it opened empty,
// with nothing to say why and nothing nearer than the selector to do about it.
// These are the pieces the selector, the store and the empty state share.

describe('the time ranges of the Raw files list', () => {
  it('end in All time, widest last', () => {
    expect(TIME_MODES.at(-1)).toBe(ALL_TIME)
    expect(TIME_MODES).toEqual([
      'Last 24 hours',
      'Last 7 days',
      'Last 30 days',
      'Last 90 days',
      'All time'
    ])
  })

  it('open on a month, which is one of them', () => {
    expect(DEFAULT_TIME_MODE).toBe('Last 30 days')
    expect(TIME_MODES).toContain(DEFAULT_TIME_MODE)
  })

  it('translate a preset into the look-back the recent-files route takes', () => {
    expect(daysOf('Last 24 hours')).toBe(1)
    expect(daysOf('Last 7 days')).toBe(7)
    expect(daysOf('Last 90 days')).toBe(90)
  })

  it('have no look-back for all time, a date range, or nonsense', () => {
    expect(daysOf(ALL_TIME)).toBeNull()
    expect(daysOf('range')).toBeNull()
    expect(daysOf('Last year')).toBeNull()
  })
})

describe('widening an empty list', () => {
  it('offers the next preset up', () => {
    expect(widerTimeMode('Last 24 hours')).toBe('Last 7 days')
    expect(widerTimeMode('Last 30 days')).toBe('Last 90 days')
    expect(widerTimeMode('Last 90 days')).toBe(ALL_TIME)
  })

  it('has nowhere further to go from all time', () => {
    expect(widerTimeMode(ALL_TIME)).toBeNull()
  })

  // A date range has no "next" preset; the empty state offers All time alone.
  it('offers no preset from an explicit date range', () => {
    expect(widerTimeMode('range')).toBeNull()
  })
})

describe('naming the range in force', () => {
  it('ends a sentence with the preset', () => {
    expect(describeTimeMode('Last 7 days')).toBe('in the last 7 days')
    expect(describeTimeMode('Last 24 hours')).toBe('in the last 24 hours')
  })

  it('names a date range without repeating its ends', () => {
    expect(describeTimeMode('range')).toBe('in the selected date range')
  })

  it('has nothing to blame for all time', () => {
    expect(describeTimeMode(ALL_TIME)).toBeNull()
  })
})
