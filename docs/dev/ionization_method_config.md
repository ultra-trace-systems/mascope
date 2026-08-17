# Ionization and acquisition-method configuration - design

Status: **design agreed in outline, Phase 0 partially shipped.** The target
model (sections 4-8) is still a proposal; nothing in it is locked in, and the
open decisions in section 10 are unanswered.

This is a design for extending and clarifying what users currently configure as
an "ionization mode", and for making the resulting data FAIR. It assumes the
peak-centric paradigm of `epic/peak-centric-assignment` as the direction of
travel and calls out the seams where the two meet.

## Picking this up

Read sections 1 and 3 for the thesis, section 9 for the plan, section 10 for
what still needs a human decision. Current state, last verified 2026-07-28:

| Phase 0 item | State |
|---|---|
| 1. Harvest `scan_parameters()` into `.props` | **Shipped** (PR #1723) |
| 2. Bump opentfraw off 1.2.0 | **Already done on develop** - see below |
| 3. Populate `method_file` | **Open, unblocked, next** |
| 4. Fix `delete_instrument_config` fan-out | **Open, needs a decision** (2.2) |
| 5. File upstream opentfraw issues | **Open** |
| 6-9. Presets, notation, pane UX, run-config stamp | **Open** |

Phases 1-4 are untouched and gated on section 10.

**Correction to what this document originally said about opentfraw.** It was
written when `libraries/thermo/pyproject.toml` pinned `opentfraw~=1.2` with the
lock at 1.2.0. Develop has since moved to `opentfraw~=1.3`, resolving **1.3.7**,
via a routine dependency-group bump. So `sample_info` and
`instrument_method_text()` are available *today*, and item 2 needs no work.

Two caveats that bump left behind, neither of them addressed:

- The **numeric A/B this document recommended as the gate for that bump was
  never run.** The version moved 1.2.0 -> 1.3.7, which includes the 1.3.5
  allocation-bounds hardening, as an unreviewed dependency update. Peak and
  centroid output was never compared. Acquisition-parameter output was verified
  identical across the two versions, and the library suites pass, but that is
  not the same check.
- The demo reproducibility manifest may still pin `opentfraw==1.2.0`. If so the
  nightly reproducibility job is failing. It lives in the shared `MASCOPE_PATH`,
  not the worktree, so it could not be checked from here.

Earlier drafts of this document also carried two claims that were **wrong and
have been corrected** under adversarial verification: the dependency-ownership
claim in 6.1 (upstream is third-party, not ours) and the consequence attributed
to the empty `method_file` in 2.2 (it does not cause cross-method peak-shape
reuse in analysis).

---

## 1. The problem, named properly

"Ionization mode" is one entity doing three unrelated jobs, and doing none of
them completely.

| Job | Today | Trouble |
|---|---|---|
| **Routing** - which config applies to this file? | `ionization_mode_token`, a filename substring | Fragile, and it is the *only* path. Forces configuration into filenames. Blocks upload entirely when absent. |
| **Chemistry** - which ion species do we look for? | `ionization_mechanism_ids`, a JSON list of notation strings | The one part that genuinely needs human expertise, and it gets the weakest UI in the app: a single free-text box. |
| **Physics** - polarity, mass range, resolution, tolerances | `ionization_mode_polarity`, plus two target collections | The rest of the physics lives in five other places. |

An ionization mode has exactly **five configurable fields**
(`db/models.py:760`). A mechanism has **one**: a notation string
(`db/models.py:743`). That is the entire parameter set. Everything a user would
call "how this measurement is set up" is scattered:

| Parameter | Home | Scope |
|---|---|---|
| adduct panel, polarity, calibrant/diagnostic collections | `IonizationMode` | global, per sample via `SampleItem.ionization_mode_id` |
| peak shape, resolution function | `InstrumentFunction` | `(instrument, method_file)` - see 2.2 |
| m/z tolerance, SNR floor, isotope-abundance min | `TofMatchParams` / `OrbiMatchParams` | instrument *class*, in code |
| Platt `a`/`b`, per-adduct corroboration weights | `AssignmentCalibration` (epic) | `(instrument string, score_version)` |
| m/z precision, formula ranges, tier bands | `PeakAssignmentConfig` (epic) | per run |
| electron mass, isotope threshold, `R_LOW`, `^N` purity 0.98 | module constants | global, code-only |
| polarity, mass range, m/z calibration | `SampleFile` | per file, read from the file |

Six homes. No wonder it is hard to grasp: there is no single object a user can
point at and say "that is my method".

### The insight the redesign turns on

**The file already knows most of the physics.** Polarity, mass range and m/z
calibration are extracted today. Instrument model, serial, software version,
resolution setting, AGC and injection time are read at runtime and thrown away
(`libraries/thermo/src/mascope_thermo/backend.py:245`, `:260`, `:492`). The
Thermo instrument method is embedded in every `.raw` file already.

So: **stop asking the user for what the file can tell us, and ask only for what
it cannot** - the chemistry. Then give that the best UI in the app, seed it with
presets, and let the assignment ledger suggest improvements over time.

---

## 2. Evidence

### 2.1 It is genuinely confusing, and we have proof

The notation was **documented wrong in shipped UI text** until 2026-07-27
(`126b7136`), and the user docs had to be corrected twice more (`f7119ae6`,
`408e906d`). The commit message is the clearest statement of the problem:

> The trailing sign is the charge of the transferred species, not the ion
> polarity; the ion polarity is derived. Deprotonation is therefore `-H+`
> (removal of a proton `H+`), giving `[M-H]-`, not `-H-`.

If the developers had it wrong in production help text, users have no chance.
Other evidence:

- **A two-character input DoS'd the backend.** Typing `++` pinned the single
  worker at 100% CPU forever (`5745a0af`). A free-text field with no examples
  and no inline errors.
- **Churn on what may be edited**: `957f0086` -> `109b2658` (restrict to name
  and token) -> `b0d1c44f`/`23e106aa` (allow mechanisms and polarity) ->
  `78ee19ba` (admin-only, flags affected batches). An unresolved model.
- **A fresh instance cannot ingest a single file** until someone hand-types
  `+H+`. There are zero seeded mechanisms or modes.
- The mechanism pane has **no tooltip, no placeholder, no example, no error
  message** - only a red border driven by three booleans, so the user cannot
  tell which of the three rules failed
  (`PaneIonizationMechanism.vue:30-37`).
- The notation explanation exists **only** in a help card behind help mode
  (`alt+h`). The popover supports a "Learn more" doc link; the ionization cards
  pass none.
- The dialog opens on the **Modes** tab, which is step 2. The Mechanisms
  multiselect is empty until you have done step 1 on the other tab.
- `resolve_ionization_modes_by_peaks` - the data-driven alternative that would
  remove the token concept entirely - is a `NotImplementedError` stub with a
  TODO (`api/new/ionization/modes/util.py:87-108`).

### 2.2 A live data-loss path, found while researching this

`SampleFile.method_file` is **hardcoded to `""` for every Orbitrap file**:

```python
# libraries/thermo/src/mascope_thermo/processor.py:92-102
@property
def method_file(self) -> str:
    """Not exposed by the OpenTFRaw reader, so reported as empty."""
    return ""
```

`InstrumentConfig` lookups group by `(method_file, instrument)` taking
`max(datetime_utc)` (`api/new/instrument_configs/service.py:91-110`), so with
`method_file` always empty, every Orbitrap config on an instrument lands in one
bucket.

**An earlier draft of this document claimed that causes cross-method peak-shape
reuse in analysis. That is wrong, and adversarial verification caught it.**
Analysis never reads the peak shape through that grouping: `instrument_configs/
lib.py:24-37` resolves strictly by the per-sample-file FK
(`InstrumentConfig.instrument_function_id == sample_file.instrument_function_id`)
with no fallback, and every ingested file fits and stamps its *own* config
(`base_processor.py:291-316`, `:420-446`). No cross-method reuse occurs on the
analysis path. The grouping defect is API-surface only, and grep finds **no
consumer of that endpoint anywhere** - not in the frontend, not in tests.

Two real bugs do fall out of the empty `method_file`, though:

1. **Data loss.** `delete_instrument_config`
   (`api/new/instrument_configs/service.py:262-274`) deletes by
   `where(instrument == ..., method_file == ...)` and removes *every* match.
   With `method_file=""`, deleting one Orbitrap config deletes them all for that
   instrument. The FK is `ondelete="SET NULL"` (`db/models.py:445-449`), so every
   sample file on that instrument loses its `instrument_function_id`, and
   `lib.py:106-107` then raises "Instrument configuration not found". This should
   be fixed to delete by id regardless of the rest of this document.
2. **The backfill script does perform the reuse I wrongly attributed to
   analysis**: `db/scripts/populate_none_instrument_function_ids.py:61-77` copies
   the newest `InstrumentFunction` for the instrument regardless of method when
   `sf.method_file` is falsy. Scoped to rows whose FK was already NULL.

Populating `method_file` (Phase 0) narrows both.

### 2.3 A reproducibility hole in the epic

`PeakAssignmentRun` stores `engine_version` plus the full resolved `config` JSON
so runs are reproducible and comparable. But `PeakAssignmentConfig`
(`api/new/peak_assignments/config.py:55`) has **no ionization field**. The
adduct panel comes from `SampleItem.ionization_mode_id` at run time and is
never recorded.

Consequences:

- Two runs with byte-identical recorded config can produce different results,
  because an admin edited the mode in between.
- `AssignmentCalibration` holds per-adduct corroboration weights and its own
  docstring says a curve is valid "per instrument, per reagent chemistry" - yet
  it is keyed by `(instrument, score_version)` only, where `instrument` is
  derived from the *filename*. A calibration fit under one reagent setup and an
  assignment run under another **cannot be detected as mismatched.**

This is the single strongest argument for versioned setups, and it is worth
fixing regardless of the rest of this document.

---

## 3. Design principles

1. **Separate what the instrument did from how we interpret it.** The first is
   observed, immutable, and should be captured automatically. The second is
   configured, versioned, and needs a good UI.
2. **Speak the field's language, not ours.** `[M+H]+`, not `+H+`.
3. **Never lose provenance to an edit.** Configuration that affects results is
   append-only versioned, and the version is stamped on every result.
4. **Automate the physics, curate the chemistry.** The file gives the former;
   presets and a learning loop help with the latter.
5. **Parsing a vendor method yields suggestions, not truth.** Store the text
   verbatim always; treat any parse as a proposal the user confirms.

---

## 4. Target model

### 4.1 `Instrument` - promote to a first-class row

Today an instrument is `SELECT DISTINCT sample_file.instrument`, where the name
is `filename.split("_")[0]`. Delete the last file and the instrument
evaporates - along with any metadata about it. Meanwhile the epic wants to hang
calibration curves off it.

```
Instrument
  instrument_id, name (filename-derived key, kept for back-compat)
  instrument_type (orbi | tof), vendor, model, serial_number, software_version
  cv_term            -- PSI-MS, e.g. MS:1000449 (LTQ Orbitrap)
  created_utc, last_seen_utc
```

Model is available from the reader today and discarded. **Serial number and
software version are not** - see 6.1; they are reachable only on the optional
pythonnet/DLL backend, and getting them on the default path is new binary-format
work upstream. Make those columns nullable and treat them as a later fill.

Promoting the row is still worth it on its own: it also satisfies the FAIR
requirement that metadata outlive the data it describes.

### 4.2 `AcquisitionMethod` - observed, immutable, auto-created

"What the instrument was told to do." Created automatically on ingest, keyed by
a stable identity taken from the file.

```
AcquisitionMethod
  acquisition_method_id, instrument_id
  method_name          -- e.g. "Standard_HCD.meth"
  method_hash          -- content hash; the real identity
  polarity, mz_range_low, mz_range_high
  resolution_setting, scan_type, source_type (cv_term)
  method_text          -- TEXT, verbatim, provenance
  method_params        -- JSON, opportunistically parsed
  source               -- extracted | uploaded | manual
  first_seen_utc
```

This is where the "upload a method file" idea lands - see section 6. Its most
valuable property is not the parsed fields but the **stable identity**, which is
the correct routing key and replaces the filename token.

### 4.3 `IonizationSetup` - the evolved ionization mode, versioned

The human-supplied interpretation layer. Renamed because "mode" now reads as
polarity to most users, and because the scope genuinely widens.

```
IonizationSetup (append-only versioned)
  setup_id, version, is_active, supersedes_id
  name, description
  polarity
  adducts[]              -- ordered, structured (4.4), each with an optional prior
  charge_states_allowed  -- finally more than +/-1
  reagents[]             -- custom elements WITH purity as config, not a constant
  calibration_collection_id, diagnostic_collection_id
  overrides              -- optional: mass range of interest, tolerance,
                         --   formula element ranges, tier bands
  created_by, created_utc
```

Versioning is the load-bearing change. `AssignmentCalibration` is already
append-only with an `is_active` flag - this is the same pattern, and it closes
2.3. Every `PeakAssignmentRun`, match run and calibration stamps the
`(setup_id, version)` it used.

### 4.4 `Adduct` - structured, replacing the notation string

The current model is one `String(256)` parsed by **two different parsers with
divergent grammars** (`target_ions_compute._mechanism_parts` and
`mascope_tools.composition.utils.parse_ionization`, which special-cases `-H-`,
a string the backend validator will never produce).

```
Adduct
  adduct_id
  operation        -- add | remove
  species          -- formula: H, Br, NO3, NH4, CH4N2O
  species_charge   -- int, may be +/-2
  multimer_n       -- default 1; enables [2M+H]+
  neutral_loss     -- optional; enables [M+H-H2O]+
  name             -- "protonation", "bromide adduct"
  legacy_notation  -- "+H+", retained: see 8.1
  -- derived: ion_polarity, ion_charge, mass_shift, display "[M+H]+"
```

Derived, not stored: ion polarity and charge. That removes the exact confusion
that caused the doc bugs - the user never types a sign whose meaning is
ambiguous.

### 4.5 `MethodBinding` - routing, decoupled from definition

How a file gets a setup. Resolution order:

1. explicit user assignment on the sample
2. **acquisition-method identity** (the new, correct key)
3. instrument + polarity, where unambiguous
4. filename pattern - the legacy token, demoted to a fallback

Polarity always comes from the file, never from the filename.

---

## 5. Notation: adopt `[M+H]+`

Mascope's `+H+` grammar is a private dialect. It is:

- **non-standard** - the literature, vendor software and every other tool write
  `[M+H]+`, `[M-H]-`, `[M+Br]-`;
- **ambiguous in practice** - the trailing sign is the transferred species'
  charge, not the ion polarity, which confused our own developers;
- **inexpressive** - cannot represent `|z| > 1`, multimers (`[2M+Na]+`) or
  neutral losses (`[M+H-H2O]+`), all of which are routine.

Recommendation: make `[M+H]+` the **primary input and display form**, backed by
the structured model in 4.4. Accept the legacy `+H+` form on input and keep it
stored as `legacy_notation`.

This is the highest clarity-per-line-of-code change in the document, and it is
simultaneously an interoperability win (section 7).

---

## 6. Capturing the acquisition method

### 6.1 For Thermo, no upload is needed - the method is already in the file

Every `.raw` carries its instrument method as an embedded blob, gated on a
`method_file_present` flag.

**Dependency ownership.** Upstream is `Sigilweaver/OpenTFRaw`, a **third-party**
project. `ultra-trace-systems/OpenTFRaw` is our fork, published to PyPI under the separate
name `mascope-opentfraw`. Mascope currently depends on **upstream** `opentfraw`
from public PyPI (verified: the installed dist-info names Nathan Riley and
`Sigilweaver/OpenTFRaw`; there is no `direct_url.json`), having deliberately
moved off the fork in `f7fba6c6`. So anything missing upstream is a PR we must
land there, or a decision to re-fork - not something we merge ourselves.

**The accessors are already released upstream, and we already have them.** PR
#18 (merge `8594c6bf`, 2026-07-08) first shipped in **upstream v1.3.0**.
`libraries/thermo/pyproject.toml:11` now pins `opentfraw~=1.3` and the lock
resolves **1.3.7**, so the whole surface below is live today. (This section
originally described the bump as pending work; see "Picking this up" at the top
for what that means for Phase 0 item 2 and for the revalidation it skipped.)

Relative to the old 1.2.0 the Python surface gains 8 members, 0 removals, 0
renames, 0 signature changes: `sample_info`, `instrument_method_text()`,
`status_log(n)`, `error_log()`, `controllers()`, `controller_count`,
`computer_name`, `acquisition_date`.

Two important corrections to the naive reading of that list:

- **There is no `instrument_method_name` in the Python binding at any version.**
  The method *name* arrives as `sample_info["inst_method"]`, verified real on
  both Mascope test fixtures
  (`C:\Xcalibur\methods\5.1 Methods\ambient_pos_massrange40-500.meth`). The rest
  of the sequence row is empty on our data - `comment`, all five `user_labels`
  and `proc_method` decode to empty strings - so the win is the method name, not
  the row.
- **`instrument_method_text()` is currently unreliable. Do not build on it.**
  It is a heuristic, not a structured read: it scans the first 512 KB for the
  *longest* contiguous run of >=256 UTF-16LE code units. On our own Exploris 120
  fixture the winner is a 9991-unit block at offset 18006 that is single-byte
  ASCII XML misread as UTF-16 - pure mojibake - while the genuine, fully readable
  method summary is an 854-unit run at offset 39064:

  > `Method Duration (min) = 0.15 / Spray Voltage = Static / Gas Mode = Static /
  > FAIMS Mode = Not Installed / Application Mode = Small Molecule /
  > Orbitrap Resolution = 120000 / Scan Range (m/z) = 40-500`

  9991 > 854, so the mojibake wins, and both pass strict UTF-16 validation.
  Upstream's only test is a type check. **The data we want is in the file; the
  extractor picks the wrong block.** File this upstream with the offset
  reproducer before depending on it. The principled fix is an OLE2/CDF decoder
  for the `MethodFile` container, which does not exist in the crate.

**Instrument serial number and software version are still not available**, and a
bump does not change that. The `InstID` block carrying them is *documented* in
the upstream format docs, but no parser has ever existed in any ref of the repo,
and no upstream issue tracks it. Our TODO at `backend.py:1004-1016` stands, and
`INSTRUMENT_FIELDS` keeps yielding `None` on the OpenTFRaw path. (They *are*
reachable on the optional pythonnet/DLL backend via `GetInstrumentData()`.) This
is new binary-format work on someone else's project - treat it as a long-term
upstream ask, not a plan dependency.

### 6.2 The richest source needs no version bump at all

The most useful finding of the whole investigation: **`scan_parameters(n)` was
already available at the old 1.2.0 pin and we threw nearly all of it away.**
This is the part now shipped, in PR #1723 - see `acquisition_parameters()` in
`libraries/thermo/src/mascope_thermo/backend.py` and the resulting
`acquisition_params` block in `.props`.

It returns the instrument's own trailer-extra dictionary - **78 typed entries**
on the Exploris fixture, including:

```
Application Mode:      'Small Molecule'
FT Resolution:         120000
AGC Target:            10000000
S-Lens RF Level:       70.0
Source CID eV:         0.0
FAIMS Attached / Voltage On / CV
Analyzer Temperature:  31.69
Multi Inject Info:     'IT=250;250'
```

Mascope reads **five** keys off `scan()` (`backend.py:287-293
_OTF_TRAILER_FIELDS`) and four labels via `scan_parameters()` at `:1446`/`:1725`.
Everything else is discarded.

For an acquisition-method redesign this is better than the method text: it is
already structured and typed, it needs no bump, no upstream PR, and no OLE2
decoder. `Application Mode`, `FT Resolution` and the FAIMS/S-Lens/source-CID
group are precisely the acquisition parameters section 4.2 wants. **Start here.**

### 6.3 Be honest about what a method file gives you

**It can give**: polarity, mass range, resolution setting, scan type and
segments, source type, spray voltage, capillary temperature, AGC target and
injection time - and a stable identity for the acquisition setup.

**It cannot give the adduct panel.** That is chemistry knowledge, not instrument
configuration. Reagent gas lines are frequently not in the method at all. Any
pitch of "upload your method and we'll configure the pipeline" must not promise
this, or it will disappoint exactly the users we are trying to help.

**The text is a printed report**, semi-structured and varying with instrument
model and firmware. So: store verbatim always (cheap, and high provenance
value), parse opportunistically into *suggestions the user confirms*, never
silently trust a parse.

### 6.4 Tofwerk

`method_file` is populated from the h5 root attr `Configuration File`, but that
is a **name only** - no content. The entire `Instrument Data` group (voltages,
pressures, temperatures) is never read, and the file object is opened raw so
every attribute is one `attrs[...]` away
(`libraries/tofwerk/src/mascope_tofwerk/tofwerk.py:14-34`).

The TofDaq configuration is a genuinely separate file, so **Tofwerk is the real
upload case** - the one place where asking the user for a method file is the
only option.

### 6.5 The learning loop - closing the gap the method file leaves

The epic's `PeakAssignment` ledger records which mechanism won each peak. That
is a direct signal for the one thing the method file cannot supply:

- "`[M+Br]-` won 12% of assigned peaks under this method but is not in your
  panel - add it?"
- "`[M+(CH4N2O)H]+` is in your panel but has won 0 peaks across 40 samples -
  remove it?"

The same aggregation can refit the per-adduct corroboration weights, which are
currently provisional and hand-fit
(`PROVISIONAL_ORBITRAP_CORROBORATION`, `calibration.py:295`).

---

## 7. FAIR

Current state is close to zero. An exhaustive search for
`mzML|mzTab|PSI-MS|OBO|ontolog|ORCID|RO-Crate|ISA-Tab|schema.org|JSON-LD|ChEBI`
across the repo returns only `CITATION.cff` hits - which identify *the software*,
never user data. Compounds carry CAS numbers and nothing else: no InChI, no
InChIKey, no SMILES. Batch exports omit instrument, method, polarity, mass range
and calibration entirely.

The useful framing: **F and A are mostly about publishing and are largely out of
scope; I and R are about vocabulary and provenance, and those are exactly what
this redesign already does.** FAIR is not a separate workstream here - it is the
same work, described differently.

| | Gap | Action | Cost |
|---|---|---|---|
| **R1** | Results do not record the config that produced them (2.3) | Versioned setups, stamped on every run | in Phase 1 |
| **R2** | No software version, engine version or method identity on results or exports | Provenance block on every result and export. Instrument serial is blocked upstream (6.1) - ship the block without it | low |
| **R3** | Metadata dies with the data - deleting the last file deletes the instrument | `Instrument` table (4.1) | in Phase 1 |
| **I1** | No controlled vocabulary anywhere | PSI-MS CV terms for instrument model, source, analyzer, detector, polarity. The mapping **already exists** in `opentfraw`'s `mzml.rs` | low |
| **I2** | Private adduct dialect | `[M+H]+` (section 5) | in Phase 1 |
| **I3** | CAS only | InChIKey + SMILES on `target_compound` | low |
| **I4** | No standard export | `RawFile.to_mzml()` **already exists in the pinned dependency and is never called** - one route away | very low |
| **F/A** | No PIDs, no metadata manifest | Stable exportable IDs + per-dataset manifest (RO-Crate or mzTab-M) | medium |

I4 deserves emphasis: standards-compliant mzML export is currently a single
unused method call.

---

## 8. Interaction with `epic/peak-centric-assignment`

The epic changes **nothing** in the ionization models themselves - it only adds
consumers. That is good news for sequencing. Three seams need care:

### 8.1 Corroboration weights are keyed by the notation string

`AssignmentCalibration.corroboration_weights` is JSON keyed by the raw notation:
`{"+Br-": 2.28, "+NH4+": 0.83}`. Changing the primary notation must therefore
either keep `legacy_notation` as the stable key (recommended - it is why 4.4
retains the field) or migrate the JSON in the same transaction.

### 8.2 Calibration keying should include the setup

`(instrument, score_version)` should become
`(instrument_id, ionization_setup_version, score_version)` - or, as a cheaper
first step, simply *record* the setup version so a mismatch is detectable rather
than silent.

### 8.3 Run config must include the setup version

One field on `PeakAssignmentConfig`. This is small, self-contained, and closes
2.3 on its own.

**Sequencing recommendation:** do the foundation on `develop`. Both paradigms
benefit, the epic is flagged off by default (`MASCOPE_PEAK_ASSIGNMENT`), and
rebasing 21k lines of epic onto a changed ionization model is far easier than
the reverse. Stitch 8.1-8.3 on the epic branch afterwards.

---

## 9. Plan

### Phase 0 - quick wins, no schema change (days)

Independently valuable, ships before any of the redesign lands.

1. ~~**Harvest `scan_parameters()`**~~ - **SHIPPED, PR #1723.** 78 acquisition
   parameters now reach `.props` per sample file via
   `ReaderBackend.acquisition_parameters()`. Implemented as a new protocol
   member rather than by widening `_OTF_TRAILER_FIELDS`, whose shape
   `scan_acquisition_settings()` pins and which has a live consumer.

2. ~~**Bump opentfraw**~~ - **ALREADY DONE on develop**, but not deliberately:
   it arrived as a routine dependency-group bump, so `opentfraw~=1.3` / 1.3.7 is
   in place while two things this document asked for were skipped.
   - **The numeric A/B was never run**, and still should be. The m/z and
     intensity functions (`freq_to_mz`, `noise_at`, `read_peaks_only`) are
     byte-identical across the versions and the rest is allocation-bounds
     hardening, so risk is low - but that is a source-level inference and peak
     positions feed calibration and assignment. Dump `peaks`/`profile`/
     `centroid_labels` for a scan sample from
     `libraries/thermo/tests/test_files/KORBI2_AMB_POS_*.raw` under 1.2.0 and
     1.3.7 and assert bit-identical arrays. Do this before trusting any
     downstream result that predates the bump.
   - **Check the demo reproducibility manifest.** If
     `.runtime/demo/*/manifest.json` still records `opentfraw==1.2.0`, the
     nightly reproducibility job is failing; refresh with
     `mascope demo snapshot --update`. The manifest lives in the shared
     `MASCOPE_PATH`, not in a worktree.

3. **Populate `method_file` from `sample_info["inst_method"]`.** The next thing
   to build. `processor.py:116` still returns `""` behind a docstring saying the
   reader does not expose it - no longer true; on the committed fixture it
   returns `C:\Xcalibur\methods\5.1 Methods\ambient_pos_massrange40-500.meth`.
   Not a one-line edit: `processor.py` talks to the `ReaderBackend` protocol,
   which has no such member, so it needs a new protocol method implemented in
   **both** `OpenTFRawBackend` and `ThermoBackend` (the latter already has the
   datum as `SampleInformation.InstrumentMethodFile`). It also requires updating
   the `test_raw_processor.py` assertion that pins `props.method_file == ""`.

4. **Decide what `delete_instrument_config` should do**, then fix it (2.2). The
   route already takes an id; the service then deliberately fans out to every
   config sharing `(instrument, method_file)`. With a real method file that is
   defensible; with `method_file == ""` it means "delete every config for this
   instrument" and strands every sample file behind an `ondelete="SET NULL"` FK.
   Either guard the empty-key case or drop the fan-out - **this needs a human
   call on intent, not a unilateral patch.** Item 3 removes the sharp edge but
   not the ambiguity.

5. **File the upstream issues** while the rest proceeds: the
   `extract_utf16le_text` mojibake bug with the offset-18006-vs-39064 reproducer,
   and a binding for `method_file_present` (without it we cannot distinguish
   "no method embedded" from "extraction failed"). Upstream is
   `Sigilweaver/OpenTFRaw`, third-party - these are contributions, not merges we
   control.
6. **Ship an adduct preset library** and one-click setup: ESI+
   (`[M+H]+`, `[M+Na]+`, `[M+K]+`, `[M+NH4]+`, `[2M+H]+`, `[M+H-H2O]+`), ESI-
   (`[M-H]-`, `[M+Cl]-`, `[M+HCOO]-`, `[2M-H]-`), the reagent chemistries we
   actually use (`[M+Br]-`, `[M+NO3]-`, `[M+(CH4N2O)H]+`), EI (`M+.`). Grouped
   into named presets. **The highest-value item on the usability side** - it
   turns a blank wall into a starting point. (Item 1 is its counterpart on the
   metadata side.)
7. Mechanism pane UX: inline per-rule error messages, worked examples in the
   field, accept `[M+H]+` on input.
8. Add the `doc` "Learn more" link to the ionization help cards - the popover
   already supports it and ionization passes none.
9. Stamp the resolved mechanism ids into `PeakAssignmentRun.config` (epic
   branch). Partial mitigation of 2.3 until versioning lands.

### Phase 1 - name the concept (weeks)

10. `Instrument` table; migrate the derived-string usages.
11. Structured `Adduct` model; `[M+H]+` primary, legacy string retained.
12. `IonizationSetup`, append-only versioned; stamp the version on runs, matches
    and calibrations. **Closes 2.3, R1, R3.**
13. Reagent purity and allowed charge states become configuration rather than
    module constants.
14. Retire one of the two mechanism parsers.

### Phase 2 - method capture (weeks)

15. `AcquisitionMethod` entity, auto-created from the file on ingest, built on
    the `scan_parameters()` harvest from item 1 plus `sample_info["inst_method"]`
    as the identity.
16. Parse method text into suggestions, presented for confirmation - **gated on
    the upstream mojibake fix (6.1) landing.** If it does not, this item degrades
    gracefully: items 1 and 15 already carry the acquisition parameters.
17. Upload path: Tofwerk/TofDaq config, and a manual override when extraction
    fails.
18. Route by method identity; demote the filename token to a fallback.

### Phase 3 - FAIR (parallel, incremental)

19. PSI-MS CV terms; InChIKey and SMILES on compounds.
20. Provenance block on results and exports.
21. mzML export route (I4 - nearly free).
22. Per-dataset metadata manifest.

### Phase 4 - the loop

23. Adduct-panel suggestions from the assignment ledger.
24. Refit corroboration weights per `(instrument, setup)`.

---

## 10. Decisions needed before Phase 1

These are product calls, not technical ones, and they change the schema:

1. **Scope of a setup.** Global (as today), per workspace, or per instrument?
   Target collections are already workspace-scoped while modes are global, so
   the current split is at least inconsistent.
2. **Migration.** Existing modes are referenced by samples and cannot be
   deleted. Versioning means minting a v1 for each and backfilling every
   `SampleItem`. Acceptable, or do we need a compatibility shim?
3. **The filename token.** Keep indefinitely as a supported path, or
   hard-deprecate once method routing works? File Agent uploads depend on it
   today.
4. **Parse depth.** How much of the method text do we parse into fields versus
   store and display verbatim? Suggest starting at "store, display, parse only
   polarity / mass range / resolution".
5. **FAIR ambition.** Internal reproducibility (R) only, or publishable outputs
   with PIDs and repository deposition (F/A)? This decides whether Phase 3 stops
   at item 18 or continues.
