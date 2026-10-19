/**
 * What a notification is headed, as a toast and in the log, and which
 * notifications earn a toast at all.
 *
 * A notification's `type` names the operation it reports, in the snake case
 * the API knows it by: `shared_link`, `match_compositions_by_mz`,
 * `calibration_mz_fit`. The common events get a heading written for the
 * reader; anything else is headed by that name beautified, with the status
 * word after it, so a new operation is never headed by nothing. The toast, the
 * notification log and the progress bar all read from here, so they name an
 * event alike.
 */

import { beautifySnakeCase, messageSeverity } from '@/lib/utils'

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

// How long a toast stays, by status. An error long enough to read twice;
// anything else is good news or a progress note.
const LIFE_MS = { error: 10000, warning: 7000 }
const DEFAULT_LIFE_MS = 3000

/**
 * The name of the operation a notification type reports, for a reader.
 *
 * @param {string} type The notification type.
 * @returns {string}
 */
export function notificationTitle(type) {
  return TOAST_TITLES[type] ?? beautifySnakeCase(type ?? 'notification')
}

/**
 * The heading of a notification, with its outcome where that is the news.
 *
 * @param {string} type The notification type.
 * @param {string} status The notification status: success, info, warning or error.
 * @returns {string}
 */
export function toastTitle(type, status) {
  const title = TOAST_TITLES[type]
  if (!title) return `${notificationTitle(type)} ${status}`
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

/**
 * The toast a notification gets: what the toaster is handed, or null for one
 * that shows no toast - a pending progress report, which the progress bar
 * carries, or a notification `toastWanted` leaves out.
 *
 * @param {object} notification The notification, as the store hands it on.
 * @returns {{severity: string, summary: string, detail: string, life: number}|null}
 */
export function toastFor(notification) {
  const { type, status, message } = notification ?? {}
  if (!status || status === 'pending' || !toastWanted(notification)) return null
  return {
    severity: messageSeverity(status),
    summary: toastTitle(type, status),
    detail: message,
    life: LIFE_MS[status] ?? DEFAULT_LIFE_MS
  }
}
