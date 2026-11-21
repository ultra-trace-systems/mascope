import { inject, provide } from 'vue'

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
