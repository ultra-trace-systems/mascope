import { describe, it, expect } from 'vitest'

import {
  STATUS_COLUMN,
  reorderColumns,
  withStatusColumn
} from '@/lib/panes/PaneBrowserSample/columnOrder.js'

const column = (field, kind = 'standard') => ({ field, kind, label: field, type: 'string' })
const fields = (columns) => columns.map((c) => c.field)

describe('withStatusColumn', () => {
  it('puts the badge right after the sample name', () => {
    const columns = [column('index'), column('sample_item_name'), column('filter_id')]
    expect(fields(withStatusColumn(columns))).toEqual([
      'index',
      'sample_item_name',
      'assignment_status',
      'filter_id'
    ])
  })

  it('puts it first when the name is not shown', () => {
    expect(fields(withStatusColumn([column('index')]))).toEqual(['assignment_status', 'index'])
  })

  // A configuration that already places the badge - wherever the user put it,
  // or wherever a stored one had it - is left alone, list identity included.
  it('leaves a list that has one alone', () => {
    const columns = [{ ...STATUS_COLUMN }, column('sample_item_name')]
    expect(withStatusColumn(columns)).toBe(columns)
  })

  it('does not mutate the list it is given', () => {
    const columns = [column('sample_item_name')]
    withStatusColumn(columns)
    expect(fields(columns)).toEqual(['sample_item_name'])
  })
})

describe('reorderColumns', () => {
  const columns = [column('a'), column('b'), column('c'), column('d')]
  // Two fixed badge columns precede the list on screen.
  const LEADING = 2

  it("maps the table's indices past the fixed columns", () => {
    // Column 'a' (displayed third) dragged to where 'c' is (displayed fifth).
    expect(fields(reorderColumns(columns, 2, 4, LEADING))).toEqual(['b', 'c', 'a', 'd'])
    // And back the other way.
    expect(fields(reorderColumns(columns, 5, 2, LEADING))).toEqual(['d', 'a', 'b', 'c'])
  })

  it('lands a drop in front of the fixed columns in the first slot', () => {
    expect(fields(reorderColumns(columns, 4, 0, LEADING))).toEqual(['c', 'a', 'b', 'd'])
  })

  it('changes nothing for a drag that starts on a fixed column, or goes nowhere', () => {
    expect(reorderColumns(columns, 1, 3, LEADING)).toBe(columns)
    expect(reorderColumns(columns, 3, 3, LEADING)).toBe(columns)
    expect(reorderColumns(columns, 9, 3, LEADING)).toBe(columns)
  })

  it('does not mutate the list it is given', () => {
    reorderColumns(columns, 2, 4, LEADING)
    expect(fields(columns)).toEqual(['a', 'b', 'c', 'd'])
  })
})
