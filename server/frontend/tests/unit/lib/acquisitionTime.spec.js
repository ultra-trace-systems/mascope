import { describe, it, expect } from 'vitest'

import {
  ALL_TIME,
  DEFAULT_TIME_MODE,
  TIME_MODES,
  daysOf,
  describeTimeMode,
  isTimeMode,
  widerTimeMode
} from '@/lib/acquisitionTime'

// The Raw files list is narrowed to a time range. On a server whose files are
// older than the range it opens empty, and the empty state has to name the
// range and offer the next one up; these are the pieces the selector, the
// store and the empty state share.

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

  // A week, not longer: a wider default is a wider query on every load, and
  // the empty state offers the wider ranges in one click.
  it('open on a week, which is one of them', () => {
    expect(DEFAULT_TIME_MODE).toBe('Last 7 days')
    expect(TIME_MODES).toContain(DEFAULT_TIME_MODE)
  })

  it('translate a preset into the look-back the recent-files route takes', () => {
    expect(daysOf('Last 24 hours')).toBe(1)
    expect(daysOf('Last 7 days')).toBe(7)
    expect(daysOf('Last 90 days')).toBe(90)
  })

  // A deployment may configure a look-back of the same form that is not a
  // preset; it reads as one, since it did before the presets were fixed.
  it('read any look-back of the same form', () => {
    expect(daysOf('Last 14 days')).toBe(14)
    expect(daysOf('Last 365 days')).toBe(365)
  })

  it('have no look-back for all time, a date range, or nonsense', () => {
    expect(daysOf(ALL_TIME)).toBeNull()
    expect(daysOf('range')).toBeNull()
    expect(daysOf('Last year')).toBeNull()
    expect(daysOf('Last 0 days')).toBeNull()
    expect(daysOf('last 7 days')).toBeNull()
    expect(daysOf(undefined)).toBeNull()
  })

  it('know which strings the store can load', () => {
    for (const preset of TIME_MODES) expect(isTimeMode(preset)).toBe(true)
    expect(isTimeMode('Last 14 days')).toBe(true)
    expect(isTimeMode('all time')).toBe(false)
    expect(isTimeMode('range')).toBe(false)
    expect(isTimeMode('')).toBe(false)
  })
})

describe('widening an empty list', () => {
  it('offers the next preset up', () => {
    expect(widerTimeMode('Last 24 hours')).toBe('Last 7 days')
    expect(widerTimeMode('Last 30 days')).toBe('Last 90 days')
    expect(widerTimeMode('Last 90 days')).toBe(ALL_TIME)
  })

  // A configured look-back between two presets offers the first preset
  // longer than it; one past every preset offers all time.
  it('offers the first preset longer than a look-back that is not one', () => {
    expect(widerTimeMode('Last 14 days')).toBe('Last 30 days')
    expect(widerTimeMode('Last 30 days')).toBe('Last 90 days')
    expect(widerTimeMode('Last 365 days')).toBe(ALL_TIME)
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
    expect(describeTimeMode('Last 14 days')).toBe('in the last 14 days')
  })

  it('names a date range without repeating its ends', () => {
    expect(describeTimeMode('range')).toBe('in the selected date range')
  })

  it('has nothing to blame for all time', () => {
    expect(describeTimeMode(ALL_TIME)).toBeNull()
  })
})
