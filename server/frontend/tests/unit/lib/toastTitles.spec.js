import { describe, it, expect } from 'vitest'

import {
  TOAST_TITLES,
  notificationTitle,
  toastFor,
  toastTitle,
  toastWanted
} from '@/lib/toastTitles'

// Every toast used to be headed by its notification type beautified and the
// status word: "Shared link success", "Match compositions by mz success",
// "Calibration mz fit success". That reads like a log line. The common events
// get a heading written for the reader; the rest keep the generated form.

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

  // A type the map does not know is headed the way every toast used to be,
  // so a new operation is never headed by nothing.
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

describe('whether a toast is shown at all', () => {
  // The search pane says "No results found" where the results would be; a
  // toast saying "No matching compositions found" on top of it is the same
  // news twice.
  it('spares a composition search that found nothing', () => {
    expect(
      toastWanted({
        type: 'match_compositions_by_mz',
        status: 'success',
        data: { results: 0, total: 0 }
      })
    ).toBe(false)
  })

  it('shows a composition search that found something', () => {
    expect(
      toastWanted({ type: 'match_compositions_by_mz', status: 'success', data: { results: 12 } })
    ).toBe(true)
  })

  // A failure carries no result count, and is news whatever the pane shows.
  it('shows a composition search that failed', () => {
    expect(toastWanted({ type: 'match_compositions_by_mz', status: 'error' })).toBe(true)
    expect(toastWanted({ type: 'match_compositions_by_mz', status: 'warning', data: {} })).toBe(
      true
    )
  })

  it('shows everything else', () => {
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
      toastFor({
        type: 'match_compositions_by_mz',
        status: 'success',
        message: 'No matching compositions found',
        data: { results: 0 }
      })
    ).toBeNull()
  })

  it('is none for nothing', () => {
    expect(toastFor(null)).toBeNull()
    expect(toastFor({})).toBeNull()
  })
})
