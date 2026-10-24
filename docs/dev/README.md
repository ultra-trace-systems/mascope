# Design notes

The developer guide is [`developer_guide.md`](developer_guide.md): how to build,
run and deploy Mascope, and how the backend, database, frontend and libraries
fit together. Everything else in this folder is a design note: the record of
why a piece of Mascope is shaped the way it is, what of it is built, and where
the open work is tracked.

Every note opens with the same header, and the table below repeats it:

- `Status` is `design` (nothing built), `building` (partly built, work in
  flight), `built` (done; the note is the record) or `retired` (superseded; a
  retired note is deleted with a pointer in the note that superseded it, and
  its text stays in git history).
- `As of` is the date the header was last checked against the tree.
- `Tracked in` links the Linear project or issue (team Mascope, key MAS) or
  the GitHub issue that carries the open work. `closed` means nothing is
  open; `none` means there is open work no issue owns yet, which a `design`
  or `building` note may not have.
- `Supersedes` or `Superseded by` names the note this one replaced or was
  replaced by, when there is one, in backticks or as a link.
- `What` is one sentence on what the note is.

The fields come in that order, one `- **Field:** value` bullet each, after the
note's title; a long value runs on in lines indented two spaces.

The header is the source and this table is made from it: when a note's state
changes, change its header and run `uv run python tooling/notes.py index`.
`uv run python tooling/notes.py check` fails on a note without the header, on
a value it does not accept, and on a table that disagrees with the headers;
it warns about a `design` or `building` note whose `As of` is more than 90
days old. Roadmap work is planned in Linear and defects are GitHub issues:
`CLAUDE.md`, "Where work is tracked", has the split.

| Note | Status | Tracked in | What it is |
|---|---|---|---|
| [`acquisition_sidecar.md`](acquisition_sidecar.md) | built | [Automatic ingest: routing and splitting](https://linear.app/ultra-trace/project/automatic-ingest-routing-and-splitting-68acd9575fa9) (Linear; the FAIR export of its ids is MAS-33) | The acquisition record a control program writes and the File Agent sends with a file: how it travels, routing and export. The schema itself is the user page [Acquisition records](../user/instruments/acquisition-records.md). |
| [`ai_layer.md`](ai_layer.md) | building | [AI layer](https://linear.app/ultra-trace/project/ai-layer-d957c301639f) (Linear, MAS-123 to MAS-142) | A natural-language agent over the SDK, read-only first, with a confirmed write path later. |
| [`assignment_confidence.md`](assignment_confidence.md) | building | [Assignment quality plan](https://linear.app/ultra-trace/project/assignment-quality-plan-553f4854192c) (Linear; MAS-73 carries the identification level; MAS-324 to MAS-327 the open P2 to P4 steps) | The layered science of going from a peak to a confident identification: plausibility, arbitration, calibration, tiers. |
| [`assignment_quality_plan.md`](assignment_quality_plan.md) | building | [Assignment quality plan](https://linear.app/ultra-trace/project/assignment-quality-plan-553f4854192c) (Linear, MAS-37 to MAS-73, MAS-408 to MAS-410 and MAS-443) | The step-by-step plan closing the assignment quality gap: stages, gates, decisions and risks. |
| [`assignment_quality_plan_log.md`](assignment_quality_plan_log.md) | built | [Assignment quality plan](https://linear.app/ultra-trace/project/assignment-quality-plan-553f4854192c) (Linear) | The quality plan's measurement log and the as-built specifications of its completed steps. |
| [`chemistry_profiles.md`](chemistry_profiles.md) | building | [Assignment quality plan](https://linear.app/ultra-trace/project/assignment-quality-plan-553f4854192c) (Linear: MAS-59, MAS-61, MAS-63, MAS-65; MAS-328 the mismatched-context harm bound; MAS-342 is where 4.4's `reference_tags` activation lives) | Reagent-ion chemistry profiles and chemistry contexts as the unit a run searches under. |
| [`cli_decoupling_plan.md`](cli_decoupling_plan.md) | building | [CLI decoupling](https://linear.app/ultra-trace/project/cli-decoupling-f732d6d0ca80) (Linear, MAS-112 to MAS-122) | The standalone operator CLI, the wheel install, and what a server installs. |
| [`device_identity_plan.md`](device_identity_plan.md) | building | [Release 2.0 and the security campaign](https://linear.app/ultra-trace/project/release-20-and-the-security-campaign-89b9f4b36f95) (Linear: MAS-210, MAS-81, MAS-82 and the decision MAS-305 for phase E; MAS-308, MAS-309, MAS-333 and MAS-304 for phase F) | First-class identity for instrument agents: pairing, attribution, machine accounts, rollout. |
| [`fair_roadmap.md`](fair_roadmap.md) | building | [FAIR data roadmap](https://linear.app/ultra-trace/project/fair-data-roadmap-0484070a43ea) (Linear, MAS-19 to MAS-36) | Mascope against the FAIR principles, and the phased plan: provenance, ids, metadata, deposit. |
| [`file_agent_self_update_plan.md`](file_agent_self_update_plan.md) | design | [OrbionICS self-update](https://linear.app/ultra-trace/project/orbionics-self-update-96caa663228f) (Linear, ICS-89 to ICS-101, filed 2026-10-22: milestones A, B and C; ICS-92 decides whether B gates MAS-210) | The File Agent moving to a new release without a hand on the instrument PC. |
| [`handoff_fair_phase0.md`](handoff_fair_phase0.md) | built | [FAIR data roadmap](https://linear.app/ultra-trace/project/fair-data-roadmap-0484070a43ea) (Linear, MAS-19 to MAS-23, done) | FAIR phase 0 as built: the provenance block, ids in exports, the OpenAPI document. |
| [`handoff_fair_phase1.md`](handoff_fair_phase1.md) | building | [FAIR data roadmap](https://linear.app/ultra-trace/project/fair-data-roadmap-0484070a43ea) (Linear, MAS-24 to MAS-28; item 9 is PR #2475) | FAIR phase 1 brief: dataset licence and authors, InChIKey on compounds, tombstones. |
| [`identification_claims.md`](identification_claims.md) | design | [FAIR data roadmap](https://linear.app/ultra-trace/project/fair-data-roadmap-0484070a43ea) (Linear: MAS-357 the structure lookup, MAS-363 the exports, MAS-361 the decision on the classic sheet; MAS-31 is the level's FAIR export) and [Assignment quality plan](https://linear.app/ultra-trace/project/assignment-quality-plan-553f4854192c) (MAS-358 the target as a candidate, MAS-359 the claim, MAS-360 the MS/MS ranking, MAS-362 the decision on the targeted path; MAS-73 carries the engine's level); filed 2026-10-22 | Identifiers as candidates and an identification as a separate, evidenced claim with a Schymanski level: where each lives, who may state what. |
| [`ingest_routing_and_splitting.md`](ingest_routing_and_splitting.md) | building | [Automatic ingest: routing and splitting](https://linear.app/ultra-trace/project/automatic-ingest-routing-and-splitting-68acd9575fa9) (Linear) and GitHub #2098 | Automatic ingest: binding a file's chemistry by evidence and splitting it into streams, segments and windows. |
| [`isolated_deployment_plan.md`](isolated_deployment_plan.md) | design | [Isolated deployments](https://linear.app/ultra-trace/project/isolated-deployments-7585d72b2a80) (Linear: MAS-301, MAS-302 and MAS-307 for phase A; MAS-303 re-bases the bundle on the wheel install, MAS-116 option b, before phases B to E are filed) | Running and updating a server with no internet route. |
| [`mfa_totp_plan.md`](mfa_totp_plan.md) | built | [Release 2.0 and the security campaign](https://linear.app/ultra-trace/project/release-20-and-the-security-campaign-89b9f4b36f95) (Linear; MAS-77, MAS-78, MAS-79 and MAS-111 done, MAS-211 open) | A TOTP second factor for the web session, with offline recovery. |
| [`ms2_analysis.md`](ms2_analysis.md) | design | [MS2 analysis](https://linear.app/ultra-trace/project/ms2-analysis-7fdc525bca51) (Linear, MAS-380 to MAS-407) | Fragmentation spectra as a stored, assigned and served object: the MS2 store, the fragment composition core shared with peaky, MS2 evidence on ledger rows, the MS2 tab, energy-resolved analysis. |
| [`peak_assignment_batch_primary.md`](peak_assignment_batch_primary.md) | built | closed; decisions 10.4 and 10.5 are carried inside MAS-321 and MAS-322 ([Assignment quality plan](https://linear.app/ultra-trace/project/assignment-quality-plan-553f4854192c) milestone 4) | The batch ledger as the durable assignment object; samples fold in at ingest. |
| [`peak_assignment_frontend.md`](peak_assignment_frontend.md) | built | closed; retiring the Targets/Assignments switch is MAS-162 in [Dashboard UX improvements](https://linear.app/ultra-trace/project/dashboard-ux-improvements-435aa9db35a9) | The Sample view as shipped for peak-centric assignment: ledger, inspector, verification, curation. |
| [`peak_assignment_paradigm.md`](peak_assignment_paradigm.md) | built | closed; the phase 4 leftovers are MAS-61 and MAS-229, the "fold targeted in" of phase 5 is MAS-162 and MAS-80 | The shift from target-anchored matching to assigning every peak, and how the engine is operated. |
| [`public_database_integration.md`](public_database_integration.md) | building | [MS2 analysis](https://linear.app/ultra-trace/project/ms2-analysis-7fdc525bca51) milestone 6, "Spectral library matching", for phase 4 (Linear: MAS-429 the `reference_spectrum` table and the library adapters, MAS-360 the ranking); for phase 5, MAS-427 the SDK surface in [AI layer](https://linear.app/ultra-trace/project/ai-layer-d957c301639f) milestone 1 and MAS-428 the refresh cadence and version pinning in [Ops workflows and dev tooling](https://linear.app/ultra-trace/project/ops-workflows-and-dev-tooling-0dd3555613f9) | Mirroring public compound databases to give assigned formulas an identity. |
| [`raw_file_sample_links.md`](raw_file_sample_links.md) | design | [Dashboard UX improvements](https://linear.app/ultra-trace/project/dashboard-ux-improvements-435aa9db35a9) (Linear, MAS-245 and MAS-246) | Links from a raw file to the samples made from it. |
| [`reference_data_authoring.md`](reference_data_authoring.md) | retired | closed | A pointer: the guide to authoring and loading a custom reference compound list is the user page [Reference compound lists](../user/guides/reference-lists.md) since 2026-10-22. |
| [`reference_lists_as_target_collections.md`](reference_lists_as_target_collections.md) | design | [Reference lists as target collections](https://linear.app/ultra-trace/project/reference-lists-as-target-collections-63b440eeab90) (Linear; decisions MAS-447 to MAS-452; phases 0 to 3 filed 2026-10-23 as MAS-453 to MAS-467) | Curated reference lists usable as target collections, pinned to a version and materialized as ordinary compounds, with target management moved onto the batch. |
| [`sample_filters_and_metadata.md`](sample_filters_and_metadata.md) | design | [Sample finder and acquisition metadata](https://linear.app/ultra-trace/project/sample-finder-and-acquisition-metadata-cd1678c32e78) (Linear; decisions MAS-104 to MAS-110; phases 1 to 4 filed 2026-10-22 as MAS-306, MAS-310, MAS-311, MAS-334 to MAS-336, MAS-338 to MAS-343, MAS-348 and MAS-349) | Sample search and refreshable batches over acquisition metadata. |
| [`sdk_peak_assignment.md`](sdk_peak_assignment.md) | building | MAS-71 in [Assignment quality plan](https://linear.app/ultra-trace/project/assignment-quality-plan-553f4854192c) for the import contract; the SDK write wrappers (section 8.4 steps 5 and 6) are MAS-330 and MAS-345, the demo bundle MAS-331 and the reference identities in frames MAS-332, in [AI layer](https://linear.app/ultra-trace/project/ai-layer-d957c301639f) milestone 3 (filed 2026-10-22) | The SDK's peak-assignment surface; section 8 is the run-import contract. |
| [`tof_calibration.md`](tof_calibration.md) | building | [TOF mass calibration](https://linear.app/ultra-trace/project/tof-mass-calibration-8271ef05d142) (Linear, MAS-171 to MAS-197) | TOF mass calibration across the analysed range: measure, fix calibrants, model, re-calibrate. |
| [`ui_state_persistence.md`](ui_state_persistence.md) | built | closed; continuous URL mirroring, the phase 4 leftover, has no issue | UI state that survives a reload, and shareable locations. |
| [`ux_review_2026_10.md`](ux_review_2026_10.md) | building | [Dashboard UX improvements](https://linear.app/ultra-trace/project/dashboard-ux-improvements-435aa9db35a9) (Linear, MAS-148 to MAS-163) | The dashboard UX review of October 2026 and its three tiers of fixes. |
| [`verification_calibration_loop.md`](verification_calibration_loop.md) | building | [Assignment quality plan](https://linear.app/ultra-trace/project/assignment-quality-plan-553f4854192c) (Linear; MAS-63 covers V2 activation and the corroboration refit; the V3 active-learning queue is MAS-329, with the three section 7 decisions recorded inside it) | Human verdicts on assignments feeding the confidence calibration. |
| [`visual_digest.md`](visual_digest.md) | building | [Visual digests](https://linear.app/ultra-trace/project/visual-digests-51cecff8f790) (Linear, MAS-217 to MAS-244) | Charts, dashboards and reports over the SDK for campaign-scale questions. |

## Retired on 2026-10-22

Deleted after their content was merged into, or pointed at from, the note that
superseded them. Their text is in git history before that date.

| Note | Where its content went |
|---|---|
| `peak_assignment_batch.md` | `peak_assignment_batch_primary.md` section 12 (its sections 2, 3, 5.3 and 8) |
| `peak_assignment_copy.md` | `peak_assignment_batch_primary.md` sections 6.4 and 11; the feature itself was removed in 1.8.0 |
| `peak_assignment_continuity.md` | `peak_assignment_batch_primary.md` section 6.3 (the shipped anchor verdicts); the ratio-stability design is in git history |
| `peak_assignment_confidence_frontend.md` | `peak_assignment_frontend.md`, "Confidence" |
| `verification_capture_frontend.md` | `verification_calibration_loop.md` section 4.5 |
| `reference_peak_assignment_convergence.md` | `public_database_integration.md` Phase 3 and `peak_assignment_paradigm.md` 3.2 |
| `handoff_fit_score_and_assignment.md` | `assignment_confidence.md`; its mass-sigma paragraph is in `libraries/tools/docs/fit_score.md` |
| `ionization_method_config.md` | `ingest_routing_and_splitting.md` phase 8 (its section 4 target model); its section 7 was superseded by `fair_roadmap.md` |
