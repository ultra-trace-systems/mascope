/**
 * The ionization mechanisms a composition search of a sample starts from.
 *
 * They are its ionization mode's. A sample need not have a mode the browser
 * holds, though: an acquisition filed without a chemistry has none, deleting a
 * mode leaves its samples with none, and a mode deleted since the list was
 * loaded is one a sample still points at. Such a sample has no mechanisms to
 * search under, which is an empty answer rather than an error in whoever
 * asks - the pane then waits for mechanisms to be picked by hand.
 *
 * A mechanism the mode names and the browser does not hold is left out for the
 * same reason: everything downstream reads the mechanism's own fields.
 *
 * @param {Object|null} sample the focused sample
 * @param {Object[]} modes the ionization modes the browser holds
 * @param {Object[]} mechanisms the ionization mechanisms the browser holds
 * @returns {Object[]} the sample's mechanisms, in its mode's order
 */
export function mechanismsOfSample(sample, modes, mechanisms) {
  const modeId = sample?.ionization_mode_id
  if (modeId == null) return []
  const mode = modes.find((candidate) => candidate.ionization_mode_id === modeId)
  return (mode?.ionization_mechanism_ids ?? [])
    .map((id) => mechanisms.find((mechanism) => mechanism.ionization_mechanism_id === id))
    .filter(Boolean)
}
