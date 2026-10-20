/**
 * The zone this browser runs in, for the acquisition times of its uploads.
 *
 * A raw file records its acquisition time as the instrument PC's wall clock,
 * with no offset, so the server can only place it in UTC if the upload says
 * which zone that clock was in. A File Agent reports its machine's zone; a
 * browser upload reported nothing, and the converter then read the wall
 * clock in its own zone - UTC in the production image - so a file uploaded
 * from the web app at a site east of UTC was stored hours late. The browser's
 * zone is a weaker hint than the agent's, since the browser is wherever the
 * person uploading sits, and the server records it under its own name.
 */

// Only a geographic zone (continent/city) is a reading of where the browser
// is. Browsers with fingerprinting resistance, and many headless, VM and
// kiosk browsers, report a placeholder such as `UTC` or `Etc/UTC` wherever
// they stand; sent as a fact, that would store the file exactly as the old
// guess did, no longer flagged as one. A real machine at a UTC+0 site reports
// `Europe/London` or `Atlantic/Reykjavik`, which pass.
const GEOGRAPHIC_ZONE =
  /^(Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|Europe|Indian|Pacific)\/[A-Za-z0-9_+-]+(\/[A-Za-z0-9_+-]+)?$/

/**
 * The IANA zone this browser runs in, when it names a geographic one.
 *
 * @returns {string | undefined} the zone, or undefined when the browser
 *   cannot name one or names only a placeholder; the server then falls back
 *   to its own zone, as it did before any zone was sent
 */
export function browserTimezone() {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone
    return typeof zone === 'string' && GEOGRAPHIC_ZONE.test(zone) ? zone : undefined
  } catch {
    return undefined
  }
}
