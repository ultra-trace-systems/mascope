import { api } from '@/api'

/**
 * Deleted datasets' tombstones, as the home menu's Deleted datasets tab reads
 * and shows them.
 *
 * A tombstone is the snapshot the server keeps of a dataset when it is
 * deleted, alone or with its workspace (FAIR A2). `GET /datasets/tombstones`
 * answers the rows the caller may read - its workspaces' for a member, every
 * row for a global admin or owner - newest delete first, a page at a time.
 */

/** Rows per page. */
export const TOMBSTONE_PAGE_SIZE = 25

/**
 * One page of tombstones. Failures are left to the caller, which shows them
 * in place of the list, so no error toast repeats them.
 *
 * @param {number} page Zero-based.
 * @param {number} [limit]
 * @returns {Promise<{data: object[], total: number}>}
 */
export async function loadTombstones(page, limit = TOMBSTONE_PAGE_SIZE) {
  const { data } = await api.http.get('/datasets/tombstones', {
    params: { page, limit },
    type: 'dataset_tombstones',
    errors: 'inline'
  })
  return { data: data?.data ?? [], total: data?.total ?? 0 }
}

/**
 * How the dataset went: on its own, or with its workspace.
 *
 * @param {{deleted_via?: string}} row
 * @returns {string}
 */
export function deletedViaLabel(row) {
  return row?.deleted_via === 'workspace' ? 'with its workspace' : 'on its own'
}

/**
 * A timestamp in the browser's locale; a dash for none.
 *
 * @param {string|null|undefined} stamp
 * @returns {string}
 */
export function whenLabel(stamp) {
  if (!stamp) return '-'
  const date = new Date(stamp)
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleString()
}

const plural = (n, one, many) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`

/**
 * What the dataset held: batches, samples and files.
 *
 * @param {{n_sample_batches?: number, n_sample_items?: number, n_sample_files?: number}} row
 * @returns {string}
 */
export function countsLabel(row) {
  return [
    plural(row?.n_sample_batches ?? 0, 'batch', 'batches'),
    plural(row?.n_sample_items ?? 0, 'sample', 'samples'),
    plural(row?.n_sample_files ?? 0, 'file', 'files')
  ].join(', ')
}

const joined = (parts) => parts.filter(Boolean).join(', ')

/**
 * The descriptive metadata a dataset held, as label and value lines in the
 * order the dataset dialog edits it. Fields left empty are left out, so a
 * dataset nobody described yields no lines.
 *
 * @param {object|null|undefined} metadata The tombstone's `descriptive_metadata`.
 * @returns {{label: string, value: string}[]}
 */
export function metadataLines(metadata) {
  const lines = []
  const add = (label, value) => {
    if (value) lines.push({ label, value })
  }
  add('Licence', metadata?.license)
  for (const author of metadata?.authors ?? []) {
    add(
      'Author',
      joined([author?.name, author?.orcid && `ORCID ${author.orcid}`, author?.affiliation])
    )
  }
  add('Keywords', joined(metadata?.keywords ?? []))
  for (const entry of metadata?.related_identifiers ?? []) {
    add(
      'Related',
      entry?.identifier &&
        joined([
          entry.relation,
          entry.identifier_type ? `${entry.identifier_type} ${entry.identifier}` : entry.identifier
        ])
    )
  }
  for (const entry of metadata?.funding ?? []) {
    add('Funding', entry?.funder && joined([entry.funder, entry.award]))
  }
  return lines
}
