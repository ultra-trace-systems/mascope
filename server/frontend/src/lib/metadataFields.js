// The metadata field registry, as the registry pane reads and writes it.
// The server holds the rules (`/api/metadata/fields`); these helpers word a
// field for the table and turn the pane's form into a request body.

/** The types a registry field may have, in the order the form offers them. */
export const FIELD_TYPES = [
  { value: 'text', label: 'Text' },
  { value: 'number', label: 'Number' },
  { value: 'datetime', label: 'Date and time' },
  { value: 'enum', label: 'One of a list' },
  { value: 'tags', label: 'Tags' }
]

/** The types whose values come from the field's allowed values. */
export const CHOICE_TYPES = new Set(['enum', 'tags'])

/** A key as the server takes one: lower case, a letter first. */
export const KEY_PATTERN = /^[a-z][a-z0-9_]*$/

export const fieldTypeLabel = (type) => FIELD_TYPES.find((t) => t.value === type)?.label ?? type

/**
 * The allowed values as the table shows them.
 *
 * @param {object} field A registry field.
 * @returns {string} The values, comma separated; "Any" for a tags field that
 *   leaves them open; an empty string for a type that has none.
 */
export const allowedValuesLabel = (field) => {
  if (!CHOICE_TYPES.has(field.type)) return ''
  const values = field.allowed_values ?? []
  if (!values.length) return field.type === 'tags' ? 'Any' : ''
  return values.join(', ')
}

/**
 * Suggest a key from a label: what a person typed, in the key's alphabet.
 *
 * @param {string} label
 * @returns {string}
 */
export const keyFromLabel = (label) =>
  (label ?? '')
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^[^a-z]+/, '')
    .replace(/_+$/, '')
    .slice(0, 64)

/**
 * The allowed values typed one per line, blank lines left out.
 *
 * @param {string} text
 * @returns {string[]}
 */
export const parseAllowedValues = (text) =>
  (text ?? '')
    .split('\n')
    .map((value) => value.trim())
    .filter(Boolean)

/** A form for a new field, or for changing `field`. */
export const fieldForm = (field = null) => ({
  key: field?.key ?? '',
  label: field?.label ?? '',
  type: field?.type ?? 'text',
  unit: field?.unit ?? '',
  allowedValues: (field?.allowed_values ?? []).join('\n'),
  description: field?.description ?? ''
})

/**
 * The request body for the form: every field's attribute when creating one,
 * only what changed when changing `original`. A blank unit or description is
 * sent as null, and allowed values only for a type that has them.
 *
 * @param {object} form The pane's form (`fieldForm`).
 * @param {object|null} original The field being changed, or null for a new one.
 * @returns {object}
 */
export const fieldBody = (form, original = null) => {
  const body = {
    label: form.label.trim(),
    type: form.type,
    unit: form.unit.trim() || null,
    description: form.description.trim() || null,
    allowed_values: CHOICE_TYPES.has(form.type) ? parseAllowedValues(form.allowedValues) : null
  }
  if (!original) return { key: form.key.trim(), ...body }
  return Object.fromEntries(
    Object.entries(body).filter(
      ([name, value]) => JSON.stringify(value) !== JSON.stringify(original[name] ?? null)
    )
  )
}

/**
 * What a form still lacks before it can be saved, or null when nothing.
 * The server checks the same and more; this keeps an obvious gap from a
 * round trip.
 *
 * @param {object} form
 * @param {boolean} creating
 * @returns {string|null}
 */
export const formProblem = (form, creating) => {
  if (creating && !KEY_PATTERN.test(form.key.trim()))
    return 'The key is lower case letters, digits and underscores, a letter first.'
  if (!form.label.trim()) return 'A field needs a label.'
  if (form.type === 'enum' && !parseAllowedValues(form.allowedValues).length)
    return 'A field of one of a list needs at least one allowed value.'
  return null
}

/**
 * Why a field cannot be deleted, or null when it can.
 *
 * @param {object} field
 * @returns {string|null}
 */
export const deleteBlocked = (field) => {
  if (field.system) return 'Mascope ships this field: it stays.'
  if (field.in_use) return 'In use: a template selects it. Retire it instead.'
  return null
}
