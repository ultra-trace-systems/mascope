// How a paired machine describes itself in the Paired machines list.
//
// Two of the four parts are reported by the agent rather than established by
// the server, and both are absent until an agent that reports them has paired
// or uploaded - so the label leaves out what a machine has not said.

/**
 * One line describing a paired machine: what it is, what it watches, what it
 * runs, and when it was last seen.
 *
 * The instrument and the release are labelled rather than dropped into the
 * list bare. Unlabelled they are ambiguous - a machine that reports only one
 * of them renders the same shape either way, and an instrument named `2` is
 * indistinguishable from a version - and they are free text from the machine,
 * so an unlabelled slot lets a value that contains the separator read as
 * extra fields.
 *
 * @param {object} device Device record from `GET /api/auth/devices`.
 * @param {string} serviceLabel Display name of the device's agent service.
 * @param {string} lastSeenLabel Rendered "last seen" phrase.
 * @returns {string} The parts the machine has reported, separated by ` · `.
 */
export function deviceMetaLabel(device, serviceLabel, lastSeenLabel) {
  return [
    serviceLabel,
    device.instrument ? `watching ${device.instrument}` : null,
    device.last_seen_version ? `agent ${device.last_seen_version}` : null,
    lastSeenLabel
  ]
    .filter(Boolean)
    .join(' · ')
}
