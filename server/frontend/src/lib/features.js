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
