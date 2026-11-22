import { defineStore } from 'pinia'

export const useBatchStatus = defineStore('browser.sample.batchStatus', () => {
  const config = {
    recalibrate: {
      type: 'button',
      icon: 'ph ph-scales',
      tooltip:
        'The calibration collection changed, or the compounds in it did. Click to ' +
        're-calibrate this batch so the new calibration takes effect (afterwards ' +
        'the batch will need re-matching)',
      severity: 'warn'
    },
    rematch: {
      type: 'button',
      icon: 'ph ph-arrows-clockwise',
      tooltip: 'Batch has been modified, matches may be out of date. Click to refresh matches',
      severity: 'secondary'
    },
    ready: {
      type: 'button',
      icon: 'ph ph-check-circle',
      tooltip: 'Batch is up to date',
      severity: 'primary',
      disabled: true
    },
    processing: {
      type: 'spinner',
      tooltip: 'Computing matches for the batch'
    }
  }

  return { config }
})
