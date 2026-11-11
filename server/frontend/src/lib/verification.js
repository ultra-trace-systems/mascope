// Verification (labelling) vocabulary shared by the capture control, the verdict
// badge, and the ledger filter. Pure metadata -- no store state. Mirrors the
// backend Verdict / EvidenceLevel literals (peak_assignments/schemas.py).

// Schymanski-aligned evidence levels, strongest -> weakest. Required when a
// verdict is `confirmed` (enforced server-side too).
export const EVIDENCE_LEVELS = [
  { value: 'reference_standard', label: 'Reference standard' },
  { value: 'msms', label: 'MS/MS' },
  { value: 'orthogonal', label: 'Orthogonal' },
  { value: 'pattern', label: 'Isotope/adduct pattern' },
  { value: 'visual', label: 'Visual' }
]
const EVIDENCE_LABEL = Object.fromEntries(EVIDENCE_LEVELS.map((e) => [e.value, e.label]))
export const evidenceLabel = (key) => (key ? (EVIDENCE_LABEL[key] ?? key) : null)

// verdict -> chip metadata (label, phosphor icon, PrimeVue Tag severity). Confirm
// and Reject are given equal weight by design (reject is a first-class label).
export const VERDICT_META = {
  confirmed: { label: 'Confirmed', icon: 'ph ph-check-circle', severity: 'success' },
  rejected: { label: 'Rejected', icon: 'ph ph-x-circle', severity: 'danger' },
  unsure: { label: 'Unsure', icon: 'ph ph-question', severity: 'warn' }
}

// --- Identification claims ---------------------------------------------------
// A claim is a confirmation that names a structure, an InChIKey and its name
// (docs/dev/identification_claims.md section 5). The server fixes the level it
// states; these mirror its rules so the form can say them before a request
// (peak_assignments/claims.py).

// Evidence that is a statement about a structure: a confirmation on it names one.
export const STRUCTURE_EVIDENCE = ['reference_standard', 'msms', 'orthogonal']
export const needsStructure = (evidence) => STRUCTURE_EVIDENCE.includes(evidence)

const LEVEL_OF_EVIDENCE = { reference_standard: 1, msms: 2, orthogonal: 3, pattern: 4 }

/**
 * The level a confirmation states, as the server will fix it: 1 to 3 on
 * structure evidence, 4 on a pattern, none on a visual look, where the row
 * reads the engine's level.
 *
 * @param {?string} evidence - the evidence level
 * @returns {?number}
 */
export const claimLevel = (evidence) => LEVEL_OF_EVIDENCE[evidence] ?? null

export const levelLabel = (level) => (Number.isInteger(level) ? `Level ${level}` : null)

/**
 * The structures a confirmation may name: the row's candidates, the target's
 * declaration first, then the reference identities, each InChIKey once. An
 * identity without an InChIKey names no structure and is left out.
 *
 * @param {object} [candidates]
 * @param {?{name: ?string, inchikey: ?string}} [candidates.target] - what the
 *   matched target compound declares
 * @param {Array<object>} [candidates.identities] - the reference identities,
 *   each with `name`, `source` and `inchikey`
 * @returns {Array<{inchikey: string, name: ?string, label: string}>}
 */
export function claimCandidates({ target = null, identities = [] } = {}) {
  const found = []
  const add = (record, label) => {
    const inchikey = typeof record?.inchikey === 'string' ? record.inchikey.trim() : ''
    if (!inchikey || found.some((candidate) => candidate.inchikey === inchikey)) return
    const name = typeof record?.name === 'string' && record.name ? record.name : null
    found.push({ inchikey, name, label })
  }
  add(target, 'target')
  for (const identity of Array.isArray(identities) ? identities : []) {
    add(identity, identity?.source || 'reference list')
  }
  return found
}
