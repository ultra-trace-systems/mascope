import { computed, nextTick, ref } from 'vue'

/**
 * One popover for a whole table, pointed at the row whose cell opened it.
 *
 * The target is read live by id, so a reload under an open popover (a verdict
 * just recorded, a consensus that moved) shows the row as it now is. Clicking
 * the open row's cell again closes it; another row's cell moves it there.
 *
 * @param {Function} findRow finds a row by id among the rows the popover may
 *   point at
 * @param {Function} idOf a row's id
 * @param {{ beforeOpen?: Function }} [options] run before opening, so an
 *   opener can close another popover: each cell stops its click, which
 *   PrimeVue's outside-click listener would otherwise see
 */
export function useRowPopover(findRow, idOf, { beforeOpen = null } = {}) {
  const menu = ref()
  const rowId = ref(null)
  const target = computed(() => (rowId.value != null ? (findRow(rowId.value) ?? null) : null))

  function open(event, row) {
    const popover = menu.value
    if (!popover) return
    beforeOpen?.()
    const id = idOf(row)
    if (rowId.value === id) {
      popover.toggle(event)
      return
    }
    rowId.value = id
    popover.hide()
    nextTick(() => popover.show(event))
  }

  return { menu, rowId, target, open, hide: () => menu.value?.hide() }
}
