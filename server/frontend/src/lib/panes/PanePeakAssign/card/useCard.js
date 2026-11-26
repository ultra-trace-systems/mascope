import { computed, inject, provide, ref } from 'vue'

/** The key the card's model is provided under. */
const CARD = Symbol('assignment-card')

/**
 * Hands the card's model (`useAssignmentCard`) to the sections under it.
 *
 * @param {object} model
 */
export function provideCard(model) {
  provide(CARD, model)
}

/**
 * The card's model, in a section of the card. A section destructures what it
 * shows, so its template reads the model's refs as its own bindings.
 *
 * @returns {object}
 */
export function useCard() {
  const model = inject(CARD, null)
  if (!model) throw new Error('A card section was mounted outside PanePeakAssign')
  return model
}

/**
 * A folded section's open state, kept in the card's model under `key`
 * (`sectionFolds`), so it outlives the section: the card takes its sections
 * out of the page while a row's detail loads, and a click on an isotope line
 * loads one. Outside a card, or with no key, the state is the section's own.
 *
 * @param {string|null} key
 * @returns {import('vue').Ref<boolean>}
 */
export function useSectionOpen(key) {
  const folds = inject(CARD, null)?.sectionFolds
  if (!folds || !key) return ref(false)
  return computed({
    get: () => folds[key] ?? false,
    set: (open) => {
      folds[key] = open
    }
  })
}
