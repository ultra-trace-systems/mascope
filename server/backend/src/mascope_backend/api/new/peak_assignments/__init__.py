"""The in-app peak assignment: a sample's peaks, each given its formula and tier.

The package is layered, lowest first. A module imports only from its own
layer or a lower one, at its top: none imports a module of the package inside
a function, and no two modules import each other, however far round
(``tooling/structure.py`` checks both). Where a lower layer has to reach an
upper one, it is handed the callable - the stage list's keep stage, which
the run's lifecycle and the fold pass in - rather than importing it.

1. Records and config: ``tiers``, ``config``, ``schemas``, ``claims``,
   ``peak_ids``, ``profiles``, ``row_terms`` (a row's roles, sources and
   provenance keys), ``run_config`` (what a run records on its row),
   ``listing``, ``target_listing``, ``admission``, ``calibration_store``; the
   batch ledger's records: ``batch_peaks``, ``batch_blend``, ``batch_lock``
   and ``batch_staleness`` (the stage names, their states and the marks that
   make them stale, below both the stages and their orchestrator).
2. Inputs: ``sample_inputs`` (a sample's peaks, resolving power and known
   isotopes), ``sample_mechanisms``, ``profile_preview``.
3. Stages: ``engine``, ``untargeted_rows``, ``partner_gate``, ``tiering``,
   ``mass_gate``, ``envelope_claims``, ``envelope_fit``, ``cross_channel``,
   ``identification_level``, ``reagent_pass``, ``artifact_pass``,
   ``prepasses``, ``stage_a_pass``, ``halogen_twins``, ``decoy``,
   ``seeded_scoring``, ``alternatives_scoring``, ``derived_evidence``,
   ``fold_view``, ``known_species``; and ``model_readers``, the recorded
   measurement model's reader, which sits with the stages because where no
   run recorded a model it reads the reagent lines' offset again through the
   reagent pass.
4. The pipeline: ``pipeline`` (the stage list, Stage A's assignments and the
   neighbour reads it pools a thin file's envelope from), ``judging_loop``,
   and what a run reads of its batch: ``batch_decisions``, ``batch_lines``.
5. The batch: the ledger's fold and runs (``batch_peaks_controller``,
   ``batch_runs``, ``batch_fold``), its stages (``batch_water_ladder``,
   ``batch_known_species``, ``batch_untargeted``,
   ``batch_merged_isotopologues``, ``batch_isotopologues``,
   ``batch_series``, ``batch_coherence``, ``continuity``, ``batch_checks``,
   ``batch_rejudge``) and their orchestrator, ``batch_stages``.
6. Entry points: the run's lifecycle (``service``), curation
   (``curation``, ``batch_curation``, ``batch_peak_verification``), imports
   (``import_service``, ``import_validation``, ``batch_import``) and an
   instrument's recalibration (``recalibration``).
7. Reads for the API, and the routes: ``ledger_reads``, ``verification``,
   ``verification_queue``, ``batch_assignment_status``,
   ``batch_peaks_records``, ``batch_export``, ``sample_status``,
   ``visualization``, ``routes``, ``batch_peaks_routes``.
"""
