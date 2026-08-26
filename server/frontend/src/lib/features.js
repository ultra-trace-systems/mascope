import { runtime } from '@/lib/runtime'

/**
 * Peak-centric assignment (docs/user/how-it-works/peak-assignment.md).
 *
 * Off unless the deployment opts in via the `peak_assignment` flag in the
 * runtime `[meta]` config -- the same switch the backend reads. With it off the
 * targeted workflow renders exactly as it did before the feature landed: the
 * peak ledger and composition search in the Sample tab, legacy match scores in
 * the search results, and no assignment views.
 */
export const peakAssignmentEnabled = Boolean(runtime?.meta?.peak_assignment)

/**
 * Largest single sample-file upload the server will accept, in bytes.
 *
 * The backend enforces `tus_max_upload_gb` from the runtime `[meta]` config and
 * advertises it as `Tus-Max-Size`; the uploader sizes its own client-side
 * restriction from the same value so the browser never refuses a file the
 * server would take (it used to hard-cap at 2.5 GiB, below even the default).
 * The value is baked in at build time like `peak_assignment`, so raising the
 * cap on a deployment needs a frontend rebuild, not just a backend restart.
 * Falls back to the 5 GB default when no runtime is present.
 */
export const maxUploadBytes = (Number(runtime?.meta?.tus_max_upload_gb) || 5) * 1024 ** 3
