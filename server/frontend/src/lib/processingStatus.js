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
    description:
      'No ionization mode could be bound to the file, so it has no samples. ' +
      'Set an ionization mode token that its name contains, then re-process it.'
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

/** Server-side filter choices for the Raw files list. */
export const PROCESSING_STATUS_FILTERS = [
  { label: 'Any status', value: null },
  { label: 'Needs attention', value: ['needs_chemistry', 'calibration_failed', 'failed'] },
  { label: 'In progress', value: ['converted', 'queued', 'bound', 'calibrated'] },
  { label: 'Done', value: ['done'] }
]

const formatTime = (iso) => {
  if (!iso) return null
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? null : date.toLocaleString()
}

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
