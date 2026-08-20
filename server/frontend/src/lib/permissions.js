// Client-side answers to "may I do this?", derived from the same two layers
// the backend enforces: the account's global role and its per-workspace role.
//
// These exist to keep the UI from offering an action that will come back 403,
// not to enforce anything - the backend remains the only authority. That
// asymmetry decides how the uncertain cases resolve: when the data needed to
// answer is not loaded, these return `true` and let the request be the judge.
// Showing a control that turns out to be refused is the status quo; hiding one
// that would have worked is a worse bug, because the user has no way to
// discover the capability they actually have.
//
// Workspace roles arrive as `my_role` on each record from `GET /api/workspaces`.

import { ROLES, roleLevel } from '@/lib/roles'

// Instrument workspaces are system workspaces named "Acquisitions <instrument>"
// (ACQUISITION_NAME_PREFIX on the backend).
const ACQUISITION_PREFIX = 'Acquisitions'

/**
 * The system workspace holding raw files for `instrument`, if it is loaded.
 *
 * Compared case-insensitively while the backend's lookup is exact. Being the
 * looser of the two is deliberate: a mismatch here can only over-report a
 * capability, which the backend then refuses, rather than hide a real one.
 */
export const instrumentWorkspace = (workspaces, instrument) => {
  if (!instrument) return undefined
  const target = `${ACQUISITION_PREFIX} ${instrument}`.toLowerCase()
  return (workspaces ?? []).find(
    (ws) => ws?.is_system && ws?.workspace_name?.toLowerCase() === target
  )
}

/** The caller's own level in a workspace record; 0 when not a member. */
export const myLevel = (workspace) => roleLevel(workspace?.my_role)

/**
 * Whether `user` may write an m/z calibration onto files from `instrument`.
 *
 * A calibration is written onto the raw file, so every workspace referencing
 * that file sees it; the instrument workspace is what bounds that, and admin
 * there is the bar. Global admins are allowed because the backend's
 * instrument-workspace checks bypass for them - including on workspaces created
 * before they were promoted, where they hold no membership at all.
 */
export const canCalibrateInstrument = (workspaces, user, instrument) => {
  if ((user?.role_id ?? 0) >= ROLES.admin) return true
  if (!instrument) return true
  return myLevel(instrumentWorkspace(workspaces, instrument)) >= ROLES.admin
}

/**
 * The batch-wide variant: every instrument the batch draws on must pass, since
 * the backend checks each one.
 *
 * An empty list means the batch's samples are not loaded, so the answer is
 * unknown and the control stays enabled.
 */
export const canCalibrateInstruments = (workspaces, user, instruments) => {
  if (!instruments?.length) return true
  return instruments.every((instrument) => canCalibrateInstrument(workspaces, user, instrument))
}

/** The distinct instruments among `samples` belonging to `sample_batch_id`. */
export const batchInstruments = (samples, sample_batch_id) => [
  ...new Set(
    (samples ?? [])
      .filter((s) => s?.sample_batch_id === sample_batch_id && s?.instrument)
      .map((s) => s.instrument)
  )
]
