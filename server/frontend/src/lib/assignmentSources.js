// Where a peak assignment row came from - its `source` - declared once.
//
// The values are the API's: `database`, `untargeted`, `manual`, `reagent` and
// `artifact` (the backend's `AssignmentSource` literal, the SDK's `SOURCES`).
// They are code, not words: the inspector's line under the formula printed them
// as they came, and the Peak Browser's source mark said them on hover. Every
// surface that names a source reads it here instead, and the hover lines that
// the inspector and the tier chip each kept a copy of live here once.
//
// `label` is the word shown among other facts, as in the inspector's line under
// the formula; `line` is what the source says on hover. A reagent or an
// artifact row is labelled with its role's own word, as its chip is.
const SOURCE_DEFINITIONS = Object.freeze({
  database: { label: 'list match', line: 'Matched from a target or reference list' },
  untargeted: { label: 'formula search', line: 'Found by the formula search' },
  manual: { label: 'by hand', line: 'Assigned by hand' },
  reagent: {
    label: 'reagent',
    line: 'An ion the ionization source made, not a compound of the sample'
  },
  artifact: { label: 'artifact', line: 'A side lobe of an intense neighbouring peak' }
})

// Own keys only: `in` walks the prototype chain, so a row whose source read
// "constructor" would be taken for a known one.
const isKnown = (source) => Object.prototype.hasOwnProperty.call(SOURCE_DEFINITIONS, source)

/**
 * The word for a source. One this build does not know is shown as it came
 * rather than hidden: a source added on the server is still a fact about the row.
 *
 * @param {string|null} source the source as stored on the row
 * @returns {string|null} its label, or null for a row with no source
 */
export const sourceLabel = (source) => {
  if (source == null || source === '') return null
  return isKnown(source) ? SOURCE_DEFINITIONS[source].label : String(source)
}

/**
 * What a source says on hover, in one line.
 *
 * @param {string|null} source the source as stored on the row
 * @returns {string|null} its line, or null for a source this build does not know
 */
export const sourceLine = (source) => (isKnown(source) ? SOURCE_DEFINITIONS[source].line : null)
