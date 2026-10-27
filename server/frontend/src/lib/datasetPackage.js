import { api } from '@/api'

/**
 * A dataset's deposit package, as "Prepare for publication" (right-click a
 * dataset) shows and builds it.
 *
 * The package is one archive of the dataset to upload to a data repository
 * such as Zenodo. The server reads what it would hold without building it
 * (`GET .../export/package/preview`), and builds it in the background
 * (`POST .../export/package`) with the builder the operator command
 * `mascope export package` runs, so the archive is the same either way. The
 * notification that says it is ready names the download, which the app
 * fetches as it does any export's.
 */

const packagePath = ({ workspace_id, dataset_id }) =>
  `/workspaces/${workspace_id}/datasets/${dataset_id}/export/package`

/**
 * What the dataset's package would hold, and what the dataset lacks for a
 * deposit. Failures are left to the caller, which shows them in the dialog,
 * so no error toast repeats them.
 *
 * @param {{workspace_id: string, dataset_id: string}} dataset
 * @returns {Promise<object>}
 */
export async function loadPackagePreview(dataset) {
  const { data } = await api.http.get(`${packagePath(dataset)}/preview`, {
    type: 'dataset_package_preview',
    errors: 'inline'
  })
  return data?.data ?? null
}

/**
 * Build the dataset's package in the background. Progress and the download
 * arrive over the socket, as with the batch spreadsheet.
 *
 * @param {{workspace_id: string, dataset_id: string}} dataset
 */
export const exportPackage = (dataset) =>
  api.http.post(
    packagePath(dataset),
    {},
    {
      use: 'process',
      type: 'export_dataset_package'
    }
  )

/** The gaps the dataset dialog fills in; a dataset of no samples is not one. */
const EDITABLE_GAPS = new Set(['license', 'authors', 'dataset_description'])

/**
 * Whether a gap is filled in from the dataset dialog.
 *
 * @param {{field: string}} gap
 * @returns {boolean}
 */
export const gapEditable = (gap) => EDITABLE_GAPS.has(gap?.field)

const plural = (n, one, many) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`

/**
 * A size in bytes for a person, as the server's notification gives it.
 *
 * @param {number} bytes
 * @returns {string}
 */
export function sizeLabel(bytes) {
  let value = bytes ?? 0
  if (value < 1000) return `${value} B`
  for (const unit of ['kB', 'MB', 'GB']) {
    value /= 1000
    if (value < 1000 || unit === 'GB') return `${value.toFixed(1)} ${unit}`
  }
}

/**
 * How many samples, in words.
 *
 * @param {number} n
 * @returns {string}
 */
export const samplesLabel = (n) => plural(n, 'sample', 'samples')

/**
 * The batches, with how many samples they hold together.
 *
 * @param {{batches: {samples: number}[]}} preview
 * @returns {string}
 */
export function batchesLabel(preview) {
  const batches = preview?.batches ?? []
  const samples = batches.reduce((sum, batch) => sum + batch.samples, 0)
  return `${plural(batches.length, 'batch', 'batches')}, ${samplesLabel(samples)}`
}

/**
 * The raw files the package copies, of those its samples were measured in,
 * and their size.
 *
 * @param {{raw_files: number, raw_files_included: number, raw_bytes: number}} preview
 * @returns {string}
 */
export function rawFilesLabel(preview) {
  const { raw_files: all = 0, raw_files_included: included = 0 } = preview ?? {}
  const count = included === all ? plural(all, 'file', 'files') : `${included} of ${all} files`
  return `${count}, ${sizeLabel(preview?.raw_bytes)}`
}

/**
 * The sample files whose raw file is no longer kept, the first few by name.
 *
 * @param {string[]} names
 * @param {number} [shown]
 * @returns {string}
 */
export function notKeptLabel(names, shown = 3) {
  const list = names ?? []
  const named = list.slice(0, shown).join(', ')
  return list.length > shown ? `${named} and ${list.length - shown} more` : named
}

/**
 * A target collection, with what the dataset used it as.
 *
 * @param {{target_collection_name: string, used_as: string[]}} collection
 * @returns {string}
 */
export const collectionLabel = (collection) =>
  `${collection.target_collection_name} (${collection.used_as.join(', ')})`
