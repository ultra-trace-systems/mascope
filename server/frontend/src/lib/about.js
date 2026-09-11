import { copyright } from 'virtual:mascope-legal'

import { runtime } from '@/lib/runtime'

// What the sign-in footer, the About dialog and the update banner say about the
// product: who makes it, which build is running, and where its legal documents
// and support live.

export const COMPANY = 'Ultra Trace Systems Oy'
export const REPOSITORY_URL = 'https://github.com/ultra-trace-systems/mascope'
export const SECURITY_POLICY_URL = `${REPOSITORY_URL}/security/policy`
const CHANGELOG_URL = `${REPOSITORY_URL}/blob/master/CHANGELOG.md`

/** NOTICE's copyright line as a footer shows it: "© 2021-2026 Ultra Trace Systems Oy". */
export const copyrightNotice = copyright
  ? copyright.replace(/^copyright\s+/i, '© ')
  : `© ${COMPANY}`

// --- Legal and support links ---

// The [meta] defaults (MetaConfig in libraries/runtime/src/mascope_runtime/
// config.py), repeated for a runtime published by a CLI that predates the
// settings, whose [meta] simply lacks them. Keep the two in step.
const LINK_DEFAULTS = {
  privacy_notice_url: 'https://ultratrace.eu/mascope/privacy',
  terms_url: '',
  support_url: 'mailto:support@ultratrace.eu'
}

const WEB = ['https:', 'http:']

/**
 * `url` as an href, or null when it is empty or not an absolute URL of one of
 * `schemes`. The backend already refuses anything else at load, but the value
 * reaches the page through /runtime-config.js, and an href is exactly where a
 * `javascript:` URL would run.
 */
export const safeHref = (url, schemes = WEB) => {
  if (typeof url !== 'string' || !url.trim()) return null
  try {
    const parsed = new URL(url.trim())
    return schemes.includes(parsed.protocol) ? parsed.href : null
  } catch {
    // Not absolute: a relative path would resolve against the app itself.
    return null
  }
}

/** The deployment's legal and support links, from [meta]; null where hidden. */
export const legalLinks = () => {
  const setting = (key) => runtime.meta?.[key] ?? LINK_DEFAULTS[key]
  return {
    privacy: safeHref(setting('privacy_notice_url')),
    terms: safeHref(setting('terms_url')),
    support: safeHref(setting('support_url'), [...WEB, 'mailto:'])
  }
}

/** A new tab for a web page; none for a mail link, which opens a mail client. */
export const linkTarget = (href) => (href?.startsWith('mailto:') ? undefined : '_blank')

/** How to show a support link: the address for mail, the URL otherwise. */
export const supportLabel = (href) =>
  href?.startsWith('mailto:')
    ? decodeURIComponent(href.slice('mailto:'.length).split('?')[0])
    : href

// --- Versions ---

// Version strings that name a channel, or nothing, rather than a build.
const NOT_A_BUILD = new Set(['latest', 'unknown'])

/** True when `version` names a specific build. */
export const isBuildVersion = (version) =>
  typeof version === 'string' && version.trim() !== '' && !NOT_A_BUILD.has(version.trim())

/**
 * The version this bundle was built as: index.html's mascope-version tag,
 * written at build time by scripts/vite-plugin-legal.js. Not `runtime.version`,
 * which in a deployment is the tag the stack was started with - it names what
 * the server was asked to run, not what this tab loaded.
 */
export const builtVersion = () =>
  document.querySelector('meta[name="mascope-version"]')?.getAttribute('content') ||
  runtime.version ||
  null

/**
 * True when the web app and the server name different builds - a tab, or a
 * frontend container, left behind by an update. A channel such as `latest`
 * cannot be compared with anything, so it never counts as a mismatch.
 */
export const versionsDiffer = (web, server) =>
  isBuildVersion(web) && isBuildVersion(server) && web.trim() !== server.trim()

const RELEASE_TAG = /^v\d+\.\d+\.\d+([-+.][0-9A-Za-z.-]+)?$/

/** Where to read what changed in `version`: its release page if tagged, else the changelog. */
export const releaseNotesUrl = (version) =>
  RELEASE_TAG.test(version ?? '')
    ? `${REPOSITORY_URL}/releases/tag/${encodeURIComponent(version)}`
    : CHANGELOG_URL

/** The builds in front of the user, laid out for a support request. */
export const versionReport = ({ web, server, userAgent = navigator.userAgent }) =>
  [
    `Mascope web app: ${web || 'unknown'}`,
    `Mascope server: ${server || 'unavailable'}`,
    `Browser: ${userAgent}`
  ].join('\n')
