/**
 * What a notification toast is headed, and whether it is shown at all.
 *
 * A notification's `type` names the operation it reports, in the snake case
 * the API knows it by, and the toaster used to head every toast with that name
 * and the status word after it: "Shared link success", "Match compositions by
 * mz success", "Calibration mz fit success". The common events get a heading
 * written for the reader instead. Anything else keeps the generated form, so a
 * new operation is never headed by nothing.
 */

import { beautifySnakeCase } from '@/lib/utils'

/** Headings by notification type, for the events a user meets every day. */
export const TOAST_TITLES = {
  shared_link: 'Share link',
  match_compositions_by_mz: 'Composition search',
  match_compositions_result: 'Composition search',
  calibration_mz_fit: 'Calibration fit',
  calibration_mz_apply: 'Calibration',
  calibration_mz_calibrate_sample: 'Calibration',
  calibration_mz_calibrate_samples: 'Calibration',
  mz_calibration: 'Calibration',
  recalibrate_batch: 'Recalibration',
  rematch_batch: 'Match refresh',
  rematch_batches: 'Match refresh',
  rematch_sample: 'Match refresh',
  rematch_dataset: 'Match refresh',
  match_compute_batch: 'Match refresh',
  match_compute_sample: 'Match refresh',
  match_aggregate_batch: 'Match refresh',
  sample_file_upload: 'Upload',
  download_sample_files: 'Download',
  file_processing: 'Processing',
  auto_process_sample_file: 'Processing',
  compute_sample_file_peaks: 'Processing',
  process_sample_item: 'Processing',
  reprocess_sample_files: 'Processing',
  import_sample_items: 'Processing'
}

// What follows a mapped heading. A success or an info needs no word for it:
// the heading names the event, and the detail says what came of it.
const STATUS_WORDS = { success: '', info: '', warning: 'warning', error: 'failed' }

/**
 * The heading of a notification's toast.
 *
 * @param {string} type The notification type.
 * @param {string} status The notification status: success, info, warning or error.
 * @returns {string}
 */
export function toastTitle(type, status) {
  const title = TOAST_TITLES[type]
  if (!title) return `${beautifySnakeCase(type ?? 'notification')} ${status}`
  const word = STATUS_WORDS[status] ?? status
  return word ? `${title} ${word}` : title
}

/**
 * Whether a notification earns a toast at all.
 *
 * A composition search that found nothing is reported as a success with no
 * results. The search pane says "No results found" where the results would
 * be, so a toast on top of that says the same thing twice; the notification
 * is still logged. Anything else is shown.
 *
 * @param {object} notification The notification, as the store hands it on.
 * @returns {boolean}
 */
export function toastWanted({ type, status, data } = {}) {
  if (type === 'match_compositions_by_mz' && status === 'success' && data?.results === 0) {
    return false
  }
  return true
}
