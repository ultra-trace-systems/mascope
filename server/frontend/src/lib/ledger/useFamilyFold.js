import { reactive, watch } from 'vue'

/**
 * Which isotopologue families of a ledger are unfolded
 * (docs/dev/assignment_ledger_and_card.md, section 3.3).
 *
 * Show > Isotopologue lines sets every family's state at once; the caret on a
 * family's row then folds or unfolds that one family against it. The families
 * the reader flipped are held as the ones that differ from Show's choice, so a
 * re-sort or a filter keeps them, and moving Show's switch starts every family
 * over from the new choice.
 *
 * @param {import('vue').Ref<boolean>} all - Show > Isotopologue lines
 * `open` unfolds one family and leaves an open one open: what the ledger does
 * for the family a hand assignment wrote into, where a toggle would shut a
 * family the reader had open.
 *
 * @returns {{ isOpen: (id: string) => boolean, toggle: (id: string) => void,
 *   open: (id: string) => void }}
 */
export function useFamilyFold(all) {
  const flipped = reactive(new Set())
  watch(all, () => flipped.clear())
  const isOpen = (id) => Boolean(all.value) !== flipped.has(id)
  const toggle = (id) => {
    if (flipped.has(id)) flipped.delete(id)
    else flipped.add(id)
  }
  const open = (id) => {
    if (!isOpen(id)) toggle(id)
  }
  return { isOpen, toggle, open }
}
