-- Scale a folded batch ledger to ceiling size by cloning, inside one transaction,
-- the batch's anchors (batch_peak) and their members (batch_peak_occurrence)
-- `mult` times, for the continuity pass's benchmark (step 4.13).
--
-- Each generation is a copy of the seed ledger in an ionization mode of its own
-- (a clone of the anchor's mode, tagged with a "[bench-clone ...]" marker in its
-- name). The pass pairs anchors within a partition, mode by instrument type, so
-- a generation forms the seed's pairs again, among its own anchors only: the
-- pairs grow with the anchors, as they would on a larger batch, instead of the
-- copies of one neutral collapsing onto one channel. A generation's isotope
-- links (`isotopologue_of`) and members' owners point into the same generation.
-- The members keep their samples, so the batch's samples, their times and the
-- per-pair series are the seed's.
--
-- Idempotent: only the seed anchors (those in an unmarked mode) are cloned and
-- the ids are deterministic, so a re-run skips what exists. Raising `mult`
-- adds the missing generations; lowering it needs a fresh restore. A 4.2
-- co-variation record copied onto a clone still names the seed's partners, so
-- the clones' channel drifts grade `red` rather than `drift`: the cost is the
-- seed's, the grade counts are not; read the batch's co-variation again after
-- expanding it. And a fold on the expanded batch is no measure of a fold: the
-- copies share the seed's samples, so re-folding a sample deletes its members
-- in every copy.
--
-- Parameters (all required, passed with -v):
--   batch  sample_batch_id whose ledger is cloned
--   mult   generations to add (0 = leave the ledger as it is)
--
-- psql -v batch=<id> -v mult=33 -f expand_batch_peaks.sql   # ~10^5 anchors from ~3,000

\set ON_ERROR_STOP on
\set marker '[bench-clone'
\timing on

BEGIN;

CREATE TEMP TABLE seed_anchor ON COMMIT DROP AS
SELECT bp.batch_peak_id AS id, bp.ionization_mode_id AS mode_id
FROM batch_peak bp
JOIN ionization_mode im ON im.ionization_mode_id = bp.ionization_mode_id
WHERE bp.sample_batch_id = :'batch'
  AND im.ionization_mode_name NOT LIKE '%' || :'marker' || '%';

CREATE TEMP TABLE gen ON COMMIT DROP AS
SELECT g FROM generate_series(1, :mult) AS g(g);

-- One mode per seed mode and generation.
CREATE TEMP TABLE mode_clone_map ON COMMIT DROP AS
SELECT m.mode_id AS orig_id,
       substr(md5(m.mode_id || ':m' || gen.g), 1, 16) AS new_id,
       gen.g
FROM (SELECT DISTINCT mode_id FROM seed_anchor) m
CROSS JOIN gen;

INSERT INTO ionization_mode (
    ionization_mode_id, ionization_mode_name, ionization_mode_token,
    ionization_mode_polarity, ionization_mechanism_ids, calibration_collection_id,
    diagnostic_collection_id, system_key, instrument, instrument_id, version,
    is_active, supersedes_id, label_purity
)
SELECT mcm.new_id,
       im.ionization_mode_name || ' ' || :'marker' || ' m' || mcm.g || ']',
       im.ionization_mode_token, im.ionization_mode_polarity,
       im.ionization_mechanism_ids, im.calibration_collection_id,
       im.diagnostic_collection_id, NULL, im.instrument, im.instrument_id,
       im.version, false, NULL, im.label_purity
FROM ionization_mode im
JOIN mode_clone_map mcm ON mcm.orig_id = im.ionization_mode_id
ON CONFLICT (ionization_mode_id) DO NOTHING;

CREATE TEMP TABLE anchor_clone_map ON COMMIT DROP AS
SELECT sa.id AS orig_id,
       substr(md5(sa.id || ':b' || gen.g), 1, 16) AS new_id,
       gen.g
FROM seed_anchor sa
CROSS JOIN gen;
CREATE INDEX ON anchor_clone_map (orig_id, g);

-- The anchors first with no isotope link, then the links, so a link never
-- points at a row not yet written.
INSERT INTO batch_peak
SELECT (jsonb_populate_record(
           NULL::batch_peak,
           to_jsonb(bp) || jsonb_build_object(
               'batch_peak_id', acm.new_id,
               'ionization_mode_id', mcm.new_id,
               'isotopologue_of', NULL
           )
       )).*
FROM batch_peak bp
JOIN anchor_clone_map acm ON acm.orig_id = bp.batch_peak_id
JOIN mode_clone_map mcm ON mcm.orig_id = bp.ionization_mode_id AND mcm.g = acm.g
ON CONFLICT (batch_peak_id) DO NOTHING;

UPDATE batch_peak clone
SET isotopologue_of = parent.new_id
FROM anchor_clone_map acm
JOIN batch_peak seed ON seed.batch_peak_id = acm.orig_id
JOIN anchor_clone_map parent
  ON parent.orig_id = seed.isotopologue_of AND parent.g = acm.g
WHERE clone.batch_peak_id = acm.new_id
  AND clone.isotopologue_of IS DISTINCT FROM parent.new_id;

INSERT INTO batch_peak_occurrence
SELECT (jsonb_populate_record(
           NULL::batch_peak_occurrence,
           to_jsonb(o) || jsonb_build_object(
               'batch_peak_id', acm.new_id,
               'owner_batch_peak_id', owner.new_id
           )
       )).*
FROM batch_peak_occurrence o
JOIN anchor_clone_map acm ON acm.orig_id = o.batch_peak_id
LEFT JOIN anchor_clone_map owner
  ON owner.orig_id = o.owner_batch_peak_id AND owner.g = acm.g
ON CONFLICT (batch_peak_id, sample_item_id) DO NOTHING;

COMMIT;

ANALYZE batch_peak;
ANALYZE batch_peak_occurrence;

SELECT count(*) AS anchors,
       sum(n_present) AS members
FROM batch_peak
WHERE sample_batch_id = :'batch';
