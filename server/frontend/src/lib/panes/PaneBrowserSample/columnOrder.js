/**
 * The sample table's configurable columns: the list the customizer keeps, and
 * the two operations on it worth getting right once.
 */

/**
 * The peak-assignment status badge, a configurable column like the rest: it
 * can be moved and hidden through the customizer, and it defaults to the place
 * `withStatusColumn` gives it.
 */
export const STATUS_COLUMN = Object.freeze({
  field: 'assignment_status',
  kind: 'status',
  label: 'Peak assignment status',
  type: 'status'
})

/**
 * The columns with the status badge in its default place - right after the
 * sample name, or first when the name is not shown - unless they hold one
 * already. A stored configuration from before the badge was a column of its
 * own has no entry for it, and would otherwise lose the badge.
 *
 * @param {Array<{field: string, kind: string}>} columns
 * @returns {Array} the same list, or a new one with the badge inserted
 */
export function withStatusColumn(columns) {
  if (columns.some((column) => column.kind === STATUS_COLUMN.kind)) return columns
  const name = columns.findIndex((column) => column.field === 'sample_item_name')
  const at = name < 0 ? 0 : name + 1
  return [...columns.slice(0, at), { ...STATUS_COLUMN }, ...columns.slice(at)]
}

/**
 * The configurable columns after a header drag.
 *
 * PrimeVue reports the drag and drop indices over ALL displayed columns, the
 * first `leading` of which are fixed and not in this list, so both indices are
 * that many ahead of the list. A drag that starts on a fixed column changes
 * nothing; a drop in front of them lands in the first configurable slot.
 *
 * @param {Array} columns - the customizer's list
 * @param {number} dragIndex - where the dragged column was, over all columns
 * @param {number} dropIndex - where it was dropped, over all columns
 * @param {number} leading - how many fixed columns precede the list
 * @returns {Array} a new list, or the same one when nothing moves
 */
export function reorderColumns(columns, dragIndex, dropIndex, leading) {
  const from = dragIndex - leading
  if (from < 0 || from >= columns.length) return columns
  const to = Math.max(0, Math.min(columns.length - 1, dropIndex - leading))
  if (from === to) return columns
  const next = [...columns]
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved)
  return next
}
