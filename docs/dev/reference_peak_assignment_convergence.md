`ULTRA TRACE MASCOPE - DESIGN DOC - REFERENCE x PEAK-CENTRIC CONVERGENCE`

# Reference Database x Peak-Centric Assignment: Convergence Plan

## Purpose

Two initiatives are in flight and they meet at a single seam:

- **Public reference database** ([public_database_integration.md](public_database_integration.md)) -
  mirrors known compounds and annotates assigned formulas with identity.
- **Peak-centric assignment** ([peak_assignment_paradigm.md](peak_assignment_paradigm.md)) -
  assigns a composition to *every* peak, database-known first (Stage A) then
  untargeted (Stage B).

Both design docs describe the *same* deliverable from opposite ends:

- Reference-DB **Phase 3**: "Expose `reference_compound` as the known-composition
  set for **Stage A of the peak-centric engine**; annotate `source=database`
  assignments with identity."
- Peak-centric **Phase 1 / Stage A**: match every peak against known
  isotopologues - "the existing target library, **optionally plus a curated
  'known compositions' reference set**."

Implemented twice, this duplicates logic and guarantees merge conflicts. This
document makes the seam a **single, explicitly-owned piece of work** with one
narrow interface, so the two tracks stop entangling. It supersedes the phrasing
of reference-DB Phase 3 and the "reference set" arm of peak-centric Phase 1.

> Status: **being applied.** The seam described here is implemented in PR #1633
> (`feat/reference-stage-a`): `iter_known_compositions` (Section 2.1) plus its
> consumption in Stage A, with the reference set bounded to an atmospheric window
> — which settles open decision 3 (Section 7). Sequencing steps 1-2 (Section 5)
> are done: the reference work and the peak-centric epic both sit on
> `epic/peak-centric-assignment`, which has been rebased onto `develop` with its
> Alembic chain re-parented onto develop's head.

---

## 1. Ownership decision

**The convergence is owned by the peak-centric track.** It is fundamentally an
extension of Stage A - turning known neutral formulas into matchable
isotopologues, matching them peak-centrically, arbitrating peak ownership, and
persisting the result. That is peak-centric machinery; the reference database is
a *data provider* to it.

Consequences:

- **Reference-DB Phase 3 is retired as a standalone phase.** Its content is
  realized by the peak-centric Stage A work through the interface in Section 2.
- **Reference-DB Phase 4 (MS/MS spectral matching) stays independent** on the
  reference track - it is a separate subsystem that does not touch peak-centric
  assignment.
- The reference library takes **no dependency** on the peak-centric engine.

---

## 2. The seam: one narrow, one-directional interface

The only coupling between the two subsystems is a small query contract. Keeping
it narrow and one-directional is what prevents future entanglement.

**Direction (strict):** the peak-centric engine imports `mascope_reference`;
`mascope_reference` never imports peak-centric code, and the peak-centric engine
never reads the `reference_*` tables directly - only through the query interface.

### 2.1 What `mascope_reference` provides

Today (shipped): per-formula lookup used for *annotation* -
`by_formula`, `by_mass_window`, `annotate_formulas`.

New for Stage A: a **bulk provider of the active known-composition set**, so
Stage A can pre-compute isotopologues once per run instead of querying per peak.
Implemented as `mascope_reference.known.iter_known_compositions` (consumed by
`api/new/peak_assignments/service.py`):

```python
async def iter_known_compositions(
    session: AsyncSession,
    *,
    licenses: set[str] | None = None,  # commercial gating; None = all
    elements: frozenset[str] | None = DEFAULT_ELEMENTS,  # atmospheric window
    max_carbon: int | None = DEFAULT_MAX_CARBON,
    max_mass: float | None = DEFAULT_MAX_MASS,
    max_identities: int = DEFAULT_MAX_IDENTITIES,
) -> list[KnownComposition]: ...
```

where a `KnownComposition` is **one unique neutral formula** plus the identities
that share it:

| field | meaning |
| --- | --- |
| `formula` | canonical neutral formula, Hill order |
| `monoisotopic_mass` | precomputed on ingest |
| `identities` | list of `(name, source, license, inchikey, xrefs)` sharing the formula |

The set is deduplicated **on formula** (see Section 3), spans the active version
of every reference source, and is license-filterable.

### 2.2 What the peak-centric engine owns

Everything downstream of the formula list:

- formula -> ions (per the batch's ionization mechanisms) -> isotopologues, reusing
  the existing `target_ions_compute` / IsoSpec path;
- peak-centric matching + arbitration (Stage A of
  [peak_assignment_paradigm.md](peak_assignment_paradigm.md) §3.2);
- persistence as `PeakAssignment` rows with `source=database`.

---

## 3. Matching is formula-based; identity is one-to-many

The load-bearing subtlety that keeps the interface clean:

- **Stage A matches on formula.** Isotopologue prediction and envelope scoring
  are functions of the *neutral formula*, not of a specific named compound. So the
  set Stage A pre-computes isotopologues from is a set of **unique formulas**.
- **Identity is separate and one-to-many.** A single formula (e.g. `C10H16O3`)
  maps to many named reference compounds (pinonic acid, and others). Those names
  do not change the match; they are *attached* to the winning peak as identity.

Therefore:

- The known-composition set is deduplicated **on canonical formula** for matching.
- After a peak's owner formula is decided, its reference **identities** (possibly
  several) are attached to the `PeakAssignment`. This reuses the existing
  InChIKey collapse only for presentation, not for matching.

This cleanly divides responsibilities: reference DB answers "what formulas are
known, and what are they called"; the peak-centric engine answers "which formula
owns this peak."

---

## 4. Persisted semantics: `source=database` + identity

Aligning the two data models (`PeakAssignment` from peak-centric, `reference_compound`
from the reference DB):

- A peak whose winning formula came from the **known set** gets
  `source=database` (vs `untargeted` for Stage B).
- The known set is the **union** of two providers:
  1. the curated **target library** (existing `TargetCompound` / `TargetIon`),
     which sets `target_compound_id` / `target_ion_id` on the assignment;
  2. the **reference set** (`reference_compound`), which attaches reference
     **identity** (name, source, license, xrefs) but no `target_compound_id`.
- **Precedence when both provide the same formula:** the curated target library
  wins the `target_compound_id` linkage (it is the user's own, authoritative
  list); reference identities are still attached as additional annotation. Both
  remain `source=database`.
- Identity is stored on the assignment (e.g. an `identity` / `provenance` JSON
  field), not as a hard FK to `reference_compound`, so a reference re-sync (new
  versioned load) never dangles a past assignment - matching the reproducibility
  stance both docs already take.

Note: the *non-persisted* form of this already ships - reference annotation
enriches on-demand `cheminfo` results today. The convergence is the **persisted,
whole-spectrum** equivalent inside peak assignment.

---

## 5. Sequencing & merge discipline

Both initiatives edit `server/backend/.../db/models.py` and add Alembic
migrations, so they will keep colliding (as the reference-DB PR already did with
`develop`). Practical plan:

1. **Merge reference-DB PR first.** It is purely additive (new tables + additive
   annotation, no change to existing behavior), so it has the smaller blast
   radius.
2. **Rebase the peak-centric PR onto the merged result**, re-parenting its
   migration onto the new Alembic head (the same re-parent-and-verify drill used
   when the reference PR was rebased onto `develop`). Then merge it.
3. **Convergence PR (this seam).** One PR on the peak-centric track: add
   `iter_known_compositions` to `mascope_reference`, consume it in Stage A,
   persist `source=database` + identity. Closes reference-DB Phase 3 and completes
   peak-centric Phase 1's reference arm.
4. **Re-diverge, in parallel:**
   - peak-centric: Stage B untargeted -> arbitration/tiers -> batch (its Phases 2-4);
   - reference: MS/MS spectral matching (its Phase 4) - independent subsystem.
5. **Reconverge at productization:** one unified UI/CLI + reproducibility effort
   (both docs' Phase 5), not two.

Mitigations for the shared-file friction: land model/migration changes close
together, keep them non-overlapping in the file, and expect the second-to-merge
PR to rebase.

---

## 6. Edits to the existing design docs (not yet applied)

The convergence itself is implemented (Section 2), but these doc edits have not
been made — the two documents still describe the pre-convergence plan:

- [public_database_integration.md](public_database_integration.md) **Phase 3**:
  replace the phase body with a pointer to this document.
- [peak_assignment_paradigm.md](peak_assignment_paradigm.md) **Phase 1 / Stage A**:
  note that the "curated known compositions reference set" is provided via
  `iter_known_compositions` (Section 2 here).

---

## 7. Open decisions

1. **License filtering in Stage A.** Should a commercial deployment exclude
   reference compounds under non-redistributable licenses from *assignment*
   (not just from display)? *Recommend: filterable via `licenses=`, default to
   the open/public-domain set for assignment; annotation display can be broader.*
2. **Precedence, target vs reference, for the same formula.** *Recommended above:*
   target library owns `target_compound_id`; reference identity attached
   alongside. Confirm this matches how targeted alarms/collections should behave.
3. **Cost of the known set.** The union of the target library and a large
   reference mirror could be a very large formula set to pre-compute
   isotopologues for. *Recommend* bounding Stage A's reference set (e.g. to
   curated/suspect sources, or a mass/element-range gate) rather than the full
   PubChem mirror; the full mirror remains available for on-demand annotation.
   This is the main performance fork and should be decided before Stage A scales.

---

## 8. Summary

The two tracks converge at exactly one point - the known-composition set feeding
Stage A - and this document makes that point a single owned deliverable behind a
narrow, one-directional interface (`iter_known_compositions`). Matching stays
formula-based; identity stays one-to-many and attached, not matched on. With the
reference PR merged first and the peak-centric PR rebased onto it, the convergence
is one PR; after it, the tracks run in parallel (MS/MS vs Stage B/arbitration/batch)
and reconverge only at productization.
