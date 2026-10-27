import { describe, it, expect } from 'vitest'

import {
  TOAST_TITLES,
  notificationSeverity,
  notificationTitle,
  toastFor,
  toastTitle,
  toastWanted
} from '@/lib/toastTitles'

// A toast is headed by what its notification reports. The common events get a
// heading written for the reader; anything else is headed by its type
// beautified and the status word ("Update target compound success"), which
// reads like a log line but is never nothing.

describe('the heading of a toast', () => {
  it.each([
    ['shared_link', 'Share link'],
    ['match_compositions_by_mz', 'Composition search'],
    ['calibration_mz_fit', 'Calibration fit'],
    ['rematch_batch', 'Match refresh'],
    ['sample_file_upload', 'Upload'],
    ['file_processing', 'Processing']
  ])('names a %s by what it is to the reader', (type, title) => {
    expect(toastTitle(type, 'success')).toBe(title)
  })

  // A success needs no word for it: the heading names the event, the detail
  // says what came of it. "Share link success" said nothing "Share link" with
  // "Link to this view copied to clipboard" under it does not.
  it('adds no status word to a success or an info', () => {
    expect(toastTitle('sample_file_upload', 'success')).toBe('Upload')
    expect(toastTitle('sample_file_upload', 'info')).toBe('Upload')
  })

  it('says when a mapped event went wrong', () => {
    expect(toastTitle('calibration_mz_fit', 'warning')).toBe('Calibration fit warning')
    expect(toastTitle('match_compositions_by_mz', 'error')).toBe('Composition search failed')
  })

  // A type the map does not know keeps the generated form, so a new operation
  // is never headed by nothing.
  it('falls back to the generated form for anything else', () => {
    expect(toastTitle('update_target_compound', 'success')).toBe('Update target compound success')
    expect(toastTitle('verify_assignment', 'error')).toBe('Verify assignment error')
  })

  it('copes with a notification of no type', () => {
    expect(toastTitle(undefined, 'error')).toBe('Notification error')
  })

  it('maps only to headings fit for the top of a toast', () => {
    for (const title of Object.values(TOAST_TITLES)) {
      expect(title).toMatch(/^[A-Z][a-z]+( [a-z]+)*$/)
    }
  })
})

describe('the severity of a notification', () => {
  // A search that found nothing went right, but there is nothing to be
  // pleased about: it is an info, not a green success.
  it('is info for a composition search that found nothing', () => {
    expect(
      notificationSeverity({
        type: 'match_compositions_by_mz',
        status: 'success',
        data: { results: 0, total: 0 }
      })
    ).toBe('info')
  })

  it('is success for a composition search that found something', () => {
    expect(
      notificationSeverity({
        type: 'match_compositions_by_mz',
        status: 'success',
        data: { results: 12 }
      })
    ).toBe('success')
  })

  // A failure carries no result count, and keeps the weight of its status.
  it('keeps the status of a composition search that failed', () => {
    expect(notificationSeverity({ type: 'match_compositions_by_mz', status: 'error' })).toBe(
      'error'
    )
    expect(
      notificationSeverity({ type: 'match_compositions_by_mz', status: 'warning', data: {} })
    ).toBe('warn')
  })

  it('follows the status for everything else', () => {
    expect(
      notificationSeverity({ type: 'rematch_batch', status: 'success', data: { results: 0 } })
    ).toBe('success')
    expect(notificationSeverity({ type: 'shared_link', status: 'warning' })).toBe('warn')
    expect(notificationSeverity()).toBeUndefined()
  })
})

describe('whether a toast is shown at all', () => {
  // A pane that renders the outcome where the user is looking has claimed it;
  // a toast on top of that is the same news twice.
  it('spares a claimed outcome', () => {
    expect(toastWanted({ type: 'calibration_mz_fit', status: 'success', claimed: true })).toBe(
      false
    )
    expect(toastWanted({ type: 'calibration_mz_fit', status: 'warning', claimed: true })).toBe(
      false
    )
    expect(
      toastWanted({
        type: 'match_compositions_by_mz',
        status: 'success',
        claimed: true,
        data: { results: 0 }
      })
    ).toBe(false)
  })

  // A failure is news whoever claimed it.
  it('shows a claimed outcome that failed', () => {
    expect(toastWanted({ type: 'calibration_mz_fit', status: 'error', claimed: true })).toBe(true)
  })

  // The pane claims its search once the launch is acknowledged, and an empty
  // search can finish first; it says "No results found" all the same.
  it('spares an empty composition search even unclaimed', () => {
    expect(
      toastWanted({ type: 'match_compositions_by_mz', status: 'success', data: { results: 0 } })
    ).toBe(false)
  })

  it('shows an unclaimed outcome', () => {
    expect(
      toastWanted({ type: 'match_compositions_by_mz', status: 'success', data: { results: 12 } })
    ).toBe(true)
    expect(
      toastWanted({ type: 'match_compositions_by_mz', status: 'warning', data: { results: 0 } })
    ).toBe(true)
    expect(toastWanted({ type: 'rematch_batch', status: 'success', data: { results: 0 } })).toBe(
      true
    )
    expect(toastWanted({ type: 'shared_link', status: 'success' })).toBe(true)
    expect(toastWanted()).toBe(true)
  })
})

describe('the name of an event', () => {
  // What the progress bar's tooltip leads with: no status, since the bar is
  // the status.
  it('is the mapped heading, or the type beautified', () => {
    expect(notificationTitle('calibration_mz_fit')).toBe('Calibration fit')
    expect(notificationTitle('update_target_compound')).toBe('Update target compound')
  })
})

// What App.vue hands the toaster, whole, so that the wiring there is one line
// with nothing left to get wrong.
describe('the toast a notification gets', () => {
  it('carries the heading, the message, the severity and a life by status', () => {
    expect(
      toastFor({ type: 'sample_file_upload', status: 'success', message: 'Uploaded 1 file' })
    ).toEqual({ severity: 'success', summary: 'Upload', detail: 'Uploaded 1 file', life: 3000 })
    expect(toastFor({ type: 'rematch_batch', status: 'warning', message: 'm' })).toMatchObject({
      severity: 'warn',
      summary: 'Match refresh warning',
      life: 7000
    })
    expect(toastFor({ type: 'shared_link', status: 'error', message: 'm' })).toMatchObject({
      severity: 'error',
      summary: 'Share link failed',
      life: 10000
    })
    expect(toastFor({ type: 'read_params', status: 'info', message: 'm' })).toMatchObject({
      severity: 'info',
      summary: 'Read params info',
      life: 3000
    })
  })

  // The progress bar carries a pending report; a toast would say it twice.
  it('is none for a pending report', () => {
    expect(toastFor({ type: 'sample_file_upload', status: 'pending', message: '50%' })).toBeNull()
  })

  it('is none where no toast is wanted', () => {
    expect(
      toastFor({ type: 'calibration_mz_fit', status: 'success', message: 'm', claimed: true })
    ).toBeNull()
    expect(
      toastFor({
        type: 'match_compositions_by_mz',
        status: 'success',
        message: 'No matching compositions found',
        data: { results: 0 }
      })
    ).toBeNull()
  })

  it('is still an error toast for a claimed failure', () => {
    expect(
      toastFor({ type: 'calibration_mz_fit', status: 'error', message: 'm', claimed: true })
    ).toMatchObject({ severity: 'error', summary: 'Calibration fit failed', life: 10000 })
  })

  it('is none for nothing', () => {
    expect(toastFor(null)).toBeNull()
    expect(toastFor({})).toBeNull()
  })
})
