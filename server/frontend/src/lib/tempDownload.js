// Fetch a finished background export from the user's temp store.
//
// An export's file name is built from names a user typed (a batch, a sample, a
// dataset: `download_name` in the backend's temp storage), and keeps every
// character a file system accepts, `#` and `%` among them. Put into a URL as
// it is, `#` starts the fragment and the browser requests a cut-off path, and a
// `%` with two hex digits after it is decoded into another name: either way the
// route answers 404. The name is one path segment, so it is encoded as one;
// the file is still saved under the plain name.
import { runtime } from '@/lib/runtime.js'

/**
 * The download route's URL for the temp file `name`.
 *
 * @param {string} name - the file name the export notification carries
 * @returns {string}
 */
export const tempDownloadUrl = (name) => `${runtime.api_path}/api/temp/${encodeURIComponent(name)}`

/**
 * Start the browser's download of the temp file `name`.
 *
 * @param {string} name - the file name the export notification carries
 */
export const downloadTempFile = (name) => {
  const link = document.createElement('a')
  link.download = name
  link.href = tempDownloadUrl(name)
  link.click()
}
