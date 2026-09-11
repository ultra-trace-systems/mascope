import { describe, it, expect, vi, afterEach } from 'vitest'

// What the product says about itself. The links end up in hrefs on the sign-in
// screen, so what they must get right is falling back to the published defaults
// for a runtime that predates the settings, honouring an operator who hides or
// replaces one, and never rendering anything but a web or mail URL. The version
// helpers decide whether the About tab cries "mismatch", so a channel name
// such as `latest` must never count as one.

vi.mock('@/lib/runtime', () => ({ runtime: { meta: {}, version: 'v0.0.0-runtime' } }))

import { runtime } from '@/lib/runtime'
import {
  builtVersion,
  combineNotices,
  copyrightNotice,
  isBuildVersion,
  legalLinks,
  linkTarget,
  releaseNotesUrl,
  safeHref,
  supportLabel,
  versionReport,
  versionsDiffer
} from '@/lib/about'

const RELEASES = 'https://github.com/ultra-trace-systems/mascope/releases/tag'
const CHANGELOG = 'https://github.com/ultra-trace-systems/mascope/blob/master/CHANGELOG.md'

afterEach(() => {
  runtime.meta = {}
  document.head.querySelectorAll('meta[name="mascope-version"]').forEach((tag) => tag.remove())
})

describe('about: legal links', () => {
  it('defaults to the published privacy notice and support address, with no terms', () => {
    // A runtime published by a CLI that predates the settings carries none of them.
    runtime.meta = {}

    expect(legalLinks()).toEqual({
      privacy: 'https://ultratrace.eu/mascope/privacy',
      terms: null,
      support: 'mailto:support@ultratrace.eu'
    })
  })

  it("follows the deployment's settings, and hides a link set to empty", () => {
    runtime.meta = {
      privacy_notice_url: '',
      terms_url: 'https://example.org/terms',
      support_url: 'https://help.example.org/'
    }

    expect(legalLinks()).toEqual({
      privacy: null,
      terms: 'https://example.org/terms',
      support: 'https://help.example.org/'
    })
  })

  it('never renders anything but a web or mail URL', () => {
    runtime.meta = {
      privacy_notice_url: 'javascript:alert(1)',
      terms_url: '/terms',
      support_url: 'data:text/html,<script>alert(1)</script>'
    }

    expect(legalLinks()).toEqual({ privacy: null, terms: null, support: null })
  })

  it('accepts a mail address for support only', () => {
    runtime.meta = { privacy_notice_url: 'mailto:legal@example.org' }

    expect(legalLinks().privacy).toBeNull()
    expect(safeHref('mailto:help@example.org', ['mailto:'])).toBe('mailto:help@example.org')
  })

  it('opens web pages in a new tab and mail links in the mail client', () => {
    expect(linkTarget('https://example.org/')).toBe('_blank')
    expect(linkTarget('mailto:help@example.org')).toBeUndefined()
  })

  it('shows a mail link as its address', () => {
    expect(supportLabel('mailto:help@example.org?subject=Mascope')).toBe('help@example.org')
    expect(supportLabel('https://help.example.org/')).toBe('https://help.example.org/')
  })
})

describe('about: versions', () => {
  it("reads the bundle's own build from index.html, not the deployment's tag", () => {
    const tag = document.createElement('meta')
    tag.name = 'mascope-version'
    tag.content = 'v1.8.0'
    document.head.appendChild(tag)

    expect(builtVersion()).toBe('v1.8.0')
  })

  it('falls back to the runtime version for a bundle built without one', () => {
    expect(builtVersion()).toBe('v0.0.0-runtime')
  })

  it('tells a build from a channel or nothing', () => {
    expect(isBuildVersion('v1.7.3')).toBe(true)
    expect(isBuildVersion('2026.09.01-abc1234')).toBe(true)
    for (const channel of ['latest', 'unknown', '', ' ', null, undefined]) {
      expect(isBuildVersion(channel), String(channel)).toBe(false)
    }
  })

  it('calls two different builds a mismatch, and nothing else', () => {
    expect(versionsDiffer('v1.7.3', 'v1.7.4')).toBe(true)
    expect(versionsDiffer('v1.7.3', 'v1.7.3')).toBe(false)
    // A deployment tracking `latest` reports the channel; it cannot be compared.
    expect(versionsDiffer('v1.7.3', 'latest')).toBe(false)
    expect(versionsDiffer('v1.7.3', null)).toBe(false)
  })

  it('links a tagged release to its release page and anything else to the changelog', () => {
    expect(releaseNotesUrl('v1.7.3')).toBe(`${RELEASES}/v1.7.3`)
    expect(releaseNotesUrl('v1.8.0-rc.1')).toBe(`${RELEASES}/v1.8.0-rc.1`)
    expect(releaseNotesUrl('2026.09.01-abc1234')).toBe(CHANGELOG)
    expect(releaseNotesUrl('latest')).toBe(CHANGELOG)
    expect(releaseNotesUrl(null)).toBe(CHANGELOG)
  })

  it('reports both builds and the browser for a support request', () => {
    expect(versionReport({ web: 'v1.7.3', server: null, userAgent: 'TestBrowser/1.0' })).toBe(
      'Mascope web app: v1.7.3\nMascope server: unavailable\nBrowser: TestBrowser/1.0'
    )
  })
})

describe('about: notices', () => {
  it('joins the sections in order, each in its own words, and skips empty ones', () => {
    const rule = '='.repeat(79)

    expect(combineNotices(['NOTICE\n\n', '', 'web list\n', null, 'server list'])).toBe(
      `NOTICE\n\n${rule}\n\nweb list\n\n${rule}\n\nserver list\n`
    )
  })
})

describe('about: legal identity', () => {
  it('takes the copyright line from the repository NOTICE', () => {
    expect(copyrightNotice).toMatch(/^© \d{4}(-\d{4})? Ultra Trace Systems Oy$/)
  })
})
