import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/runtime.js', () => ({ runtime: { api_path: 'http://api.test' } }))

import {
  downloadFromNotification,
  downloadTempFile,
  sourceDownloadUrl,
  tempDownloadUrl
} from '@/lib/tempDownload'

describe('tempDownloadUrl', () => {
  it('leaves a plain name as it is', () => {
    expect(tempDownloadUrl('20261027_batch.xlsx')).toBe(
      'http://api.test/api/temp/20261027_batch.xlsx'
    )
  })

  it('keeps a # in the path instead of starting the fragment', () => {
    const url = new URL(tempDownloadUrl('20261027_Run_#3.xlsx'))
    expect(url.hash).toBe('')
    expect(url.pathname).toBe('/api/temp/20261027_Run_%233.xlsx')
    expect(decodeURIComponent(url.pathname.split('/').pop())).toBe('20261027_Run_#3.xlsx')
  })

  it('keeps a % followed by hex digits from decoding into another name', () => {
    const url = new URL(tempDownloadUrl('20261027_100%41.xlsx'))
    expect(decodeURIComponent(url.pathname.split('/').pop())).toBe('20261027_100%41.xlsx')
  })
})

describe('downloadTempFile', () => {
  afterEach(() => vi.restoreAllMocks())

  it('clicks a link to the encoded URL, saving under the plain name', () => {
    const clicked = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {
      clicked.push({ href: this.href, download: this.download })
    })
    downloadTempFile('20261027_Run_#3.xlsx')
    expect(clicked).toEqual([
      {
        href: 'http://api.test/api/temp/20261027_Run_%233.xlsx',
        download: '20261027_Run_#3.xlsx'
      }
    ])
  })
})

describe('sourceDownloadUrl', () => {
  it('points at the sample file source data route', () => {
    expect(sourceDownloadUrl('abc123')).toBe('http://api.test/api/sample/files/abc123/raw')
  })

  it('encodes the id as one path segment', () => {
    expect(sourceDownloadUrl('a/../b#c')).toBe(
      'http://api.test/api/sample/files/a%2F..%2Fb%23c/raw'
    )
  })
})

describe('downloadFromNotification', () => {
  // `download` reads '' on a link without the attribute too, so whether the
  // link has it is recorded on its own.
  const clicks = () => {
    const clicked = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {
      clicked.push({
        href: this.href,
        download: this.download,
        hasDownload: this.hasAttribute('download')
      })
    })
    return clicked
  }

  afterEach(() => vi.restoreAllMocks())

  it('fetches a single sample file from where it is stored, not from temp', () => {
    const clicked = clicks()
    downloadFromNotification({
      status: 'success',
      data: { download: null, source_download: 'abc123' }
    })
    // An empty download attribute: the route's Content-Disposition names it,
    // and an error answer is not opened in the app's own tab.
    expect(clicked).toEqual([
      { href: 'http://api.test/api/sample/files/abc123/raw', download: '', hasDownload: true }
    ])
  })

  it('fetches a zip of several files from temp', () => {
    const clicked = clicks()
    downloadFromNotification({
      status: 'success',
      data: { download: 'sample_files_x.zip', source_download: null }
    })
    expect(clicked).toEqual([
      {
        href: 'http://api.test/api/temp/sample_files_x.zip',
        download: 'sample_files_x.zip',
        hasDownload: true
      }
    ])
  })

  it('reads a warning download from its error detail', () => {
    const clicked = clicks()
    downloadFromNotification({
      status: 'warning',
      error: { detail: { data: { download: 'sample_files_x.zip' } } }
    })
    expect(clicked.map(({ href }) => href)).toEqual(['http://api.test/api/temp/sample_files_x.zip'])
  })

  it('starts nothing for a failed or pending task', () => {
    const clicked = clicks()
    downloadFromNotification({ status: 'error', data: { source_download: 'abc123' } })
    downloadFromNotification({ status: 'pending', data: { download: 'a.zip' } })
    expect(clicked).toEqual([])
  })
})
