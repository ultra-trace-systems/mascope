import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/runtime.js', () => ({ runtime: { api_path: 'http://api.test' } }))

import { downloadTempFile, tempDownloadUrl } from '@/lib/tempDownload'

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
