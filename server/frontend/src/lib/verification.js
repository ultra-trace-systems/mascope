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
