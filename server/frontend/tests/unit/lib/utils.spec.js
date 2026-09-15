import { describe, it, expect, vi } from 'vitest'

import {
  beautifySnakeCase,
  strToSnakeCase,
  capitalizeFirstLetter,
  beautifyConstant,
  norm,
  prettyTrim,
  genId,
  clone,
  debounce,
  instrumentType,
  sampleInstrumentType
} from '@/lib/utils'

describe('string helpers', () => {
  it('beautifySnakeCase turns snake_case into a readable label', () => {
    expect(beautifySnakeCase('user_sign_in')).toBe('User sign in')
    expect(beautifySnakeCase('word')).toBe('Word')
  })

  it('strToSnakeCase converts arbitrary strings to snake_case', () => {
    expect(strToSnakeCase('Hello World')).toBe('hello_world')
    expect(strToSnakeCase('camelCaseValue')).toBe('camel_case_value')
    expect(strToSnakeCase('with-dashes and.dots')).toBe('with_dashes_and_dots')
  })

  it('capitalizeFirstLetter only touches the first character', () => {
    expect(capitalizeFirstLetter('mascope')).toBe('Mascope')
    expect(capitalizeFirstLetter('already Capitalized')).toBe('Already Capitalized')
  })

  it('beautifyConstant converts SNAKE_CASE constants to labels', () => {
    expect(beautifyConstant('FILTER_REGENERATION')).toBe('Filter Regeneration')
    expect(beautifyConstant('ONLINE')).toBe('Online')
    expect(beautifyConstant('')).toBe('')
    expect(beautifyConstant(null)).toBe(null)
  })

  it('norm collapses whitespace and optionally lowercases', () => {
    expect(norm('  Hello   World  ')).toBe('Hello World')
    expect(norm('  Hello   World  ', true)).toBe('hello world')
    expect(norm('')).toBe('')
    expect(norm(null)).toBe('')
  })

  it('prettyTrim truncates long labels with an ellipsis', () => {
    expect(prettyTrim('short')).toBe('short')
    expect(prettyTrim('exactly fifteen')).toBe('exactly fifteen')
    expect(prettyTrim('a very long label indeed')).toBe('a very long lab...')
    expect(prettyTrim('abcdef', 3)).toBe('abc...')
  })
})

describe('misc helpers', () => {
  it('genId produces ids of the requested length and alphabet', () => {
    const id = genId(8)
    expect(id).toHaveLength(8)
    expect(id).toMatch(/^[0-9A-Za-z]{8}$/)
    const upper = genId(12, false)
    expect(upper).toMatch(/^[0-9A-Z]{12}$/)
  })

  it('clone deep-copies plain data', () => {
    const original = { a: 1, nested: { list: [1, 2, 3] } }
    const copy = clone(original)
    expect(copy).toEqual(original)
    copy.nested.list.push(4)
    expect(original.nested.list).toHaveLength(3)
    expect(clone(null)).toBe(null)
  })

  it('debounce fires once after the timeout with the last arguments', () => {
    vi.useFakeTimers()
    const callback = vi.fn()
    const debounced = debounce(callback, 200)
    debounced('first')
    debounced('second')
    vi.advanceTimersByTime(199)
    expect(callback).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(callback).toHaveBeenCalledOnce()
    expect(callback).toHaveBeenCalledWith('second')
    vi.useRealTimers()
  })

  it('instrumentType classifies instruments by name', () => {
    expect(instrumentType('KORBI2')).toBe('orbi')
    expect(instrumentType('KLTOF1')).toBe('tof')
    expect(instrumentType('api-3000')).toBe('tof')
    expect(instrumentType('unrelated')).toBe(null)
    expect(instrumentType(null)).toBe(null)
  })

  it('takes the recorded instrument class of a sample over its name', () => {
    // The converter records the class; a name that does not say is fine.
    expect(sampleInstrumentType({ instrument: 'Test', instrument_type: 'tof' })).toBe('tof')
    // Rows from before the field fall back to the name rule.
    expect(sampleInstrumentType({ instrument: 'KORBI2' })).toBe('orbi')
    expect(sampleInstrumentType({ instrument: 'Test' })).toBe(null)
    expect(sampleInstrumentType(null)).toBe(null)
  })
})
