/**
 * Presentation model for a raw file's processing status.
 *
 * Auto-processing records on each `sample_file` how far it got
 * (`processing_status`; the values are `ProcessingStatus` in the backend's
 * `api/models/sample/files/config.py`), a sentence or two about what that
 * means for the file (`processing_detail`), and when it was written
 * (`processing_updated_utc`). Files processed before the status existed carry
 * none, and show none.
 */

const IN_PROGRESS = 'Auto-processing is still running.'

/** The statuses of a run still under way (IN_PROGRESS in the backend). */
export const IN_PROGRESS_STATUSES = ['converted', 'queued', 'bound', 'calibrated']

/**
 * How long a run may go without recording a stage before its file counts as
 * stalled (STALLED_AFTER in the backend), which lets processing asked for
 * again take the file over.
 */
export const STALLED_AFTER_MS = 24 * 60 * 60 * 1000

const STALLED = 'Nothing was recorded for over a day: the run has stopped. Re-process the file.'

/** One entry per status the backend writes, in pipeline order. */
export const PROCESSING_STATUSES = {
  converted: {
    label: 'Converted',
    severity: 'secondary',
    icon: 'ph ph-hourglass-medium',
    description: `The converter registered the file. ${IN_PROGRESS}`
  },
  queued: {
    label: 'Queued',
    severity: 'secondary',
    icon: 'ph ph-hourglass-medium',
    description: 'Processing was asked for again and waits for its turn.'
  },
  bound: {
    label: 'Bound',
    severity: 'secondary',
    icon: 'ph ph-hourglass-medium',
    description: `The file's samples exist under their ionization modes. ${IN_PROGRESS}`
  },
  calibrated: {
    label: 'Calibrated',
    severity: 'secondary',
    icon: 'ph ph-hourglass-medium',
    description: `m/z calibrated. ${IN_PROGRESS}`
  },
  needs_chemistry: {
    label: 'Needs a chemistry',
    severity: 'warn',
    icon: 'ph ph-flask',
    description: 'No ionization mode could be bound to the file, so it has no samples yet.',
    // Shown under the file's own reason too, which the detail carries.
    action:
      'Right-click it and choose its chemistry, or fix the ionization mode tokens ' +
      'its name should match and re-process it.'
  },
  calibration_failed: {
    label: 'Calibration failed',
    severity: 'warn',
    icon: 'ph ph-scales',
    description:
      'An m/z calibration failed, fell below the quality bar or could not be made, ' +
      'so some or all of the samples were not matched.'
  },
  done: {
    label: 'Done',
    severity: 'success',
    icon: 'ph ph-check',
    description: 'Every sample of the file was matched, or a blank had nothing to match.'
  },
  failed: {
    label: 'Failed',
    severity: 'danger',
    icon: 'ph ph-x-circle',
    description: 'Processing stopped on an error.'
  }
}

/**
 * Server-side filter choices for the Raw files list. Each status that asks for
 * a person also has a choice of its own, which is what a kept notification's
 * Show files sets.
 */
export const PROCESSING_STATUS_FILTERS = [
  { label: 'Any status', value: null },
  { label: 'Needs attention', value: ['needs_chemistry', 'calibration_failed', 'failed'] },
  { label: 'Needs a chemistry', value: ['needs_chemistry'] },
  { label: 'Calibration failed', value: ['calibration_failed'] },
  { label: 'Failed', value: ['failed'] },
  { label: 'In progress', value: IN_PROGRESS_STATUSES },
  { label: 'Done', value: ['done'] }
]

const formatTime = (iso) => {
  if (!iso) return null
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? null : date.toLocaleString()
}

/**
 * Whether a file's run stopped without saying so: it is in progress, and has
 * recorded nothing for longer than any run takes to record its next stage.
 *
 * @param {object} file - a `sample_file` row
 * @param {number} [now] - the time to judge by, in ms since the epoch
 * @returns {boolean}
 */
export function isStalled(file, now = Date.now()) {
  if (!IN_PROGRESS_STATUSES.includes(file?.processing_status)) return false
  const updated = Date.parse(file.processing_updated_utc)
  return !Number.isNaN(updated) && now - updated > STALLED_AFTER_MS
}

/**
 * Whether files can be given a chemistry: no run is working on any of them.
 *
 * A file that needs one, a file that failed before its samples were made, a
 * file bound wrongly and a file whose run stalled all can.
 *
 * @param {object[]} files - `sample_file` rows, as the list holds them now
 * @returns {boolean}
 */
export const canChooseChemistry = (files) =>
  files.length > 0 &&
  files.every((file) => !IN_PROGRESS_STATUSES.includes(file.processing_status) || isStalled(file))

/**
 * Derive the status tag for a raw file row.
 *
 * An unknown status is shown under its own name rather than dropped: a newer
 * backend may write a value this build has no entry for.
 *
 * @param {object|null|undefined} file - a `sample_file` row
 * @returns {{state: string, label: string, severity: string, icon: string,
 *   tooltip: string}|null} The tag, or null when no status was recorded.
 */
export function processingStatus(file) {
  const state = file?.processing_status
  if (!state) return null
  const meta = PROCESSING_STATUSES[state] ?? {
    label: state,
    severity: 'secondary',
    icon: 'ph ph-question',
    description: ''
  }
  const updated = formatTime(file.processing_updated_utc)
  const tooltip = [
    file.processing_detail || meta.description,
    meta.action,
    isStalled(file) ? STALLED : null,
    updated ? `Recorded ${updated}` : null
  ]
    .filter(Boolean)
    .join('\n')
  return {
    state,
    label: meta.label,
    severity: meta.severity,
    icon: meta.icon,
    tooltip
  }
}
