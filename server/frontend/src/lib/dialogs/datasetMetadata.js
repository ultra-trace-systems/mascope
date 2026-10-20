/**
 * The dataset's descriptive metadata as the dataset dialog edits it: a form
 * built from a dataset, and the request fields built back from the form.
 *
 * Every field is optional. A row the user left without its required part (an
 * author's name, an identifier or its relation, a funder) is dropped rather
 * than sent: the server would refuse it, and an empty row is not something
 * the user meant to save. Blank optional parts go as null.
 */

/** Licences the dialog suggests; any other string is accepted too. */
export const LICENSE_SUGGESTIONS = ['CC-BY-4.0', 'CC0-1.0', 'CC-BY-NC-4.0', 'proprietary']

/** The DataCite relationType values a dataset most often needs. */
export const RELATION_SUGGESTIONS = [
  'IsSupplementTo',
  'IsDescribedBy',
  'IsDerivedFrom',
  'Cites',
  'References',
  'IsPartOf'
]

export const IDENTIFIER_TYPE_SUGGESTIONS = ['DOI', 'URL']

/** An ORCID iD as the server checks it: format only, no check digit. */
export const ORCID_PATTERN = /^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$/

/** The width of the dataset's licence column. */
export const LICENSE_MAX_LENGTH = 64

export const METADATA_FIELDS = ['license', 'authors', 'keywords', 'related_identifiers', 'funding']

export const emptyAuthor = () => ({ name: '', orcid: '', affiliation: '' })
export const emptyRelatedIdentifier = () => ({
  identifier: '',
  relation: '',
  identifier_type: ''
})
export const emptyFunding = () => ({ funder: '', award: '' })

/**
 * An editable copy of a dataset's metadata; empty for a new dataset.
 *
 * @param {object|null|undefined} dataset
 * @returns {{license: string, authors: object[], keywords: string[],
 *   related_identifiers: object[], funding: object[]}}
 */
export function metadataForm(dataset) {
  const rows = (list, empty) =>
    (list ?? []).map((entry) => {
      const row = empty()
      for (const key of Object.keys(row)) row[key] = entry?.[key] ?? ''
      return row
    })
  return {
    license: dataset?.license ?? '',
    authors: rows(dataset?.authors, emptyAuthor),
    keywords: [...(dataset?.keywords ?? [])],
    related_identifiers: rows(dataset?.related_identifiers, emptyRelatedIdentifier),
    funding: rows(dataset?.funding, emptyFunding)
  }
}

const text = (value) => (typeof value === 'string' ? value.trim() : '')
const optional = (value) => text(value) || null

/**
 * The request fields for a form: every field present, null when empty, so an
 * update clears what the user emptied.
 *
 * @param {ReturnType<typeof metadataForm>} form
 * @returns {object}
 */
export function metadataPayload(form) {
  const list = (entries) => (entries.length ? entries : null)
  return {
    license: optional(form.license),
    authors: list(
      form.authors
        .filter((author) => text(author.name))
        .map((author) => ({
          name: text(author.name),
          orcid: optional(author.orcid),
          affiliation: optional(author.affiliation)
        }))
    ),
    keywords: list(form.keywords.map(text).filter(Boolean)),
    related_identifiers: list(
      form.related_identifiers
        .filter((entry) => text(entry.identifier) && text(entry.relation))
        .map((entry) => ({
          identifier: text(entry.identifier),
          relation: text(entry.relation),
          identifier_type: optional(entry.identifier_type)
        }))
    ),
    funding: list(
      form.funding
        .filter((entry) => text(entry.funder))
        .map((entry) => ({ funder: text(entry.funder), award: optional(entry.award) }))
    )
  }
}

/**
 * Whether an ORCID field holds something the server would refuse. A blank
 * one is fine: the iD is optional.
 *
 * @param {string|null|undefined} orcid
 * @returns {boolean}
 */
export function orcidInvalid(orcid) {
  const value = text(orcid)
  return value !== '' && !ORCID_PATTERN.test(value)
}

/**
 * The keywords with one more, trimmed; unchanged for a blank or a repeated one.
 *
 * @param {string[]} keywords
 * @param {string} keyword
 * @returns {string[]}
 */
export function addKeyword(keywords, keyword) {
  const value = text(keyword)
  if (!value || keywords.includes(value)) return keywords
  return [...keywords, value]
}

/**
 * Whether the form holds anything the server would refuse, so the dialog can
 * hold its save button back instead of sending it.
 *
 * @param {ReturnType<typeof metadataForm>} form
 * @returns {boolean}
 */
export function metadataInvalid(form) {
  return (
    text(form.license).length > LICENSE_MAX_LENGTH ||
    form.authors.some((author) => orcidInvalid(author.orcid))
  )
}

/**
 * The list with the entry at `index` moved one place up; unchanged for the
 * first entry. Author order is the order a citation lists them in.
 *
 * @param {object[]} entries
 * @param {number} index
 * @returns {object[]}
 */
export function moveUp(entries, index) {
  if (index <= 0 || index >= entries.length) return entries
  const moved = [...entries]
  ;[moved[index - 1], moved[index]] = [moved[index], moved[index - 1]]
  return moved
}
