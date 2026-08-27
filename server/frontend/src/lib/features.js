import { runtime } from '@/lib/runtime'

/**
 * Peak-centric assignment (docs/user/how-it-works/peak-assignment.md).
 *
 * On unless the deployment switches it off via the `peak_assignment` flag in
 * the runtime `[meta]` config -- the same switch the backend reads. With it off
 * the targeted workflow renders exactly as it did before the feature landed:
 * the peak ledger and composition search in the Sample tab, legacy match scores
 * in the search results, and no assignment views.
 *
 * Read once at module load from `runtime`, which prefers the config the
 * container published at start over the copy baked into the bundle (see
 * `@/lib/runtime`). Flipping the flag is therefore a stack restart, and the
 * value the UI gates on is the one the backend is actually enforcing.
 *
 * A missing key reads as OFF here, while the backend's own fallback is on. The
 * asymmetry is deliberate and effectively unreachable - `peak_assignment` is a
 * MetaConfig field, so the serialized runtime always carries it - but if a
 * runtime ever arrived without it, hiding the views is the safer half to be
 * wrong on: the writes would still be gated server-side.
 */
export const peakAssignmentEnabled = Boolean(runtime?.meta?.peak_assignment)

/**
 * Largest single sample-file upload the server will accept, in bytes.
 *
 * The backend enforces `tus_max_upload_gb` from the runtime `[meta]` config and
 * advertises it as `Tus-Max-Size`; the uploader sizes its own client-side
 * restriction from the same value so the browser never refuses a file the
 * server would take (it used to hard-cap at 2.5 GiB, below even the default).
 * Read from the same published runtime as `peak_assignment`, so raising the cap
 * on a deployment takes a stack restart and both sides agree on the new value.
 * Falls back to the 5 GB default when no runtime is present.
 */
export const maxUploadBytes = (Number(runtime?.meta?.tus_max_upload_gb) || 5) * 1024 ** 3
