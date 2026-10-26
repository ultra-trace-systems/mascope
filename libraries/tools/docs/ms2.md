# MS2 readings: fragment sub-formulas with loss decomposition

*Reference for `mascope_tools.composition.ms2.readings`: `read_fragments`,
`ladder_gate` and the functions under them. The rules are peaky's
(`peaky/ms2/subformula.py` and `peaky/ms2/readings.py`, peaky PRs 78 and 80),
ported with peaky's known answers as tests
(`libraries/tools/tests/test_ms2_readings.py`), so that the two engines read one
spectrum alike. The design note is `docs/dev/ms2_analysis.md`, section 2.4.*

> Keep this in sync with the code. Every threshold below is a named constant in
> `readings.py`; `test_ms2_readings.py` fails when one is missing here or its
> value differs.

## 1. What it is - and what it is not

A fragment of a singly charged precursor is an **element-wise sub-formula of
the precursor ion** - the charge stays on the fragment - and the difference is
the neutral the precursor lost. A plain sub-formula search accepts any
difference, including ones no molecule has (a bare `C4`). This module accepts a
reading only when its loss **decomposes into at most `k_max` molecules**, and
ranks the readings of one peak against one parent.

It is a **composition test**, applied to every ion of the isolation window (the
target and its co-isolates) with the same rules. It never decides *which*
window ion a fragment came from: a peak two window ions can both explain keeps
both readings (`n_parents` 2). Attribution needs intensities across collision
energies and is a later module's (`ms2.attribute`, MAS-399). Harmonic ghosts
and side-lobe satellites, centroids that are no ions at all, are flagged by
another (MAS-388); this one reads every peak it is given.

```
MS2 peaks (m/z [+ intensity])           window ions {label: ion formula}
        |  apply_offset: mz_cal = mz / (1 + offset_ppm * 1e-6)
        v                                         | per parent, once per call:
   for each peak x parent <-----------------------+ all sub-formulas -> fragment gate
        |  bisect the parent's sorted sub-formula masses
        v  within max(ppm * mz_cal * 1e-6, mda * 1e-3)
   candidate fragment F  ->  loss L = parent - F  (element-wise)
        |  decompose L into <= k_max molecules (library + <= 1 unnamed piece)
        v  no decomposition -> not a reading
   ranked readings per (peak, parent)  ->  rows (every peak appears)
        |  one window's spectra together, read at k_max 4
        v
   ladder_gate: a fourth piece only on an observed ladder
```

## 2. Inputs

- **Peaks**: a 1-D array-like of m/z, or a mapping or frame with an `mz` column
  and optionally `intensity`. Every m/z must be finite and positive. A 2-D input
  (an `(n, 2)` centroid array, `(mz, intensity)` pairs) raises: flattened, every
  intensity would be read as one more m/z.
- **Window ions**: one ion formula, a sequence of them (each labelled by
  itself), or an ordered mapping `label -> ion formula`: the target plus the
  co-isolated window ions. Only strings: every one goes through `parse_ion`'s
  checks. Labels must be unique after `str()`, and all ions must share one
  polarity. Each ion is given once: one ion under two labels or two spellings
  (`C10H17+` and `H17C10+`, `C7H8+` and `C7H8+.`, `[15N]` and `^N`) raises, as
  it would be read as two parents and composition cannot tell isomers apart.
  Which ions are a window's is the caller's to say: they are named, never
  guessed (the note's decision 4).
- **`offset_ppm`** (default `0.0`): the spectrum's constant-ppm axis offset,
  usually `mass_offset` on the surviving precursor.
- **`tolerance`** (`Tolerance(ppm=READING_TOLERANCE_PPM, mda=READING_TOLERANCE_MDA)`),
  **`k_max`** (`K_MAX_DEFAULT`), **`allow_radical`** (`True`), **`spectrum`**
  (`''`, the caller's key for the spectrum, which `ladder_gate` groups by:
  give each spectrum of a window its own).

**Notation.** Formulas are written in Mascope's notation: Hill order as
`utils.to_hill_order` writes it (C first, H second, the rest alphabetically),
an ion with exactly one sign (`'C9H11+'`, `'C10H17O5-'`), 15N as `[15N]`
(`'C4H10[15N]O4+'`). Peaky's caret label `^N` is accepted on input, in an ion,
a loss or a counts mapping, and written as `[15N]`. 15N is a **distinct
element** everywhere - in masses and in counts - so a labelled-ammonium adduct's
`H3[15N]` loss (18.0236) is never confused with `H2O` (18.0106) or `H3N`
(17.0265). The elements read are `ELEMENTS`: C, H, O, N, S, P, Si, F, Cl, Br,
I and [15N]; any other symbol, another isotope label included, raises.

## 3. The rules, step by step

1. **Parse the ions** (`parse_ion`, `format_ion`). Exactly one `+` or `-` is
   required: a formula without one is a neutral passed by mistake, and `2+` or
   `++` would be a doubly charged ion, which nothing here models. A trailing
   radical mark (`'C7H8+.'`, a bullet or a middle dot) is accepted and
   **checked**: on an even-electron formula it is a typo and raises. Formula
   bodies - of ions, and of loss strings given to `decompose_loss` - are parsed
   **strictly**: the whole string must be element tokens and every symbol known.
   `utils.parse_composition` skips what it cannot read (`'C2H4o2'` -> C2H4,
   `'H2O-H2'` -> H4O); here both raise.

2. **Recalibrate** (`mass_offset`, `apply_offset`). Anchors are
   `(measured_mz, ion_formula)` pairs. `offset_ppm` is the **median** anchor
   error `(measured - theoretical) / theoretical * 1e6` (positive: the axis
   reads high; with the usual two anchors, precursor and reagent ion, the
   median is their mean). The theoretical m/z is `ion_mz` of the anchor's own
   formula and charge, so an anion anchor (`'[15N]O3-'`, 62.98540) sits two
   electron masses above the cation of the same formula. Each anchor's residual
   is the same error re-read on the corrected axis, so an outlier keeps its
   sign. `apply_offset(mz, offset_ppm) = mz / (1 + offset_ppm * 1e-6)`. A
   **constant-ppm** model only: one or two anchors cannot fit a low-mass
   absolute term - that is what the tolerance's mDa floor absorbs.

3. **The sub-formula grid of a parent** (`_parent_grid`, cached per
   composition AND charge - one formula as a cation and as an anion is two
   grids). `read_fragments` fetches each window ion's grid once per call, so a
   window of more ions than the cache holds does not rebuild them peak by peak.
   All element-wise sub-formulas (one numpy meshgrid over `0..count` per
   element), then the **fragment gate**, one vectorised function that both the
   grid and `fragment_ok` call:
   - at least one non-H atom;
   - DBE >= `FRAGMENT_DBE_MIN_EVEN` (-0.5) when even-electron (H3O+, NH4+ and
     every protonated saturated molecule sit at -0.5), DBE >=
     `FRAGMENT_DBE_MIN_ODD` (0) when odd-electron;
   - DBE <= Senior's cap + `FRAGMENT_SENIOR_MARGIN` (0.5), the cap being
     `SENIOR_CAP_BASE` + C + Si + (N + [15N]) / 2 (`SENIOR_CAP_PER_ATOM`; P adds
     to the DBE but not to the cap).

   Masses are the atoms' monoisotopic masses (pyteomics, Mascope's source)
   minus `charge` electron masses (`config.ELECTRON_MASS`), sorted ascending.
   The **parent itself is never gated**: its composition is an input, and an
   ammonium cluster of a saturated molecule sits at ion DBE -1.5
   (`C6H18[15N]O2+`, C6H14O2 + 15NH4+, a co-isolate of peaky's exploratory
   fixture) yet is its own precursor peak. A grid above `GRID_MAX_ROWS` rows is
   refused - by `read_fragments` when it reads the window ions, before any peak.

4. **The window.** `tol = max(ppm * mz_cal * 1e-6, mda * 1e-3)` Da
   (`Tolerance.window_da`); every grid row with `|mass - mz_cal| <= tol` is a
   candidate. `ppm = (mz_cal - mass) / mass * 1e6`. Why the floor: an
   Orbitrap's low-mass error is an absolute offset; peaky's exploratory fixture
   read its C2H3O+ fragment at -3.8 and -4.1 ppm (-0.16 and -0.18 mDa) after the
   precursor recalibration. A pure 5 ppm window is 0.21 mDa at m/z 43; the
   0.3 mDa floor makes it 7 ppm there and is inert above m/z 60.

5. **The loss and its parity.** `L = parent - F`, element-wise. DBE is affine
   in the counts, so **DBE(L) = DBE(parent) - DBE(F) + 1**: a loss between two
   ions of the same electron parity has an integer DBE (closed-shell); between
   ions of opposite parity a half-integer one (a radical). `F = parent` is the
   precursor reading (`is_precursor`, loss `''`, 0 pieces).

6. **Decompose the loss** (`decompose_loss`, memoised per `(loss, k_max)`).
   - **Necessary bound.** k closed-shell molecules of DBE >= 0 sum to
     DBE(L) + k - 1, so k >= max(1, ceil(1 - DBE(L))); one radical piece
     (DBE >= 1/2) plus closed ones gives k >= max(1, 1.5 - DBE(L)), which for
     the radical loss's half-integer DBE is the same number. A loss whose bound
     exceeds `k_max` is refused before any search: H4O (DBE -1)
     needs 2 (H2O + H2), H5[15N]O needs 2, H7[15N]O2 (DBE -2) needs 3, H8
     (DBE -3) needs 4.
   - **Pieces.** Library formulas (`CLOSED_SHELL_LOSSES`, and for a radical loss
     exactly one of `RADICAL_LOSSES` or one unnamed radical remainder) plus **at
     most one unnamed remainder**. A closed-shell loss holds no radical piece
     and a radical loss never two: [15N]O + [15N]O2 ([15N]2O3) is no reading at
     any `k_max`, nor [15N]O + [15N]O2 + [15N]O3 ([15N]3O6) at 3 or 4. The 14N
     formulas do read, through the named N2O: N3O6 as `O2 + N2O + NO3` - a
     composition, not a claim that N2O left.
   - **Unnamed closed-shell remainder**: integer DBE >= 0; Senior's second rule
     - the sum of valences (`VALENCES`: C, Si 4; N, [15N], P 3; O, S 2; H, F,
     Cl, Br, I 1) >= 2 x the largest valence present; and **at least one H or
     halogen** (`H_OR_HALOGEN`). The last clause refuses bare-skeleton pieces
     (`C4`, `C`, `O`, `C2O`); `CO`, `CO2`, `N2`, `O2`, `O2S`, `CS2`, `COS` and
     `N2O` are library pieces, so they stay possible. A halogen-only piece
     passes: `I2` and `Br2` are not in the library but read as one unnamed
     piece, which is the iodide reagent's declustering (I3- -> I-, an
     [M-H+I2]- adduct -> [M-H]-).
   - **Unnamed radical remainder**: half-integer DBE >= 1/2; the sum of
     valences >= 2 x the largest - 1 (CH3: 7 >= 7, HO: 3 >= 3); H or halogen.
   - **Choice** (deterministic): fewest pieces, then fewer unnamed pieces, then
     library priority (the ascending list of the pieces' library indices,
     compared lexicographically). The name lists the pieces in that order, the
     unnamed remainder last: `H5[15N]O -> 'H2O + H3[15N]'`,
     `CH6 -> 'H2 + CH4'`, `H4O2 -> 'H2O + H2O'`, `C5H14O -> 'H2O + C5H12'`.
   - **Search** (`_decompose`). By total size k = 1, 2, ... `k_max`. A frontier
     holds every multiset of k - 1 library pieces that fits inside the loss
     (ascending library indices, so no permutations; one radical piece at most,
     and only for a radical loss), in lexicographic order. Size k reads as one
     more library piece that completes the loss exactly (all named, tried
     first), else as a frontier multiset plus one valid unnamed remainder. The
     first hit is the answer, which is exactly the choice rule above. A
     frontier multiset whose rest needs more pieces than `k_max` leaves (the
     same bound, applied to the rest) is dropped before it grows; it never
     drops one that can read - the tests compare it with an unpruned
     enumeration of every multiset. `k_max` above `K_MAX_LIMIT` is refused.

   Why the remainder rules differ from `heuristic_filter.rule_senior`: that
   rule gates a candidate *neutral* and deliberately leaves Senior's second
   rule out, because it refuses carbenes and would need a search over
   hypervalent states. An unnamed loss piece is a different claim - one stable
   molecule left, on top of the named ones - and the second rule, with fixed
   valences, is what keeps NH (4 < 6) from passing as one.

7. **Rank** within one (peak, parent) (`FragmentCandidate.sort_key`):
   (a) closed-shell losses before radical losses; (b) fewer loss pieces;
   (c) fewer unnamed pieces; (d) smaller |ppm|; (e) the formula string. Every
   candidate is reported (`rank`, `n_candidates`): ties are ordered, never
   hidden. With `allow_radical=False` radical losses are no readings at all.

8. **The readings** (`read_fragments`). One `Reading` per (peak, parent,
   candidate), ordered by input peak, then parent order as given, then rank.
   `n_parents` is how many parents have at least one candidate for the peak. A
   peak no parent explains gets **one** row - every input peak is in the
   output. That row keeps its `spectrum`, `peak`, `mz`, `mz_cal` and
   `intensity` and carries `''` in every other text field, 0 in `loss_pieces`,
   `unnamed_pieces`, `rank`, `n_candidates` and `n_parents`, `False` in
   `radical_loss` and `is_precursor`, and NaN in `dbe` and `ppm`, so a filter on
   a flag or a piece count never picks it up as a reading. `best_readings`
   keeps the rank-1 rows plus the unexplained rows.

## 4. The ladder gate

`K_MAX_DEFAULT` is 3: every extra loss piece admits more losses, and so more
chance compositions, at every peak. An ammonium adduct of a polyol or an
oxygenated product walks a longer chain - [M+NH4]+ loses NH3 and then water
step by step - and its fourth step (NH3 + 3 H2O, DBE -3) needs four molecules
whatever the library.

`ladder_gate(readings, k=LADDER_K)` reads the readings of **one window** across
its energies (each spectrum read by `read_fragments` at `k_max` `LADDER_K`,
with its own `spectrum` key). A reading whose loss needs a **fourth** piece is
kept only when the **same window ion** shows, at **any spectrum** of the
readings, the step before it: a reading that is first among the non-precursor
readings of its (spectrum, peak, parent) with at most three pieces - what
`k_max` 3 would rank first - and whose loss is this loss minus **one named
closed-shell library molecule**, whichever pieces the decomposition prints.

- The printed decomposition is one of several of the same loss and need not
  hold the piece the ladder took: C9H22NO6+ -> C8H11O+ loses CH11NO5, printed
  `H3N + CH4O + H2O2 + H2O2`, while C8H13O2+ (loss CH9NO4) is read one H2O back.
- One molecule means one named closed-shell piece: two waters, an unnamed
  remainder or OH do not make a step, printed or not.
- `ladder_support` records the step's loss: the first printed piece, in the
  decomposition's order, that is one and finds one; else the first such step
  in the lexicographic order of the steps' count vectors on `ELEMENTS`.
- Losses are compared as count vectors, never as strings, so the result does
  not depend on how a formula was spelled or on the interpreter's hash seed.
- A four-piece reading never supports another. Readings above `k` pieces are
  dropped. The rest are kept as they are and **re-ranked**: `rank` and
  `n_candidates` within (spectrum, peak, parent), `n_parents` within
  (spectrum, peak); a peak whose every reading was dropped becomes one
  unexplained row at its place.
- The rows must be in `read_fragments`' order, and anything else raises: each
  (spectrum, peak) together and at one m/z, an unexplained row alone, the ranks
  of each (spectrum, peak, parent) 1, 2, ... in row order. Two spectra under
  one `spectrum` key (the default `''`), or a table re-sorted by ppm or
  intensity before the gate, would be read with the wrong steps and re-ranked
  across spectra.

Example: the hexose [M+NH4]+ C6H16NO6+ -> C6H7O3+ (loss H9NO3,
`H2O + H2O + H2O + H3N`) is read only when C6H9O4+ (loss H7NO2) or C6H10NO3+
(loss H6O3) is read in the same window. On peaky's three public fixtures no
peak needs a fourth piece at all: the gate and `k_max` 3 read exactly the same
readings (tested).

## 5. Constants

All in `mascope_tools.composition.ms2.readings`.

| constant | value | role |
|---|---|---|
| `READING_TOLERANCE_PPM` | `5.0` ppm | relative half-width of the reading window |
| `READING_TOLERANCE_MDA` | `0.3` mDa | absolute floor of the window (the low-mass offset) |
| `K_MAX_DEFAULT` | `3` | most molecules a loss may split into |
| `K_MAX_LIMIT` | `6` | `k_max` above this raises; 6 still reads NH3 + 5 H2O (H13NO5, DBE -5). The search holds only multisets that fit inside the loss and stops at the first size that reads: the tests' eight deep losses at 6 take 5 to 40 ms each, once (cached), and 20 to 60 times longer without the pruning |
| `LADDER_K` | `4` | the piece count `ladder_gate` admits on a ladder |
| `GRID_MAX_ROWS` | `5_000_000` | largest sub-formula grid built per parent (a C60H100O30N3S2 parent is 2.3M rows) |
| `FRAGMENT_DBE_MIN_EVEN` | `-0.5` | fragment gate: least DBE of an even-electron fragment ion |
| `FRAGMENT_DBE_MIN_ODD` | `0.0` | fragment gate: least DBE of an odd-electron fragment ion |
| `FRAGMENT_SENIOR_MARGIN` | `0.5` | fragment gate: how far a fragment ion's DBE may sit above Senior's cap |
| `SENIOR_CAP_BASE` | `1.0` | Senior's cap, the constant term |
| `SENIOR_CAP_PER_ATOM` | C, Si `1`; N, [15N] `0.5` | Senior's cap, per atom |
| `VALENCES` | C, Si 4; N, [15N], P 3; O, S 2; H, F, Cl, Br, I 1 | DBE (2 x DBE = 2 + sum((v - 2) n), `config.UNSATURATION_COEFFICIENTS` for the elements it lists) and Senior's second rule |
| `H_OR_HALOGEN` | H, F, Cl, Br, I | an unnamed loss piece must hold one |
| `ELEMENTS` | C, H, O, N, S, P, Si, F, Cl, Br, I, [15N] | the element axis, in peaky's order |
| `CLOSED_SHELL_LOSSES` | 43 formulas | section 6 |
| `RADICAL_LOSSES` | 16 formulas | section 6 |
| `DEFAULT_TOLERANCE` | `Tolerance(5.0, 0.3)` | the documented window |
| `_GRID_CACHE` | `32` | sub-formula grids cached, per (composition, charge); `read_fragments` holds its window's grids for the call, so a larger window builds each once a call, not at every peak |
| `_LOSS_CACHE` | `65536` | loss decompositions cached, per (loss, `k_max`) |

## 6. The loss library (priority order)

The library only **names** pieces and breaks ties between decompositions of
equal size; a loss that is not a library formula is still read as one unnamed
piece when it passes the remainder rules. Names are formulas, never trivial
names and never a structure: `C3H6O` is a composition, whatever the reader
thinks left.

**Closed-shell** (`CLOSED_SHELL_LOSSES`): H2O, H3N, H3[15N], CO, CO2, H2, CH4,
C2H4, CH2O, CH4O, CH2O2, C2H2O, C2H4O2, CHN, CHNO, CH4N2O, H2O2, HNO2,
H[15N]O2, HNO3, H[15N]O3, H2S, CH4S, O2S, O3S, HF, HCl, HBr, HI, N2, O2, C2H2,
C3H6, C4H8, C3H6O, C2H4O, C5H8; then three neutrals peaky's channels lose
often: C2H6O (ethyl esters and ethers), H2O4S (organosulfates), C2H6OSi
(dimethylsilanone, the ring-contraction loss of cyclic methylsiloxanes); and
last three real losses with no H or halogen, which the remainder rule would
refuse as unnamed pieces: CS2 and COS (dithiocarbamates, thiocarbamates) and
N2O.

**Radical** (`RADICAL_LOSSES`): H, CH3, HO, HO2, NO, [15N]O, NO2, [15N]O2, NO3,
[15N]O3, CHO, CH3O, C2H5, Cl, Br, I.

## 7. Outputs

`read_fragments` and `ladder_gate` return a tuple of `Reading`:

| field | type | meaning |
|---|---|---|
| `spectrum` | str | the caller's key for the spectrum |
| `peak` | int | 0-based position of the peak in the input |
| `mz` / `mz_cal` | float | the input m/z / the recalibrated m/z every candidate is judged on (on unexplained rows too) |
| `intensity` | float | from the input `intensity`, else NaN |
| `parent` / `parent_ion` | str | the window ion's label / its normalised ion formula (`'C7H8+.'` -> `'C7H8+'`, `'C10H20^N+'` -> `'C10H20[15N]+'`) |
| `formula` | str | the fragment ion formula, e.g. `'C6H9+'` |
| `parity` | str | `'EE'` / `'OE'` of the fragment |
| `dbe` | float | DBE of the fragment ion formula |
| `ppm` | float | the reading's mass error |
| `loss` | str | parent - fragment, Hill; `''` for the precursor |
| `loss_pieces` / `unnamed_pieces` | int | molecules in the decomposition / how many unnamed (0 or 1) |
| `loss_name` | str | the pieces joined by `' + '` |
| `radical_loss` | bool | a half-integer-DBE loss (the fragment's parity is not the parent's) |
| `is_precursor` | bool | the fragment is the parent itself |
| `rank` | int | 1 = best within (spectrum, peak, parent); 0 = an unexplained row |
| `n_candidates` | int | readings within (spectrum, peak, parent) |
| `n_parents` | int | parents with at least one reading of the peak; above 1 = composition-ambiguous |
| `ladder_support` | str | the step a four-piece reading stands on (`ladder_gate`), else `''` |
| `pieces` | tuple | the decomposition's pieces, as `loss_name` spells them |

`ms2.frame.readings_frame` builds a pandas DataFrame of them with fixed dtypes
(`READINGS_COLUMNS`, every field but `pieces`), whatever the content.
`fragment_candidates` returns the readings of one (peak, parent) as
`FragmentCandidate`s (with `mz_theo` and `pieces`); `decompose_loss` returns a
`LossDecomposition` (`pieces`, `unnamed`, `radical`, `k`, `name`) or None;
`mass_offset` returns a `MassOffset` (`offset_ppm`, `anchors`).

## 8. Properties and gotchas

- **Composition, not attribution.** `n_parents` above 1 means two window ions
  can both make the peak; nothing here prefers one. In peaky's monoterpene
  ammonium-adduct window, 137, 81, 95 and 109 are sub-formulas of both
  C10H20[15N]+ and the co-isolated C10H19O+.
- **Fewest pieces wins before named pieces.** A loss that is valid as ONE
  unnamed molecule is never split into two named ones: C11H15O2+ -> C2H3O+
  reads `C9H12O`, not `H2O + C9H10`.
- **Piece order is library order**, not mass order: `CH6 -> 'H2 + CH4'`.
- **Closed-shell and radical readings rarely collide at Orbitrap tolerance.**
  Two readings of opposite parity at one nominal mass differ by an odd number of
  14N atoms. The cheapest trades are [15N] for NH (10.8 mDa, only in a parent
  holding both 14N and 15N) and N for CH2 (12.6 mDa); closer ones need
  multi-atom trades ([15N]2 for NO, 2.2 mDa; CH4N3 for C3H6O, 1.3 mDa). Ranking
  rule (a) decides mostly in wide windows.
- **The parent is never gated; fragments are.** `fragment_ok` is the fragment
  test: do not pre-screen window ions with it.
- **Long dehydration chains need the ladder or an explicit `k_max`.** A
  hexose's [M+[15N]H4]+ (C6H16[15N]O6+) reads at `k_max` 3 up to -[15N]H3
  -2 H2O; -3 H2O is 4 molecules, -4 H2O is 5 and -5 H2O is 6.
- **The window is judged on `mz_cal`.** A 12 ppm axis error left uncorrected is
  outside the default window.
- **Singly charged only**: `parse_ion`, `format_ion` and `ion_mz` refuse any
  other charge.
- **Deterministic.** Same input, same readings, across runs, with cold caches
  and under any hash seed (tested in two processes).

## 9. Where it differs from peaky

- **Notation.** `[15N]` where peaky writes `^N`, and `utils.to_hill_order`'s
  order, which puts `[15N]` before `N` (`C6H17[15N]NO5+`; peaky writes
  `C6H17N^NO5+`). Peaky's spelling is read on input.
- **Masses.** Mascope's monoisotopic masses (pyteomics) differ from peaky's
  table in the last digits, most for I (1.1 uDa) and S (0.3 uDa). A reading's
  ppm moves by at most 0.01 ppm, so a candidate within 0.01 ppm of the window's
  edge, or two candidates whose |ppm| differ by less than that, can come out
  differently. Over 288,000 random candidate lists the two engines agreed but
  for such near-ties.
- **Shape.** `read_fragments` is peaky's `assign_fragments` returning `Reading`
  rows rather than a DataFrame (`readings_frame` builds one), with the window
  as one `Tolerance` and a `spectrum` key on each row; `ladder_gate` takes those
  rows. Peaky's `read_window` (one window across energies, with harmonic
  ghosts) has no counterpart here: the caller reads each spectrum, and the
  ghosts are MAS-388's.

## References

- Kind, T.; Fiehn, O. *Seven Golden Rules for heuristic filtering of molecular
  formulas obtained by accurate mass spectrometry.* **BMC Bioinformatics**
  2007, 8:105.
  [link](https://bmcbioinformatics.biomedcentral.com/articles/10.1186/1471-2105-8-105)
- Peaky, `docs/MS2.md` sections 1 to 9 and 11.2;
  [github.com/ultra-trace-systems/peaky](https://github.com/ultra-trace-systems/peaky).
