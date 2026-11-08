// Fetch a finished background export from the user's temp store, or the one
// sample file a download names from where the filestore keeps it.
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

/**
 * The source data route's URL for the sample file `sampleFileId`: its raw or
 * h5 file, sent from where the filestore keeps it.
 *
 * @param {string} sampleFileId - the sample file id the notification carries
 * @returns {string}
 */
export const sourceDownloadUrl = (sampleFileId) =>
  `${runtime.api_path}/api/sample/files/${encodeURIComponent(sampleFileId)}/raw`

/**
 * Start the browser's download of a sample file's source data file. The route
 * names the file in its `Content-Disposition`, so the link names none.
 *
 * @param {string} sampleFileId - the sample file id the notification carries
 */
export const downloadSourceFile = (sampleFileId) => {
  const link = document.createElement('a')
  link.download = ''
  link.href = sourceDownloadUrl(sampleFileId)
  link.click()
}

/**
 * Start the download a finished task's notification names, if it names one:
 * a temp file (`download`) or, for a single sample file, its source data file
 * (`source_download`), which is not copied into the temp store. A warning
 * carries them in its error's detail.
 *
 * @param {object} notification - the task's notification
 */
export const downloadFromNotification = ({ status, data, error }) => {
  if (status !== 'success' && status !== 'warning') return
  const download = data?.download ?? error?.detail?.data?.download
  const source = data?.source_download ?? error?.detail?.data?.source_download
  if (download) downloadTempFile(download)
  else if (source) downloadSourceFile(source)
}
