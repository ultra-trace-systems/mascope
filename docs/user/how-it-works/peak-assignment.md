# Peak Assignment & Identification Confidence

Where [target matching](matching.md) answers *"do these known compounds appear in the
sample?"*, **peak assignment** answers the inverse, peak-first question: *"for every
observed peak, what is the most likely chemical composition, and how confident are we?"*
Each peak gets exactly one assignment per run, together with a **fit score** and a
**confidence tier**.

!!! note "Peak assignment is off by default"

    Targeted matching keeps working exactly as before either way — peak assignment is
    an addition, not a replacement. Target collections, ion tables, the batch overview
    and the Match tab are all unaffected. With it on, a sample is assigned against the
    known target library as it is processed, the assignment views appear, and the
    composition search reports assignment confidence.

    A deployment switches it on by setting `peak_assignment = true` under `[meta]` in
    the environment's config toml and restarting the stack — that is the whole
    procedure, and setting it back to `false` undoes it. With it off nothing is
    assigned when a sample is processed, the composition search reports the familiar
    match score, the Sample tab keeps its peak ledger, and the API refuses to launch
    assignment runs (the write routes return 403; reads stay open, so results from a
    period when it was on remain visible).

The design rests on a foundational result of the field: **accurate mass alone — even at
sub-ppm — cannot uniquely determine an elemental composition**, and isotope-pattern
information is worth more than another order of magnitude of mass accuracy
([Kind & Fiehn 2006][kf06]). Identification is therefore treated as *accumulating
independent evidence* and *arbitrating between candidates that all fit the mass*.

```
   measurement              evidence layers                    decision
 ┌──────────────┐   ┌────────────────────────────┐   ┌────────────────────────┐
 │  FIT SCORE   │ → │ chemistry (plausibility) ·  │ → │  assignment + a         │
 │ how well the │   │ (spectral context, later)   │   │  confidence + a tier    │
 │ data fit one │   └────────────────────────────┘   └────────────────────────┘
 │  candidate   │
 └──────────────┘
```

--8<-- "_help/assignment-evidence.md"

## The two stages

Every peak is assigned in a two-stage engine:

- **Stage A — database-first.** The peak is matched against the sample's known target
  library (the same target isotopologues used by [target matching](matching.md)) and
  against any reference lists your deployment has loaded; the best-fitting known
  composition wins the peak. A reference list is matched only within its own element,
  carbon and mass window, under a ceiling the sample's chemistry context sets; only in
  the polarity it is detected in; and it contributes radicals only if it allows them.
  The target library is read through the ionization mechanisms of the sample's mode; a
  reference list also through the channels the run opens for itself (*A list through
  the channels a run opens*, below).
- **Stage B — untargeted.** Peaks that Stage A left unexplained are run through a
  bounded composition search that enumerates every elemental formula whose ion lands
  within the mass tolerance — the classic mass-decomposition problem
  ([Böcker & Lipták 2007][bl07]) — and scores each candidate the same way.

**A list compound meets the search.** When a run searches formulas, every peak Stage A
assigned is put to Stage B too, with the list's compound as one of its candidates. The
compound's place on a list counts for it: a closed-shell formula from the search takes
the peak only where its evidence (fit × plausibility, see
[Arbitration](#arbitration-competing-the-candidates)) is more than twice the compound's,
and ahead of it by more than a tie. It must also explain the compound's own isotope
lines, the ones the spectrum shows where the compound predicts them: the fit charges a
reading for a line it predicts and the spectrum lacks, but never charges a rival for a
line the spectrum holds and the rival leaves unexplained. A siloxane's silicon lines, or
the bromine line of a bromide adduct, keep the list's compound on its peak against a
formula without them. A compound of your own target library keeps its peak in any case.
Where a rival does take the peak, the row shows the search's formula, with the list's
compound first among its alternatives, so promoting it by hand restores the list's
reading. The compound's isotope lines leave the ledger with it, and the search's own
pattern can hold them. Where the compound keeps its peak, the search's rivals still count
against it (*rivals left standing*, below).

Peaks that neither stage explains are recorded as *unassigned*, so a run is a complete,
queryable ledger: one row per observed peak.

**Single owner, within tolerance.** Each peak has exactly one owner per run, and a peak is
only owned by an isotopologue whose measured m/z is *within tolerance* of the prediction.
A predicted isotopologue that has no real peak is left unmatched rather than being pinned to
a nearby, out-of-tolerance peak — that peak is released to the untargeted stage (or left
unassigned) so it can get its own correct assignment instead of being mislabelled as a
poorly-fitting isotopologue of something else.

## The chemistry a run searches under

A run searches under two presets:

- **A chemistry profile:** how the sample was ionized. It decides which ions the source
  makes of itself, and which elements the sample's compounds can be built from.
- **A chemistry context:** what was sampled.

**What they set.** A run first sets aside the ions a CIMS or charge-transfer source makes
of itself - its reagent's own clusters, the ions of the air it ionizes, an Orbitrap's
calibrant beam - as *reagent* peaks, so neither stage reads them as compounds (*The ions a
source makes of itself*, below). An electrospray profile has neither a reagent nor a
discharge, and sets none aside. Then:

- the profile gives the untargeted stage its element grid, its m/z window on each
  instrument class, and the extra channels a source of its kind produces;
- the context narrows that grid, never widening it;
- the context also rejects formulas whose hydrogen, oxygen, nitrogen or
  ring-and-double-bond count per carbon no such sample holds, in the manner of the
  element-ratio rules of [Kind & Fiehn 2007][kf07];
- the context caps the window a reference list is matched in.

| Profile | Polarity | Recognised by | Context it takes |
|---|---|---|---|
| Bromide CIMS | negative | `[M+Br]-` | Ambient air |
| Uronium (urea) CIMS | positive | `[M+CH4N2O+H]+` | Uronium |
| Nitrate CIMS | negative | `[M+NO3]-` | Ambient air |
| 15N-nitrate CIMS | negative | `[M+^NO3]-` | Ambient air |
| Iodide CIMS | negative | `[M+I]-` | Ambient air |
| Charge transfer (EASY-IC), positive | positive | electron transfer, `[M]+.`, and no reagent, on an Orbitrap | Ambient air |
| Charge transfer (EASY-IC), negative | negative | electron transfer, `[M]-.`, and no reagent, on an Orbitrap | Ambient air |
| Positive ESI / APCI | positive | no diagnostic mechanism | none |
| Negative ESI / APCI | negative | no diagnostic mechanism | none |

The contexts are ambient air, chamber, indoor air, object headspace, combustion, water,
food and beverage, and uronium. Each describes itself where it is chosen.

**Auto.** Both presets default to **Auto**. The profile is read off the sample's
ionization mechanisms: a mode carrying the bromide mechanism is a bromide source, whatever
the mode is called. On an Orbitrap, a mode with no reagent whose mechanisms include
electron transfer, `[M]+.` or `[M]-.`, is a charge-transfer source, the way that
instrument's EASY-IC source is declared: it searches a hydrocarbon-sized grid under the
ambient prior, its reagent pass claims the fluoranthene beam, and the channels such a
source also runs, hydride abstraction (`[M-H]+`), proton transfer and methyl loss
(`[M-CH3]+`, what charge transfer leaves of alpha-pinene at m/z 121 and of a cyclic
siloxane at its base peak) in positive mode and deprotonation in negative, are opened as
secondary ones where the spectrum shows the source runs them (or where the acquisition
could not have shown it), and capped at candidate without corroboration. A channel the
mode declares itself, proton transfer beside electron transfer, is the mode's own and is
not capped. An ion such a channel reads
is often one electron transfer reads too, as a different molecule: the tropylium ion is
protonated C7H6 or toluene
less a hydride, and the search's own preference for the heavier mechanism would take the
proton. So an opportunistic reading stands only where the sample commits its molecule
through one of the mode's own channels; otherwise the mode's reading is the row's, and
between two opportunistic readings the one whose molecule the sample shows wins. Toluene
is seen through electron transfer, so tropylium reads as toluene. The reading set aside stays
on the row, marked as not borne out, and does not count against the one that was. Where the
sample shows both molecules, the one it shows more strongly wins, whichever mechanism
carries more mass: the molecule whose brightest peak through the mode's own channels is
the brighter, among the rows committed at *candidate* or better. The tier is only that
bar, since the passes after this rule can still lower a row, and a peak's height they
cannot change. On a certified mixture holding toluene and not C7H6, electron transfer
sees both, toluene 27 to 31 times as strongly, and tropylium reads as toluene less a hydride.
The weaker reading stays on the row as well. Where its molecule's peak is a tenth as
bright or less, it is marked as outweighed and the row counts the question as settled;
closer than that, it is still a rival the sample shows, and the row is held at
*candidate* with it named (*ambiguous adduct*, below): a reading the sample shows seven
times as strongly is the better one, not a certain one. A reading through one of the
mode's own channels is never outweighed this way and keeps its formula; a rival the
sample shows is weighed against it by the same margin, read from the other side: the
rival has to be shown ten times as strongly to hold it at *candidate* (*ambiguous
nitrogen* and *ambiguous adduct*, below). The rule is
read after the run's mass gate, so a partner is a reading that gate left committed and a
reading it lifts stays under that gate's ceiling, and a batch search reads it over its own
rows. On any other instrument electron transfer names an
ambient-ion mode as readily, and such a mode keeps the ESI profile of its polarity; so does
a mode with only protonation or deprotonation and no reagent. A secondary channel is
searched only where the deployment holds its mechanism, so Mascope ships the mechanism of
every channel a profile can open, hydride abstraction's `[M-H]+` and methyl loss's
`[M-CH3]+` among them (*The chemistry every deployment holds*, below). A reading through
methyl loss stands, as one through hydride abstraction does, only where the sample commits
the molecule it proposes through one of the mode's own channels: pinene less a methyl is
also protonated C9H12. The context is the one the profile is normally used with.

**Formate.** Every negative reagent profile (nitrate, 15N-nitrate, bromide, iodide) can
open a formate adduct channel, `[M+HCOO]-`, the way it opens carbonate: where the spectrum
shows formate, its dimer with formic acid, or its cluster with the reagent's acid. On a
nitrate source the channel also stays on where the acquisition starts above every
formate carrier the source makes, since that silence is the window's, not the
chemistry's; a halide profile claims only what it can show. A peak that reads both as a
deprotonated acid and as the formate adduct of a molecule 46 Da lighter is one ion read
two ways, and every acid reads that way. The formate reading is the row's only where the
lighter molecule is itself committed through one of the mode's own channels, which also
corroborates it; otherwise the acid reading stands, and the formate reading stays on the
row as one the sample did not bear out. The channel is searched through the `[M+HCOO]-`
mechanism, which Mascope ships with the rest.

**The chemistry every deployment holds.** A run searches a channel only where the
deployment holds its ionization mechanism, since an assignment names the mechanism it was
read through. So Mascope ships every mechanism its chemistry needs, and a server creates
the ones it lacks when it starts: those the ionization modes Mascope ships declare (the
nitrate, 15N-nitrate, bromide, iodide, urea, ammonium and 15N-ammonium reagents,
protonation, deprotonation, and electron transfer in either polarity), and every
secondary channel a profile can open (hydride abstraction, methyl loss, proton transfer,
deprotonation, carbonate, formate, the dibromide and diiodide clusters, ammonium, sodium
and potassium). A fresh server searches under the same chemistry as any other from its
first run, so a run on one set of samples reads the same wherever it is repeated. Each
mechanism is created the way one added under Ionization mechanisms is, with an ion for
every compound already in the library, so the start that first creates them takes longer
on a large library, once. A mechanism the server already holds, in either spelling, keeps
its own row. These mechanisms cannot be deleted, since the next start would only create
them again. The run's snapshot lists a channel as unavailable where its mechanism is
missing all the same, which on a current server means the start could not create it, and
its log says why.

**A list through the channels a run opens.** A reference list names a compound, not the
channel it is seen through, so Stage A reads a list through the channels the run opened
for itself as well as through the mode's own mechanisms. On a charge-transfer source,
whose mode declares electron transfer alone, a cyclic siloxane's methyl-loss ion, its
base peak there, and its protonated molecule beside it are named from the list. A
reading through an opened channel is held to the rules the search's readings through
that channel are: it is capped at *candidate* unless its own isotope lines or the same
compound through one of the mode's own channels corroborate it, and through a channel
that needs the compound shown elsewhere (hydride abstraction, proton transfer, methyl
loss, deprotonation on the charge-transfer source, formate on a reagent source), it
stands only where the sample commits the compound through one of the mode's own
channels. The list's compound stays the row's either way: where another reading of the
ion would have taken a search's row, the list keeps its compound on the row, and the
other reading stays on it as a rival that holds it at *candidate* where nothing settles
which. Your own target library is read through the mode's mechanisms alone, since its
compounds were named for the modes its collection is attached to; to read it through
another channel, declare that channel in the mode. A secondary channel the mode
declares, such as carbonate, is part of the mode's mechanisms, and a list read through
it keeps its tier as before.

**No profile and No context.** *No profile* switches the layer off: the run searches
the engine's original wide grid at a fixed 10 ppm window. *No context* applies no
matrix prior.

**In the launchers.** *Assign peaks* and the batch's *Search untargeted* both offer the
two presets. Each says what *Auto* resolves to: for the sample, or for each group of the
batch's samples, since a batch can hold more than one ionization mode. Where the samples
share them, the formula range and m/z precision fields show the grid and the window the
run would search at, and *Find more* - the composition search in the Sample tab - starts
from the same two for the sample in view.

A named profile applies to every sample it reaches. The launcher warns when that profile
belongs to the other polarity from the samples. The choice is remembered with the other
launch settings, and *Reset to defaults* puts both presets back on *Auto*.

**In the run selector.** Each in-app run names the profile it searched under beside its
engine. Hovering the name shows:

- whether the profile was read off the mechanisms or named for the run;
- the context;
- the element grid and the m/z window;
- the extra channels searched;
- any channel the spectrum showed but the deployment has no mechanism for.

### The ions a source makes of itself

Before either stage, a run sets aside the peaks its source made rather than its sample,
as *reagent* peaks: a row that names the ion and no compound, so it votes on no batch
consensus and weighs on no tier. Each ion the pass knows belongs to a family and carries
the works that name it, and the row records both, so a claim can be checked against a
paper rather than against Mascope's word. A work is cited for an ion only where it names
that ion.

- **The reagent's own ladder.** The ion a CIMS source is dosed with and what it makes of
  itself: nitrate, its clusters with nitric acid [Jokinen et al. 2012][jok12] and its
  hydrates [Skalný et al. 2004][ska04], [2007][ska07], with every nitrogen labelled on a
  15N-nitrate source [Zhang et al. 2026][zha26]; bromide, its hydrates and its dimer
  [Sanchez et al. 2016][san16], [Rissanen et al. 2019][ris19], [Wang et al. 2021][wa21];
  iodide and its hydrate [Dörich et al. 2021][dor21], and its dimer and trimer [Gómez
  Martín et al. 2022][gom22]; protonated urea and its dimer [Shcherbinin et al.
  2025][shc25].
- **The air's ions.** What a discharge makes of nitrogen, oxygen, water and carbon dioxide
  before any reagent or analyte is involved. On a charge-transfer source they are the
  source; on a reagent source, its background.
    - Positive: N2+, N3+ and N4+ [Good et al. 1970a][good70], [Kolakowski et al.
      2004][kol04], the first also [Shcherbinin et al. 2024][shc24]; O2+ [Good et al.
      1970b][good70b], [Shcherbinin et al. 2024][shc24], [Dusanter et al. 2025][dus25];
      NO+ [Shahin 1966][sha66], [Sabo & Matejčík 2012][sab12], [2013][sab13], [Dusanter
      et al. 2025][dus25]; NO2+ [Shahin 1966][sha66], [Shcherbinin et al. 2024][shc24];
      the hydrates of O2+, NO+ and NO2+ [Shahin 1966][sha66]; and the protonated water
      ladder from H3O+ to (H2O)5H+ [Good et al. 1970a][good70], [Shahin 1966][sha66],
      [Hansel et al. 1995][han95], [Pfeifer et al. 2020][pfe20].
    - Negative: OH- and its water clusters [Fujishima et al. 2023][fuj23], [Sekimoto &
      Takayama 2011][sek11], [Takayama 2026][tak26], which one corona source reads and
      another argues become bicarbonate before they reach the analyser [Asakawa &
      Hiraoka 2023][asa23], so they are claimed where a spectrum shows them; O2-
      [Skalný et al. 2004][ska04], [2007][ska07], [Sekimoto et al. 2012][sek12] and its
      hydrate [Sekimoto & Takayama 2011][sek11]; O3- and its hydrate [Shahin
      1969][sha69]; O2- carrying carbon dioxide [Matas et al. 2023][mat23]; carbonate and
      its hydrates [Skalný et al. 2004][ska04], [2007][ska07], [Shahin 1969][sha69];
      bicarbonate [Nagato et al. 2006][nag06], [Sekimoto et al. 2012][sek12]; nitrite and
      its hydrates [Skalný et al. 2007][ska07], [Ewing & Waltman 2009][ewi09], [Matas et
      al. 2023][mat23]; nitrate, its hydrates and its cluster with nitric acid [Skalný et
      al. 2004][ska04], [Nagato et al. 2006][nag06], [Ewing & Waltman 2009][ewi09]; and
      bicarbonate's cluster with nitric acid [Nagato et al. 2006][nag06]. On an iodide
      source nitrite and nitrate are left to the stages (below).
    - On a 15N-nitrate source the plain nitrate ions are left to the reagent's own
      envelope, whose 14N remainder they are first: the envelope claims them where their
      height fits the label's purity.
- **The calibrant beam.** An Orbitrap's EASY-IC source is a fluoranthene beam, its radical
  cation at m/z 202.0777 and its radical anion at 202.0788 [Thermo Fisher Scientific
  2018][easyic], [Leborgne et al. 2023][leb23], [Ashbacher et al. 2026][ash26], [Martens
  et al. 2016][mar16]. Beside the cation it carries the ion one hydrogen lighter, the
  protonated ion and the ions two and three hydrogens heavier where the source is humid
  [Shcherbinin et al. 2024][shc24], [West et al. 2018][wes18], and the cation's own
  fragments, less H2 and less one and two acetylenes, which its electron-ionization
  spectrum shows [NIST WebBook][nist]. It also carries two polycyclic aromatic ions one
  CH2 apart, C14H10+. and C15H12+., claimed on what the test spectra show (below). C14H10
  is anthracene's and phenanthrene's ion, so on this source a PAH of these compositions
  is not read as an analyte.

Seven of these ions are named by no work found. They are claimed on what Mascope's test
spectra show instead, and each row says so: bromide's trimer Br3-, in all 15 files of the
three bromide sets at 1.5 to 35% of the base peak; its oxide BrO-, in 11 of the 15 at up
to 1.8%; bromate, BrO3-, and bromide's cluster with HBr, in every file of one set at 0.6%
and 0.06%; protonated urea's trimer, in every file of one of the two uronium sets at
0.08%; and the beam's C14H10+. and C15H12+., in all 19 files of the three
certified-cylinder sets, zero air among them, at up to 4.3% and 6%.

**What is left to the stages.** An ion a work reads as an analyte's is not claimed,
however much it looks like the source's, and neither is an ion no work names that the
test spectra do not show:

- ammonia's ions, NH4+ and its hydrates: how a protonated-water source measures ammonia
  [Pfeifer et al. 2020][pfe20]; ammonia with urea is the urea source's reading of it
  [Shcherbinin et al. 2025][shc25];
- bromide with HO2, the HO2 radical's own reading [Sanchez et al. 2016][san16];
- on an iodide source, nitrate and nitrite, their hydrates and nitric acid on nitrate
  and on bicarbonate: bare nitrate at m/z 62 is how an iodide source shows nitric acid,
  and N2O5, the nitrate radical and the halogen nitrates beside it [Dörich et al.
  2021][dor21], and nitrite goes with it as its pair. On a bromide source no work found
  reads either as an analyte's, so there they are the air's;
- formate, acetate and their clusters with their acids: formic and acetic acid
  deprotonated, which is how an acetate source measures them [Veres et al. 2008][ver08],
  [Bertram et al. 2011][ber11]. On a chamber's nitrate source they are the chamber's acids,
  read through the deprotonation the mode declares;
- trifluoroacetate, and CF3- and CF3O-, which ride with it: trifluoroacetic acid is an
  analyte a nitrate or iodide source measures, and a claim before the stages cannot ask
  whether it is in the sample. As its fragments the two would be claimed after the
  stages, against the acid committed, but no work found gives either as its fragment
  with a height a claim could be held to, so neither is claimed;
- the cyclic siloxanes. They are a background of most instruments, from laboratory air
  and the inlet [Schlosser & Volkmer-Engert 2003][sch03], and the analytes of an indoor-
  or urban-air study, and a claim before the stages cannot tell the two apart. They are
  named from the shipped cyclic-siloxane list, which reads them as background: the row
  shows the list's *background* tag beside the name, the tier does not weigh it, and
  whether they are background in a dataset is for the batch and its blanks to say. A list
  is read through the mechanisms the mode declares and the channels the run opens, so on
  a charge-transfer source a siloxane's methyl-loss ion, its brightest there, and its
  protonated molecule are named from the list, each held to the rules of the channel it
  is read through (*A list through the channels a run opens*, above);
- the rest of what the ladders' grammar would enumerate: the higher bromide and iodide
  clusters and their hydrates, the iodine oxide clusters, a bromide precursor's anion, the
  urea multimers above the trimer and those carrying ammonium, the fluoranthene dimer,
  and C13H8+., the PAH rung below the beam's two, which the test spectra show in one
  file of nineteen.

**Fragments of an analyte.** A charge-transfer or proton-transfer source breaks some of
what it ionizes, and a fragment has an ordinary composition, so the formula search reads it
as a molecule of its own: a partner, a second channel and a rival for everything else at
its mass. The monoterpenes are the case: alpha-pinene's C7H9+, C6H9+, C6H8+., C6H7+,
C6H5+ and C5H7+ ([NIST WebBook][nist]; [Wang et al. 2003][wan03]; [Schoon et al.
2003][scn03]; [Materić et al. 2017][mat17]; [Tani et al. 2003][tan03]; [Kari et al.
2018][kar18]; [Ishihara et al. 2026][ish26]). A fragment is claimed after the stages,
since what licenses it is a commit, where three things hold:

- the monoterpene is committed through one of the mode's own channels, at any tier, since
  the claim asks of it its height, not its fit;
- the fragment is no taller, against the parent's ion, than three times what the
  literature gives it. For the ions electron ionization makes that is alpha-pinene's
  electron-ionization spectrum, the hardest ionization a monoterpene meets, so a softer
  source breaks it less and there is no lower bound; for C6H9+, which proton transfer
  makes beside the protonated molecule, it is the ion's share of a proton-transfer
  spectrum at the harder of two drift fields [Kari et al. 2018][kar18];
- no reading of the fragment's ion names a molecule the sample shows on a peak of its own,
  at *candidate* or better through one of the mode's own channels. That is what tells a
  monoterpene's C6H7+ from protonated benzene: where the sample shows benzene's radical
  cation, the ion stays benzene's.

The ladder leaves out C7H8+., C7H7+ and C8H9+, though the monoterpene's spectrum has them
strong: they are toluene's radical cation and toluene and xylene less a hydride, and a
component all of whose ions sat on the ladder would have nothing left to be shown on. The
row names the fragment's ion, the parent it was read against and what the stages had read
the peak as; a compound of your own target library keeps its peak.

**How the rows read.** A reagent row names no compound, so the sample's assignment ledger
shows its ion in the formula column instead, written with its charge (`Br-`, `C16H10+`),
and the inspector heads the card with it; the *reagent* chip beside it says the source
made it. An ion's isotope lines are claimed with it and are its family, as an analyte's
isotopologues are the analyte's: they fold under the ion's monoisotopic row, counted in
its **+N** marker, and the inspector lists them in the ion's isotopologue table against
the abundances its isotope pattern predicts. A fragment's lines fold under the fragment
the same way. The *reagent* count above the ledger still counts every peak the source
accounts for, the lines included. If you assign a compound to an ion's peak by hand, its
lines are unassigned with it, as an analyte's isotopologues are when their compound is
replaced. A run made before Mascope linked the lines to their ion lists them as rows of
their own and writes the ion without its charge; running the assignment again links them.

## The fit score — a pure measurement

The **fit score** measures exactly one thing: *how well does the observed data fit the
predicted spectrum of this candidate?* It is on `[0, 1]` (1.0 = perfect fit), computed
per ion from its isotopologue peaks, and is deliberately:

- **competitor-blind** — it knows nothing about alternative formulas;
- **not a probability of correctness** — because mass alone cannot prove a composition
  ([Kind & Fiehn 2006][kf06]);
- **deterministic and reproducible** — the same spectrum always yields the same score.

Rather than scoring the monoisotopic mass alone, the fit score scores the **whole isotope
pattern**: each predicted isotopologue contributes a mass likelihood (a Gaussian in ppm,
its width set by the instrument's *measured* mass accuracy — and widened for weak peaks,
whose centroids are legitimately less precise — so the score is fair on both
high-resolution Orbitrap and lower-resolution TOF instruments) and an intensity
likelihood (its tolerance set by the peak's own signal-to-noise). A predicted peak that is
**absent but should have been detectable** counts against the assignment; one that is
below the noise is simply excluded. The per-peak likelihoods are combined as an
abundance-weighted geometric mean. This construction follows the probabilistic
isotope-pattern matching of **SIRIUS** ([Böcker et al. 2009][bo09]; [Dührkop et al.
2019][du19]), adapted to centroided industrial spectra by adding detection-limit
awareness. The full mathematical model is in the developer reference,
`libraries/tools/docs/fit_score.md`.

The Gaussian's width and centre are the sample's own where it can measure them, from
the lines of your target library that it matched. Where fewer than eight matched, the
width is the instrument class's, and the centre is the median mass error of the reagent
ions the run claimed, if it claimed at least three and their offset exceeds that width;
otherwise the sample is scored on its nominal masses. The reagent ions stand in only
there: they sit at the low end of the mass range and need not agree with the offset
your library measures.

**A consequence users see:** a lone mass-only match (one peak, no isotopic corroboration)
scores *low* by design, while a fully corroborated isotope envelope scores near 1.0. This
is intentional — mass alone is weak evidence.

**M0, M+1, M+2.** Throughout Mascope, **M0** is the monoisotopic peak — the ion built from
each element's most abundant isotope — and the offsets count from it, as in an isotope
table. It is the row that carries a compound's assignment, the peak its isotopologues
fold under in the ledgers, and the peak a verdict is recorded on. For most ions it is
also the tallest peak of the cluster; for a bromine- or chlorine-rich ion it is the
lightest, and the tallest peak is its M+2. For an ion made with an isotopically labelled
reagent, such as 15N-nitrate, the labelled atom is at its label: the M0 is the labelled
peak, and the reagent's unlabelled remainder, a small peak one mass unit below it, reads
M-1. Abundances in the inspector are fractions of the family's most abundant
isotopologue, so nothing reads above 100 %.

## Chemical plausibility — the Seven Golden Rules

Most mass-degenerate formulas are chemically impossible or implausible. Mascope scores
each candidate's **chemical plausibility** on `[0, 1]` from the **Seven Golden Rules**
([Kind & Fiehn 2007][kf07]), combining three referenced factors:

1. **Valence feasibility (Lewis/Senior).** The ring-and-double-bond equivalents must be
   non-negative and the atoms must be able to form a connected molecule; an over-saturated
   formula (more hydrogens than any structure can carry) is driven to zero.
2. **Element-ratio plausibility (Rules 4–5).** The ratios of H, N, O, P, S, halogens to
   carbon are graded against the *common / extended / extreme* ranges the paper derived
   from tens of thousands of real formulas (its Table 2).
3. **Heteroatom co-occurrence (Rule 6).** Simultaneous high counts of N, O, P and S are
   improbable; graded against the paper's multi-element restrictions (its Table 3).

Plausibility is **conservative and fail-open**: it *grades* candidates rather than
hard-rejecting them, and unusual-but-real chemistry (radicals, exotic elements) is never
penalised — only the provably impossible is.

## Arbitration — competing the candidates

For a single peak, the surviving candidates are competed by their combined **evidence =
fit × plausibility**: a candidate must both fit the data *and* be chemically sensible.
Mascope reports, per candidate, a **confidence** (its evidence share among the peak's
candidates) and is **honest about ties** — when two candidates are genuinely
indistinguishable it says so rather than inventing a winner. On the reference dataset,
folding chemistry into the ranking this way resolves markedly more of the hard,
mass-degenerate cases than the fit score alone, because it demotes the spectrally-plausible
but chemically-implausible decoys the fit score is (by design) blind to.

This is the "which of the well-fitting compositions is most likely" problem — the core of
identification. Reliability at scale is estimated with **target–decoy** methods, the
established approach for large-scale MS annotation ([Scheubert et al. 2017][sch17]).

## Calibrated confidence (probability of being correct)

**P(correct)** is the assignment's evidence calibrated into an actual probability of
being correct, on a curve fit per instrument class from assignments whose truth is known
&mdash; so of everything reported at 0.9, about 90% would really be right.

**The app does not show it yet.** The one curve that ships is provisional, fit on a
preliminary reference set nobody has verified, and a probability read off it would be
the one number on the page a reader takes at its word. So the peak browser has no
P(correct) column and the peak inspector no P(correct) row while the curve is
provisional. The engine still computes it, and the API and the Python SDK still serve it
on every row that has one (`p_correct`, with `p_correct_provisional` saying the curve
behind it is provisional), so a script can read it with that caveat in hand. It returns
to the app when a curated curve replaces the provisional one.

The evidence score ranks assignments, but a raw evidence of 0.85 is not "85% likely
correct" — the calibration that closes that gap is **Platt scaling** ([Platt
1999][platt]), a logistic curve `P = sigmoid(a·evidence + b)` fit on assignments
whose truth is known.

In practice:

- **It is per instrument.** The same raw evidence means different things on an Orbitrap
  (sub-ppm, high resolution) than on a lower-resolution TOF, so each instrument class has
  its own curve. The label data comes from **confident identifications** — most strongly,
  compounds confirmed by a **reference standard** (a Level‑1 identification,
  [Schymanski et al. 2014][sch14]) — versus near-mass decoys. This is why calibration is
  tied to your **reference dataset**, and why you can, in principle, **calibrate your own
  instrument** by running known standards.
- **An uncalibrated assignment carries no probability**, only its evidence: nothing is
  made up for an instrument without a curve, or for a formula the formula search or a
  person chose, which the curve never scored. Today one **provisional** Orbitrap curve
  ships (fit on a preliminary reference set); it will be replaced by a curated fit, and
  TOF is uncalibrated until a TOF reference set exists.
- **The adduct lift is measured, not assumed.** A real compound rarely appears as a single
  ion — it also shows up through other adducts (e.g. `[M+H]⁺` alongside `[M+NH₄]⁺`, or
  `[M−H]⁻` alongside `[M+Br]⁻`), and each adduct's corroborating worth is *measured*: a
  chemically distinctive adduct like bromide corroborates strongly, while a generic one
  (ammonium, protonation) barely moves it. The lift is bounded, and — like the calibration
  curve — the per-adduct weights are specific to your instrument's reagent chemistry.

## Confidence tiers

--8<-- "_help/assignment-tiers.md"

The tiers are the product-facing summary of the confidence layer, and the quantity
underneath them is the **evidence** — fit × plausibility, the same product the candidates
were competed on, rather than the fit alone. A tier therefore reflects both how well the
measured isotope pattern matches *and* how chemically plausible the formula is: a
composition that fits the mass beautifully but describes an unlikely molecule no longer
earns the top tier on the strength of the match. The band a row lands in is read off the
same quantity that won it the peak in the first place, so the tier and the arbitration
cannot disagree.

The chip carries no percentage because the evidence is not a probability: *P(correct)* is
the calibrated one, which the app does not show yet (above). The fit score is unchanged — still recorded, still shown in the
inspector as the pure measurement, beside the evidence — so the two stay visible apart. A
*batch peak*'s consensus tier is a weighted vote over what the batch's samples each
concluded about the peak, not a threshold on any single number.

The long-term goal is to report a community-standard **identification level** ([Schymanski
et al. 2014][sch14]; MSI reporting standards, [Sumner et al. 2007][sum07]) alongside the
confidence, since that is how the field communicates identification certainty.

> **Note.** The fit score is a pure measurement and stays one. Chemistry, spectral context
> and calibration are *layers on top* of it and are never folded back into the score —
> tiering reads the fit and the plausibility together, but the fit score itself is the
> measurement alone, unchanged, which keeps it reproducible while the confidence layers
> evolve. The current tier thresholds are provisional and will be recalibrated per
> instrument; tying a tier to a calibrated probability of being correct is still where this
> is heading, and still waits on calibration coverage across instruments. The rules that
> read a tier are still being built too, which is why the tier column and the inspector's
> tier row carry a *provisional* mark.

### Why a row holds its tier

The evidence sets the highest tier a row can reach. A second pass then asks each committed
row the questions the evidence cannot answer, and it can only lower a tier, never raise
one; the one thing it does besides is to read a peak as another compound's isotope line,
which replaces the row's reading rather than raising it (below). The peak inspector lists
its answers under **Why this tier**: every committed row carries at least one, naming
either what capped it at *candidate* or what it kept its tier on. The sentence under each
is the run's own, about that row.

A row whose evidence is under the top band says so first: **evidence band** gives its
evidence, the fit and plausibility it is the product of, and the band it falls short of -
*evidence 8% (fit 8% x plausibility 100%) is under the candidate band of 45%*. The
band is where the row stands before any rule, so the reasons after it say what else holds
it down, or what it has going for it: a row can be seen through a second channel and
still stand below assignability on a poor fit.

What caps a row at candidate:

- **radical neutral** — the committed neutral breaks the even-valence rule, the first of
  the SENIOR rules among [Kind & Fiehn 2007][kf07]'s checks: its ring-and-double-bond count
  is a half-integer, so it has an unpaired electron and names a radical rather than a
  molecule. Radicals are real chemistry, but a formula search chooses a radical reading of
  an ion rather than measuring it against a closed-shell one. A curated identity is exempt,
  because it was matched to a compound somebody authored rather than chosen by the search.
- **no oxygen to cluster on** — the peak was read as nitrate clustered with a neutral that
  has no oxygen. Nitrate holds on to a molecule by hydrogen bonds from its oxygen-bearing
  groups, and quantum-chemical modelling of what a nitrate source detects puts the bar at
  two such hydrogen-bond donors ([Hyttinen et al. 2015][hy15]); a neutral with no oxygen
  offers none of them. The row keeps its formula, since its mass and isotope pattern
  still fit. The rule covers every form of the nitrate channel: the plain and the
  15N-labelled ion, and their clusters with nitric acid. Carbonate clusters are not
  judged this way. A second ionization channel does not lift the cap. A compound of your
  own target library is exempt; a formula from a loaded reference list is not, since a
  list names a compound rather than the channel it is seen through.
- **polyhalide: air or source** — the peak was read as a halide attached to a neutral
  made only of halogens: IBr read through bromide is the polyhalide anion IBr2-. A
  halide source measures the air's halogen molecules exactly this way, and it also makes
  them from its own, from impurities in its halogen supply and from species its walls
  give back ([Wang et al. 2021][wa21]), so the mass and the isotope pattern cannot tell
  the air from the source. The row keeps its formula; only how the peak moves over time
  can settle where it came from. A second ionization channel does not lift the cap, and
  a compound of your own target library is exempt.
- **rivals left standing** — the peak's own evidence could not separate the committed
  formula from at least one other, and no second ionization channel of the run committed
  the same neutral. A compound matched from a list is asked this too. When the run
  searches formulas, the search also looks at that compound's peak: the formulas its
  element ranges hold for the mass are scored beside the list's, and any the evidence
  cannot tell apart count as rivals, named in the reason. A radical does not count,
  since the run never holds one at *assigned*. A list's presence is evidence for a
  formula, not a label on it, so a list compound is only held at *assigned* where
  nothing plausible competes with it.
- **one peak only** — the formula search found the formula on one peak, and nothing
  else in the run saw it: no line of its own isotope pattern is committed beside it, no
  second ionization channel committed the same neutral, and it was not matched from a
  list. However well it fits, one line is one observation. A faint line matches its
  isotope pattern perfectly by having no other line to match, and on a TOF its mass
  error is judged against a width of several ppm, so the top tier asks for a second
  one: the isotope or adduct evidence [Schymanski et al. 2014][sch14] ask of a formula
  before calling it unequivocal. An isotope line whose mass error does not follow the
  row's (*off its M0*, below) does not count, since it is more likely another peak
  inside the matching window; a line *in doubt* does. A formula matched from your
  target library or a reference list is not asked: the list is its second
  observation.
- **a neighbour's isotope line** — a committed neighbouring compound's isotope pattern
  predicts a line on this peak, and the peak is no more than twice as tall as that line,
  so the line could account for all of it. The neighbour must itself be a reading the
  run stands behind, at *candidate* or above.
- **read as a neighbour's line** — where that neighbour is held at *assigned*, the peak
  is read as its isotopologue instead, at *candidate*: the run first committed it as a
  compound of its own, which is exactly the doubt. The formula it held before is the
  first of its close alternatives, so assigning it by hand puts it back. The peak stays
  what it was, with the reason saying why, where the neighbour is at *candidate*, where
  the peak is a compound of your own target library, where another ionization channel
  of the run committed its neutral, where the neighbour already has a line there, or
  where the peak's mass error does not follow the neighbour's even allowing for what the
  line can deliver (next item). The peak's own isotope lines go with it where the
  neighbour's pattern predicts them too, and are left unassigned where it does not.
  Every check then runs again over what the run now holds, so the calibration is no
  longer measured with the peak as a compound of its own.
- **line in doubt** and **off its M0** — an isotope line's mass error should follow its
  M0's, since both are measured on one axis. Within the instrument's precision it does,
  and the line corroborates the M0. A faint line is placed less well - its own noise
  widens the margin by the square root of how much fainter it is than a line at a
  signal-to-noise of 15 - and a line with another peak within two peak widths of it is
  pushed off its place, by up to a quarter of a width for a neighbour at least as tall
  (read off the file's resolving power). A line that misses its M0 by no more than that
  is in doubt: it is held at *candidate*, and not taken lower for a distance from the
  calibration that the same causes explain. A line that misses by more is off its M0,
  and is held at *candidate* at least, like any row off calibration beyond that. Neither
  corroborates its M0.
- **oxygen lattice** and **carbon-free formula** — shapes a mass search produces rather
  than a source: more than 1.3 oxygens per carbon with at least five oxygens, or no carbon
  at all without being one of the small inorganics these sources make. A curated identity
  is exempt here too, for the same reason: both shapes describe what a search arrives at,
  and a curated row was not arrived at by one.
- **ambiguous nitrogen** and **ambiguous adduct** — the committed ion reads just as well
  as another molecule through another of the run's ionization channels, and no second
  channel of the run settles which. Dimethylformamide with a proton is the same ion as
  acrolein with ammonium: the same mass, the same isotope pattern and the same fit, so the
  spectrum cannot choose between them. *Ambiguous nitrogen* is where the two readings put
  a different number of nitrogen atoms on the compound - an ammonium, urea or nitrate
  adduct against a plain channel, or two such adducts against each other; *ambiguous
  adduct* is the rest, such as a bromide cluster against the deprotonated molecule that
  holds the hydrogen bromide, or a formate adduct against the deprotonated acid that holds
  the formic acid. The row keeps its formula. A radical reading is no rival,
  since the run never holds a radical at *assigned*, and a compound of your own target
  library is exempt, since your curation chose the reading. A second channel settles the
  question only where the sample does not also commit the other molecule through one of
  the mode's own channels: where it does, each reading has an observation of its own, and
  the two are weighed as two opportunistic readings are (*Auto*, above). Where the
  sample shows this row's molecule on a peak at least ten times as bright as the other's,
  the question is settled. Short of that, what decides is the channel the row is read
  through. Through a channel the run opened for itself, the row is held at *candidate*.
  Through one of the mode's own channels - the channels you declared as what the source
  does - the row is held only where the sample shows the other molecule on a peak at
  least ten times as bright as this one's, or shows this molecule through no other of the
  mode's channels at all; otherwise its second channel settles the question, and the
  sentence says the sample shows the other molecule too. The same margin is asked of a
  rival as of the stronger reading of two opportunistic ones. On a nitrate source,
  pinonic acid deprotonated also reads as nopinone with formate: nopinone seen twice as
  brightly as the acid leaves the acid *assigned*, and formaldehyde seen a hundred times
  as brightly as glycolic acid holds that acid at *candidate*. Either way the row keeps
  its formula, and a held row's sentence says the sample shows the other molecule too and
  how the two compare. The row's own peak is
  the one ion both readings explain, so it counts for neither, and neither does another
  peak of the same ion, however it was read. So on a mode with one channel of its own,
  such as protonation in ESI, the row's molecule has no other peak through that channel to
  be weighed on, and a rival the sample shows holds the row at *candidate* however faint
  the rival is. A formula from a loaded reference list is asked too: the run gives it the
  other readings of its ion that the formula search would have considered, shown with its
  close alternatives.
- **off calibration** and **minor channel only** — caps the run applied earlier, restated
  here so every reason is in one place: a mass error far from the run's own fitted
  calibration with nothing corroborating it; and a commitment through a channel the
  ionization mode treats as secondary, with no isotopologue or second channel behind it.
  The last applies to the formula search's own results only. The calibration check
  exempts only a line one of whose isotopologues tracks its mass error: your library's
  lines help measure the calibration, but a list names a compound rather than where each
  of its lines has to sit, so a line of it far off calibration with no isotopologue
  behind it is capped like any other. A formula from a loaded reference list is a prior
  matched against every sample rather than a list assembled for your data, and its
  matches play no part in measuring the sample's own mass accuracy. The calibration's
  centre can follow
  the mass range: where the run's assignments show their mass error changing with m/z the
  way a fixed offset in millidaltons does, growing in ppm as the mass falls, each ion is
  measured from the centre at its own m/z, so a small ion a couple of ppm out can sit
  exactly on calibration.

What a row that keeps its tier kept it on: **second channel** (the same neutral committed
through two or more of the run's ionization channels), **second line** (a line of its own
isotope pattern committed with it, named with its m/z), **on a list** (matched from your
target library or a reference list, named with the list's compound; the formula search
did not elect it), **same ion, settled** (the ion
reads as another molecule too, and something settled which: a second channel - for a
reading through one of the mode's own channels, even where the sample shows the other
molecule too, short of ten times as brightly - the sample showing this molecule
decisively more strongly than the other (see *Auto* above), your target library, or the
other reading being a radical - the sentence says which), **no close
rival** (the evidence separated the formula from every other candidate the run competed
for the peak; where the ion reads another way, it separated the ion from every other ion,
since two readings of one ion are one measurement), or **not measured** (nothing the pass
reads was recorded for the row, which is a statement of absence rather than a finding). An isotopologue **follows its M0**: it is the M0's ion
seen at another isotope, so it takes the M0's answer and loses the top tier with it, and
the inspector shows the M0's reasons beneath its own. What was found about its own line -
in doubt, off its M0, read as the M0's line - is listed above that.

**Beside the reasons.** The peak inspector also names what the row is, and shows two
measurements the reasons read.

- The ***ionization*** follows the formula at the head of the card: the mechanism the ion
  was read through (*[M+H]+*, *[M+NH4]+*, *[M-H]-* ...). The neutral and the ion formula under it
  imply it, but it is half of the assignment, and hovering it spells out the reaction.
- ***reference list***, above the evidence, is what a reference list calls the formula,
  with the list it comes from: the compound the run matched it from, or - marked
  *potential* - a compound a list holds for a formula the run reached through the formula
  search, which is a lead to check rather than a match the run made. A list names a
  formula only where a run of the sample could have matched it from
  that list: in the sample's polarity, inside the list's window, and a radical only from
  a list that allows radicals. A formula match names candidate compounds, not an
  identification. Hovering lists the compounds by name, up to 25, and says how many more
  the lists hold. The same names appear beside the other readings of the ion and beside
  the close alternatives. The ledger shows the run's own match in its **listed as**
  column, beside the formula: the first name, its list and the list's tags, with how
  many more names the run matched, so a row's list reading is there without opening
  the inspector. A lead is not shown there - it is looked up when a row is inspected.
- ***mass z*** sits beside the m/z error. It is the row's distance from the run's own
  mass calibration at its m/z, counted in the calibration's widths. Hovering it gives
  the run's centre and width, and the distances at which *off calibration* caps a row.
  The value is marked when it is past the distance that caps a row at *candidate*.
- ***Same ion, read another way*** sits under the reasons. It lists the other neutrals
  the committed ion reads as through the run's other channels, which *ambiguous
  nitrogen*, *ambiguous adduct* and *same ion, settled* are about. Two such readings
  have the same mass and isotope pattern, so the spectrum cannot choose between them; a
  second channel of the run can.

The close alternatives mark three kinds of entry that are not simply runners-up:

- *same ion*: another reading of the same ion;
- *earlier reading*: what the run first read the peak as, before a neighbour's isotope
  line claimed it;
- *list compound*: a list's compound that a formula from the search took the peak from.

*Use this* on either of the last two puts that reading back.

A reason can name a rule that lowered nothing: a row its evidence already put below
*candidate* keeps that tier, and the rule still says what it found. The run records the
rule set's version and thresholds with its configuration, because a tier can only be
compared between two runs together with the rules that produced it.

## Assigning a peak yourself

--8<-- "_help/assignment-curation.md"

A hand assignment says "this candidate is the better reading of the evidence"; a
verification says "I have evidence of *this grade* that it is right". Keeping the two
apart is what keeps the calibration honest: the labelled record that future confidence
curves are fit on stays a record of stated evidence, not of preferences. It is the same
reason an override drops the engine's calibrated P(correct), which the API serves,
instead of carrying it over
&mdash; the curve was fit to score the engine's arbitration, and a probability quoted
beside a formula it never scored would be a number with nothing behind it.

The run-scoped lifetime follows from what a run is: one reading of the sample, computed
from the data at a moment. Editing a row of it corrects that reading; it is not a
standing instruction, so the next run starts from the data again and knows nothing about
it. Verifications are the layer built to outlive a run &mdash; keyed on the peak, formula
and ionization mechanism rather than on a run &mdash; which is why they carry over a
re-assignment and an override does not.

### Curating a species for the whole batch

In a sample served from the batch ledger (its runs list shows *Batch ledger*), the
inspector's close alternatives are the other identities the batch has seen at that
peak, each with the share of the batch's evidence behind it. *Use this* on one of them
acts on the batch peak rather than on the sample: the chosen identity is pinned as the
species for the whole batch, then measured in every sample that holds the peak. A sample
where it can be measured now reads it with a fit of its own; one where it cannot keeps
what it had. The batch peak claims the pinned formula whatever the samples' vote says -
and says so when the two disagree - and a hand icon beside the formula in the *Batch
peaks* ledger marks it. *Release*, in the inspector's note, undoes it: the samples that
were re-measured go back to what they read before, and the batch decides again.

## Verifying assignments

--8<-- "_help/assignment-verification.md"

The evidence levels follow the field's identification-confidence ladder
([Schymanski et al. 2014][sch14]): a reference standard is a Level-1 identification,
and each weaker level is worth correspondingly less as a label. Verdicts deliberately
capture the *evidence* behind a judgment rather than echoing the model's own score,
so the labelled record stays informative for recalibration.

Verdicts are recorded from the peak inspector, or from the ledger's Verdict column. In the
inspector, each verdict opens a small dialog for an optional note, and *Confirm*'s also
asks for the evidence level. In the ledger the cell is a button, as in the *Batch peaks*
ledger, so an unverified row shows a faint seal that opens a verdict form in place. A
verdict is about the compound, so it is recorded on the family's M0 whichever member the
row is.

### Batch-level verdicts

--8<-- "_help/batch-peak-verdicts.md"

The *Verdict* column of the *Batch peaks* ledger is where a batch-level verdict is
recorded: click the cell to judge the species, change the verdict or retract it. The
samples it covers show it as a borrowed badge - in parentheses in the assignment
ledger, as a dashed pill in the inspector - and the assignment ledger's verdict filter
counts them under it, so *Unverified* lists only rows that show no badge at all. A
per-sample verdict always wins: verifying one of those samples yourself records an
exception, and where the two disagree the per-sample badge says so.

Confirming or rejecting names the formula you judged. If the consensus has moved since
the ledger was read - another sample's fold can move it - the verdict is refused and the
row reloads, so you never confirm a formula you did not see. A verdict whose formula the
consensus has since left stays on record, outlined as stale, until you judge the new
formula or retract it. Batch-level verdicts are kept apart from the labelled record that
confidence calibration is fit on: one judgment fanned out over a batch would count as
many correlated labels, so it counts as none.

## Assignment runs

--8<-- "_help/assignment-runs.md"

Publishing a run from another engine is what makes the two comparable on the same
sample: both live in the same run history, so selecting one and then the other
switches the ledger between them peak for peak. What an imported run may assert
stops short of what Mascope presents as its own judgement &mdash; it declares the
tier bands it used and every row is checked against them, it discloses what it
calibrated against, and it writes no calibrated P(correct) on its rows.
The in-app engine's name is reserved, so the chip cannot be forged. Verifications
recorded against an imported run are kept and shown, but stay out of the
instrument-wide confidence calibration, whose labels come only from runs this
server computed.

A sample whose peaks are in the batch ledger but that has no run of its own &mdash; its
runs were deleted or pruned, or it was folded into the batch without one &mdash; is shown
from the batch ledger instead. The run selector lists it as **Batch ledger**; the
ledger carries what the batch knows about each peak (formula, adduct, tier, fit and
isotopologue family), and the inspector's close alternatives are what
the rest of the batch saw at that m/z. It carries no mass error or isotope label, and
it cannot be edited by hand &mdash; assign the sample for a ledger of its own. Verdicts
can still be recorded against it.

A sample served from the batch ledger (its run reads *Batch ledger*) carries each peak's
fit and tier, but not the numbers a run would have stored beside them: the m/z and
abundance error of each isotopologue, the isotope labels, the chemical plausibility, the
evidence the tier was read off. The peak inspector measures those on demand when you focus
such a peak - the family's composition is scored against the sample's own peaks, through
its M0 - and fills them in a moment later. They are computed for the view and never stored;
run an assignment on the sample to persist a full ledger of its own.

The inspector's *confidence* row stays in place for such a sample, so the card reads the
same whichever way a sample is served. The arbitration confidence, which only a run of the
sample computes by weighing the peak's candidates against each other, reads as a dash with
that explanation.
The isotopologue table always lists the main peak, even when the pattern has no other
peaks, so the focused peak's m/z is read in the same place on every card. Its labels
count from the monoisotopic peak, the way an isotope table does - a bromine-rich ion reads
M0, M+2, M+4, M+6, with M0 the lightest peak of the cluster rather than the tallest - and
its abundances are fractions of the most abundant isotopologue, so nothing reads above
100 %. Where a line's isotopologue formula is known, the line is named by how it differs
from the monoisotopic peak instead, such as `[13C]` or `[81Br]2`. For an ion made with a
labelled reagent such as 15N-nitrate, whose monoisotopic peak is the labelled one, the
reagent's unlabelled remainder - its M-1 - reads `[14N]`, its labelled atom at 14N. When
none of the predicted isotopologues pairs with the family's main peak, the card keeps the
pattern's numbers and adds a *main peak* line saying which prediction came nearest, how
far away it lies, and whether it paired with a peak elsewhere.

The sample browser marks each sample's assignment status with a tag badge in a column of
its own, after the sample name by default (the table-controls cog moves or hides it like
any other column): green for a sample with a completed run of its own (the
tooltip names the engine, its version and the time), the accent colour for a sample served
from the batch ledger without a run of its own, faint for one with nothing assigned yet.
The tooltip also says how many of the sample's peaks carry an assignment in the ledger.

## Batch peaks

--8<-- "_help/batch-peaks.md"

Every processed sample folds into the batch peaks as it arrives - assigned from the
known compositions, without a per-sample run of its own - and so does every completed
assignment run. *Rebuild batch ledger* does the same for a whole batch on demand: a
sample with an assignment run folds from it, one without is assigned from the known
compositions and folded without a run (a blank, or a sample whose m/z calibration is
not verified, is skipped). Use it to populate a batch that predates the ledger or was
never assigned, or to refresh after an import. There is no batch-wide assignment run:
the untargeted search runs once per batch peak instead (below), and a species is
curated once at its batch peak rather than sample by sample.

*Search untargeted*, beside *Rebuild batch ledger*, first asks for the search's parameters
- m/z precision, formula ranges, the peak ceiling, the intensity threshold and the number
of alternatives kept, the same settings as a per-sample run's untargeted stage, applied
to the whole search - and then runs the untargeted composition search
for the batch peaks nothing has assigned yet &mdash; once per species, on its brightest
peak in that sample's own spectrum &mdash; and then measures the composition it found
against every other sample the species was seen in, so each carries a fit of its own.
It writes no per-sample runs: the results appear in the batch ledger and in each
sample's view, marked as untargeted. An assignment run on a sample still takes
precedence for that sample.

The whole ledger leaves the app as a CSV from the view menu behind the cog: *Export
ledger (CSV)* writes one row per member peak - every sample's reading of every batch
peak, with the batch peak's consensus beside it - and the browser downloads the file
when it is ready. The same rows are one call away in the SDK
(`mascope.load_batch_ledger(...)`, or `mascope.batch_peaks.members(batch_id)` per
batch), which is the shortest way from a batch's assignment to any other format.

A batch peak the source accounts for reads as the sample ledger reads it. Where more
of its members are peaks the reagent or artifact pass claimed - the reagent's ions, the
air's, the calibrant beam's, or an intense peak's ringing - than peaks a formula was
assigned to, the row shows the ion in the Formula column, written with its charge, and
the *reagent* or *artifact* chip in place of a tier; it counts under that chip in the strip above the
ledger, after the tiers, rather than as unassigned. An ion's isotope lines fold under it
as a compound's isotopologues do. A few samples reading the ion as a neutral - a dim
file the claim missed, or a sample assigned by an older engine - do not outvote the rest,
and a tie stays with the formula. The untargeted search leaves these peaks alone, as a
run does. A batch ledger folded before the ledger learned this reads its reagent peaks
as unassigned until its samples are folded again (*Rebuild batch ledger*).

The *Verdict* column, last in the ledger, records and shows a
[batch-level verdict](#batch-level-verdicts) on the species: one judgment that covers
every sample in the batch without a verdict of its own.

The rows you tick in the *Batch peaks* ledger are what the batch chart draws, one
trace per batch peak. The ledger lists every anchor in the batch, which on a large
batch is far more than a chart can usefully show, so a selection is capped at 300 —
select all on a bigger ledger takes the first 300 rows and tells you so. Which 300 is
up to you: filter the ledger first, with the tier chips or the Formula column's
filter, and then select.

One row per species, not per peak: a compound's isotopologue peaks are
folded under its main peak and counted in the **+N** marker beside the formula, the
same way the per-sample assignment ledger folds them. The *Isotopologues* toggle,
behind the cog at the end of the tier-chip row, unfolds them as indented rows
underneath. The link is derived rather than given — a
batch peak is an m/z anchor and carries no compound of its own — so a peak is folded
only when its per-sample assignments agree, across most of the samples that assigned
it, that it belongs to another anchor's compound. One that is an isotopologue in one
sample and a species in its own right in the rest stays a row of its own.

The *Intensity* column is the highest intensity the species reaches in any sample of
the batch, in the instrument's own unit (summed peak heights on an Orbitrap, summed
peak areas on a TOF). It is a property of the trace rather than of the assignment, so
unassigned anchors carry one too — sorting by it is how you find the largest thing in
the batch that nothing was assigned to.

Hover a column header for a one-line reminder of what the column holds at batch level:
the m/z is the anchor's bin, the intensity the brightest sample's, the formula and tier a
consensus over the members. *Listed as* is what a reference list calls the consensus
formula, where a sample's run matched it from a list: the ledger keeps the listing of each
identity a member brought matched from a list, through whichever channel, and shows the
consensus formula's. A batch ledger folded before this column existed shows it once its
samples are folded again (*Rebuild batch ledger*). The column a ledger is sorted by is kept per ledger - it
survives the switch between the *Batch peaks* ledger and a sample's ledger, and a reload.

Because a batch peak is one identity for a species across the batch, the focused peak
follows you between samples: pick another sample and the inspector and the spectrum
stay on the same species rather than on nothing. It is the batch peak that decides
what "the same" means here, not the nearest m/z — so a peak follows only where that
species was actually observed. Move to a sample where it was not, and the selection
clears the way it always did. The same is true in a batch whose batch peaks have not
been computed yet: there is no anchor to follow, so nothing does. Picking a peak
yourself always wins over this — it will not overwrite a choice you just made, or
refill a selection you cleared.

To see how an assignment looks in a spectrum, use the arrow beside a row's intensity: it
opens the brightest sample that holds the batch peak, with that peak focused, in the
*Sample* tab - the same click-through as a data point in the batch chart, without having
to tell which trace to click when several are plotted. When an earlier run is on screen,
the jump reads that run's members.

The sample ledger reaches the batch chart too: the chart icon at the start of a row puts that species
into the chart (it selects the batch peak the row's peak folded into, in the *Batch peaks*
ledger) or takes it out, without leaving the sample. The chart draws at most as many species
as the *Batch peaks* selection allows, and a peak the ledger does not hold cannot be plotted.
And a peak focused from the batch side - the chart, the arrow beside a batch peak's intensity
- scrolls its row into view in the sample ledger.

### Batch runs

--8<-- "_help/batch-runs.md"

The run selector beside *Rebuild batch ledger* lists the batch's runs newest first, each
with what it did and, for a search, the parameters it was given. The current run is the
live ledger; picking an earlier one shows the *Batch peaks* ledger and the chart as that
run left them, read-only - verdicts and curation act on the current run, so the Verdict
column waits until you pick it again. A run that fails is kept and marked, and never
becomes current. The same history is one call away in the SDK
(`mascope.batch_peaks.runs(batch_id)`, and `list(batch_id, run_id=...)` for the species
table as an earlier run left it).

### Importing an engine's batch result

An external engine that works on the batch as a whole - one identity per m/z - can
land its result on the batch ledger as a run of its own, through the SDK
(`mascope.batch_peaks.import_run(batch_id, rows, engine=..., engine_version=...)`) or
`POST /api/batch-peaks/batch/{id}/runs/import`. Each row is matched to the batch peak
nearest its m/z (within 5 ppm by default) and its composition is then measured against
every sample that holds that peak, so the ledger shows Mascope's own fit of the engine's
formula, with the engine named as the source. Curated batch peaks are left alone, as are
isotopologue peaks, and rows whose adduct could not be resolved to a mechanism; the run's
summary counts what landed and why the rest did not. The ledger as it was stays under
the previous run in the run selector, so the two views can be compared, and *Rebuild
batch ledger* puts Mascope's own view back.

## References

- <a id="kf06"></a>Kind, T.; Fiehn, O. *Metabolomic database annotations via query of
  elemental compositions: mass accuracy is insufficient even at less than 1 ppm.* BMC
  Bioinformatics 2006, 7:234.
  [link](https://bmcbioinformatics.biomedcentral.com/articles/10.1186/1471-2105-7-234)
- <a id="kf07"></a>Kind, T.; Fiehn, O. *Seven Golden Rules for heuristic filtering of
  molecular formulas obtained by accurate mass spectrometry.* BMC Bioinformatics 2007,
  8:105. [link](https://bmcbioinformatics.biomedcentral.com/articles/10.1186/1471-2105-8-105)
  ([open access](https://pmc.ncbi.nlm.nih.gov/articles/PMC1851972/))
- <a id="bl07"></a>Böcker, S.; Lipták, Z. *A fast and simple algorithm for the money
  changing problem.* Algorithmica 2007, 48(4):413–432.
  [link](https://doi.org/10.1007/s00453-007-0162-8)
- <a id="bo09"></a>Böcker, S.; Letzel, M. C.; Lipták, Z.; Pervukhin, A. *SIRIUS:
  decomposing isotope patterns for metabolite identification.* Bioinformatics 2009,
  25(2):218–224.
  [link](https://academic.oup.com/bioinformatics/article/25/2/218/218950)
- <a id="du19"></a>Dührkop, K. et al. *SIRIUS 4: a rapid tool for turning tandem mass
  spectra into metabolite structure information.* Nature Methods 2019, 16:299–302.
  [link](https://www.nature.com/articles/s41592-019-0344-8)
- <a id="sch17"></a>Scheubert, K. et al. *Significance estimation for large-scale
  metabolomics annotations by spectral matching.* Nature Communications 2017, 8:1494.
  [link](https://www.nature.com/articles/s41467-017-01318-5)
- <a id="platt"></a>Platt, J. *Probabilistic outputs for support vector machines and
  comparisons to regularized likelihood methods.* Advances in Large Margin Classifiers,
  1999. [link](https://en.wikipedia.org/wiki/Platt_scaling)
- <a id="sch14"></a>Schymanski, E. L. et al. *Identifying small molecules via high
  resolution mass spectrometry: communicating confidence.* Environ. Sci. Technol. 2014,
  48(4):2097–2098. [link](https://pubs.acs.org/doi/10.1021/es5002105)
- <a id="hy15"></a>Hyttinen, N.; Kupiainen-Määttä, O.; Rissanen, M. P.; Muuronen, M.;
  Ehn, M.; Kurtén, T. *Modeling the charging of highly oxidized cyclohexene ozonolysis
  products using nitrate-based chemical ionization.* J. Phys. Chem. A 2015,
  119(24):6339–6345. [link](https://doi.org/10.1021/acs.jpca.5b01818)
- <a id="wa21"></a>Wang, M.; He, X.-C.; Finkenzeller, H.; Iyer, S.; Chen, D.; Shen, J.;
  Simon, M.; Hofbauer, V.; Kirkby, J.; Curtius, J.; Maier, N.; Kurtén, T.; Worsnop, D. R.;
  Kulmala, M.; Rissanen, M.; Volkamer, R.; Tham, Y. J.; Donahue, N. M.; Sipilä, M.
  *Measurement of iodine species and sulfuric acid using bromide chemical ionization mass
  spectrometers.* Atmos. Meas. Tech. 2021, 14:4187–4202.
  [link](https://doi.org/10.5194/amt-14-4187-2021)
- <a id="sum07"></a>Sumner, L. W. et al. *Proposed minimum reporting standards for chemical
  analysis (Metabolomics Standards Initiative).* Metabolomics 2007, 3:211–221.
  [link](https://doi.org/10.1007/s11306-007-0082-2)
- <a id="good70"></a>Good, A.; Durden, D. A.; Kebarle, P. *Ion-molecule reactions in pure
  nitrogen and nitrogen containing traces of water at total pressures 0.5-4 torr. Kinetics
  of clustering reactions forming H+(H2O)n.* J. Chem. Phys. 1970, 52:212–221.
  [link](https://doi.org/10.1063/1.1672667)
- <a id="good70b"></a>Good, A.; Durden, D. A.; Kebarle, P. *Mechanism and rate constants
  of ion-molecule reactions leading to formation of H+(H2O)n in moist oxygen and air.* J.
  Chem. Phys. 1970, 52:222–229. [link](https://doi.org/10.1063/1.1672668)
- <a id="sha66"></a>Shahin, M. M. *Mass-spectrometric studies of corona discharges in air
  at atmospheric pressures.* J. Chem. Phys. 1966, 45:2600–2605.
  [link](https://doi.org/10.1063/1.1727980)
- <a id="sha69"></a>Shahin, M. M. *Nature of charge carriers in negative coronas.* Appl.
  Opt. 1969, 8(S1):106–110. [link](https://doi.org/10.1364/AO.8.S1.000106)
- <a id="sab12"></a>Sabo, M.; Matejčík, S. *Corona discharge ion mobility spectrometry
  with orthogonal acceleration time of flight mass spectrometry for monitoring of volatile
  organic compounds.* Anal. Chem. 2012, 84:5327–5334.
  [link](https://doi.org/10.1021/ac300722s)
- <a id="sab13"></a>Sabo, M.; Matejčík, S. *A corona discharge atmospheric pressure
  chemical ionization source with selective NO+ formation and its application for
  monoaromatic VOC detection.* Analyst 2013, 138:6907–6912.
  [link](https://doi.org/10.1039/c3an00964e)
- <a id="kol04"></a>Kolakowski, B. M.; Grossert, J. S.; Ramaley, L. *Studies on the
  positive-ion mass spectra from atmospheric pressure chemical ionization of gases and
  solvents used in liquid chromatography and direct liquid injection.* J. Am. Soc. Mass
  Spectrom. 2004, 15:311–324. [link](https://doi.org/10.1016/j.jasms.2003.10.019)
- <a id="dus25"></a>Dusanter, S.; Holzinger, R.; Klein, F.; Salameh, T.; Jamar, M.
  *Measurement guidelines for VOC analysis by PTR-MS.* ACTRIS standard operating
  procedure, 2025.
  [link](https://actris.eu/sites/default/files/inline-files/PTRMS%20SOP%20(April2025).pdf)
- <a id="han95"></a>Hansel, A.; Jordan, A.; Holzinger, R.; Prazeller, P.; Vogel, W.;
  Lindinger, W. *Proton transfer reaction mass spectrometry: on-line trace gas analysis at
  the ppb level.* Int. J. Mass Spectrom. Ion Processes 1995, 149-150:609–619.
  [link](https://doi.org/10.1016/0168-1176(95)04294-U)
- <a id="pfe20"></a>Pfeifer, J.; Simon, M.; Heinritzi, M.; Piel, F.; Weitz, L.; Wang, D.;
  Granzin, M.; Müller, T.; Bräkling, S.; Kirkby, J.; Curtius, J.; Kurtén, A. *Measurement
  of ammonia, amines and iodine compounds using protonated water cluster chemical
  ionization mass spectrometry.* Atmos. Meas. Tech. 2020, 13:2501–2522.
  [link](https://doi.org/10.5194/amt-13-2501-2020)
- <a id="ska04"></a>Skalný, J. D.; Mikoviny, T.; Matejčík, S.; Mason, N. J. *An analysis
  of mass spectrometric study of negative ions extracted from negative corona discharge in
  air.* Int. J. Mass Spectrom. 2004, 233:317–324.
  [link](https://doi.org/10.1016/j.ijms.2004.01.012)
- <a id="ska07"></a>Skalný, J. D.; Horváth, G.; Mason, N. J. *Mass spectrometric analysis
  of small negative ions (e/m < 100) produced by Trichel pulse negative corona discharge
  fed by ozonised air.* J. Optoelectron. Adv. Mater. 2007, 9:887–893.
  [link](https://oro.open.ac.uk/11208/)
- <a id="nag06"></a>Nagato, K.; Matsui, Y.; Miyata, T.; Yamauchi, T. *An analysis of the
  evolution of negative ions produced by a corona ionizer in air.* Int. J. Mass Spectrom.
  2006, 248:142–147. [link](https://doi.org/10.1016/j.ijms.2005.12.001)
- <a id="sek11"></a>Sekimoto, K.; Takayama, M. *Observations of different core water
  cluster ions Y-(H2O)n (Y = O2, HOx, NOx, COx) and magic number in atmospheric pressure
  negative corona discharge mass spectrometry.* J. Mass Spectrom. 2011, 46:50–60.
  [link](https://doi.org/10.1002/jms.1870)
- <a id="sek12"></a>Sekimoto, K.; Sakai, M.; Takayama, M. *Specific interaction between
  negative atmospheric ions and organic compounds in atmospheric pressure corona discharge
  ionization mass spectrometry.* J. Am. Soc. Mass Spectrom. 2012, 23:1109–1119.
  [link](https://doi.org/10.1007/s13361-012-0363-5)
- <a id="fuj23"></a>Fujishima, S.; Sekimoto, K.; Takayama, M. *Identification of negative
  ion at m/z 20 produced by atmospheric pressure corona discharge ionization under ambient
  air.* Mass Spectrom. 2023, 12:A0124.
  [link](https://doi.org/10.5702/massspectrometry.A0124)
- <a id="asa23"></a>Asakawa, D.; Hiraoka, K. *Comments on "Identification of negative ion
  at m/z 20 produced by atmospheric pressure corona discharge ionization under ambient
  air".* Mass Spectrom. 2023, 12:A0140.
  [link](https://doi.org/10.5702/massspectrometry.A0140)
- <a id="tak26"></a>Takayama, M. *Reply to comment on "Identification of negative ion at
  m/z 20 produced by atmospheric pressure corona discharge ionization under ambient air".*
  Mass Spectrom. 2026, 15:A0185. [link](https://doi.org/10.5702/massspectrometry.A0185)
- <a id="mat23"></a>Matas, E.; Moravský, L.; Ilbeigi, V.; Matejčík, S. *Negative
  atmospheric pressure chemical ionisation of NO2 by O2-.CO2.(H2O)n studied by ion
  mobility spectrometry.* Eur. Phys. J. D 2023, 77:21.
  [link](https://doi.org/10.1140/epjd/s10053-023-00603-x)
- <a id="ewi09"></a>Ewing, R. G.; Waltman, M. J. *Mechanisms for negative reactant ion
  formation in an atmospheric pressure corona discharge.* Int. J. Ion Mobil. Spectrom.
  2009, 12:65–72. [link](https://doi.org/10.1007/s12127-009-0019-8)
- <a id="jok12"></a>Jokinen, T.; Sipilä, M.; Junninen, H.; Ehn, M.; Lönn, G.; Hakala, J.;
  Petäjä, T.; Mauldin, R. L.; Kulmala, M.; Worsnop, D. R. *Atmospheric sulphuric acid and
  neutral cluster measurements using CI-APi-TOF.* Atmos. Chem. Phys. 2012, 12:4117–4125.
  [link](https://doi.org/10.5194/acp-12-4117-2012)
- <a id="zha26"></a>Zhang, J.; Zhang, Y.; Koskenvaara, H.; Zhao, J.; Ehn, M. *Gas-phase
  products from nitrate radical oxidation of five monoterpenes: insights from free-jet
  flow-tube experiments.* Atmos. Chem. Phys. 2026, 26:3933–3949.
  [link](https://doi.org/10.5194/acp-26-3933-2026)
- <a id="san16"></a>Sanchez, J.; Tanner, D. J.; Chen, D.; Huey, L. G.; Ng, N. L. *A new
  technique for the direct detection of HO2 radicals using bromide chemical ionization
  mass spectrometry (Br-CIMS): initial characterization.* Atmos. Meas. Tech. 2016,
  9:3851–3861. [link](https://doi.org/10.5194/amt-9-3851-2016)
- <a id="ris19"></a>Rissanen, M. P.; Mikkilä, J.; Iyer, S.; Hakala, J. *Multi-scheme
  chemical ionization inlet (MION) for fast switching of reagent ion chemistry in
  atmospheric pressure chemical ionization mass spectrometry (CIMS) applications.* Atmos.
  Meas. Tech. 2019, 12:6635–6646. [link](https://doi.org/10.5194/amt-12-6635-2019)
- <a id="dor21"></a>Dörich, R.; Eger, P.; Lelieveld, J.; Crowley, J. N. *Iodide CIMS and
  m/z 62: the detection of HNO3 as NO3- in the presence of PAN, peroxyacetic acid and
  ozone.* Atmos. Meas. Tech. 2021, 14:5319–5332.
  [link](https://doi.org/10.5194/amt-14-5319-2021)
- <a id="gom22"></a>Gómez Martín, J. C.; Lewis, T. R.; James, A. D.; Saiz-Lopez, A.;
  Plane, J. M. C. *Insights into the chemistry of iodine new particle formation: the role
  of iodine oxides and the source of iodic acid.* J. Am. Chem. Soc. 2022, 144:9240–9253.
  [link](https://doi.org/10.1021/jacs.1c12957)
- <a id="shc24"></a>Shcherbinin, A.; Finkenzeller, H.; Mikkilä, J.; Kontro, J.; Vinkvist,
  N.; Kangasluoma, J.; Rissanen, M. *From hydrocarbons to highly functionalized molecules
  in a single measurement: comprehensive analysis of complex gas mixtures by
  multi-pressure chemical ionization mass spectrometry.* Anal. Chem. 2024, 96:19926–19932.
  [link](https://doi.org/10.1021/acs.analchem.4c03859)
- <a id="shc25"></a>Shcherbinin, A. et al. *Uronium from X-ray-desorbed urea enables
  sustainable ultrasensitive detection of amines and semivolatiles.* Anal. Chem. 2025,
  97:21282–21290. [link](https://doi.org/10.1021/acs.analchem.5c02239)
- <a id="easyic"></a>Thermo Fisher Scientific. *EASY-ETD and EASY-IC Ion Sources User
  Guide, for the Orbitrap Tribrid series mass spectrometer.* Document 80000-97515,
  Revision A, 2018.
  [link](https://documents.thermofisher.com/TFS-Assets/CMD/manuals/man-80000-97515-easy-etd-ic-ion-sources-user-man8000097515-en.pdf)
- <a id="leb23"></a>Leborgne, C.; Meudec, E.; Sommerer, N.; Masson, G.; Mouret, J.-R.;
  Cheynier, V. *Untargeted metabolomics approach using UHPLC-HRMS to unravel the impact of
  fermentation on color and phenolic composition of rose wines.* Molecules 2023, 28:5748.
  [link](https://doi.org/10.3390/molecules28155748)
- <a id="ash26"></a>Ashbacher, S. M.; Xie, D.-Y.; Muddiman, D. C. *Differentiation of
  wild-type and PAP1-overexpressing tobacco by volatile organic compound profiling using
  TP-SESI mass spectrometry.* Anal. Bioanal. Chem. 2026, 418:5577–5585.
  [link](https://doi.org/10.1007/s00216-026-06630-y)
- <a id="mar16"></a>Martens, J.; Berden, G.; Oomens, J. *Structures of fluoranthene
  reagent anions used in electron transfer dissociation and proton transfer reaction
  tandem mass spectrometry.* Anal. Chem. 2016, 88:6126–6129.
  [link](https://doi.org/10.1021/acs.analchem.6b01483)
- <a id="wes18"></a>West, B.; Rodriguez Castillo, S.; Sit, A.; Mohamad, S.; Lowe, B.;
  Joblin, C.; Bodi, A.; Mayer, P. M. *Unimolecular reaction energies for polycyclic
  aromatic hydrocarbon ions.* Phys. Chem. Chem. Phys. 2018, 20:7195–7205.
  [link](https://doi.org/10.1039/c7cp07369k)
- <a id="nist"></a>Linstrom, P. J.; Mallard, W. G. (eds.). *NIST Chemistry WebBook, NIST
  Standard Reference Database Number 69*; electron-ionization spectra from the NIST Mass
  Spectrometry Data Center. [link](https://doi.org/10.18434/T4D303)
- <a id="wan03"></a>Wang, T.; Španěl, P.; Smith, D. *Selected ion flow tube, SIFT, studies
  of the reactions of H3O+, NO+ and O2+ with eleven C10H16 monoterpenes.* Int. J. Mass
  Spectrom. 2003, 228:117–126. [link](https://doi.org/10.1016/S1387-3806(03)00271-9)
- <a id="scn03"></a>Schoon, N.; Amelynck, C.; Vereecken, L.; Arijs, E. *A selected ion
  flow tube study of the reactions of H3O+, NO+ and O2+ with a series of monoterpenes.*
  Int. J. Mass Spectrom. 2003, 229:231–240.
  [link](https://doi.org/10.1016/S1387-3806(03)00343-9)
- <a id="mat17"></a>Materić, D.; Lanza, M.; Sulzer, P.; Herbig, J.; Bruhn, D.; Gauci, V.;
  Mason, N.; Turner, C. *Selective reagent ion-time of flight-mass spectrometry study of
  six common monoterpenes.* Int. J. Mass Spectrom. 2017, 421:40–50.
  [link](https://doi.org/10.1016/j.ijms.2017.06.003)
- <a id="tan03"></a>Tani, A.; Hayward, S.; Hewitt, C. N. *Measurement of monoterpenes and
  related compounds by proton transfer reaction-mass spectrometry (PTR-MS).* Int. J. Mass
  Spectrom. 2003, 223-224:561–578. [link](https://doi.org/10.1016/S1387-3806(02)00880-1)
- <a id="kar18"></a>Kari, E.; Miettinen, P.; Yli-Pirilä, P.; Virtanen, A.; Faiola, C. L.
  *PTR-ToF-MS product ion distributions and humidity-dependence of biogenic volatile
  organic compounds.* Int. J. Mass Spectrom. 2018, 430:87–97.
  [link](https://doi.org/10.1016/j.ijms.2018.05.003)
- <a id="ish26"></a>Ishihara, R.; Fukuyama, D.; Sekimoto, K. *Interpretation of
  alpha-pinene mass spectra in APCI-like ambient mass spectrometry using GC-coupled
  atmospheric pressure corona discharge ionization.* Mass Spectrom. 2026, 15:A0190.
  [link](https://doi.org/10.5702/massspectrometry.A0190)
- <a id="sch03"></a>Schlosser, A.; Volkmer-Engert, R. *Volatile polydimethylcyclosiloxanes
  in the ambient laboratory air identified as source of extreme background signals in
  nanoelectrospray mass spectrometry.* J. Mass Spectrom. 2003, 38:523–525.
  [link](https://doi.org/10.1002/jms.465)
- <a id="ver08"></a>Veres, P.; Roberts, J. M.; Warneke, C.; Welsh-Bon, D.; Zahniser, M.;
  Herndon, S.; Fall, R.; de Gouw, J. *Development of negative-ion proton-transfer
  chemical-ionization mass spectrometry (NI-PT-CIMS) for the measurement of gas-phase
  organic acids in the atmosphere.* Int. J. Mass Spectrom. 2008, 274:48–55.
  [link](https://doi.org/10.1016/j.ijms.2008.04.032)
- <a id="ber11"></a>Bertram, T. H.; Kimmel, J. R.; Crisp, T. A.; Ryder, O. S.; Yatavelli,
  R. L. N.; Thornton, J. A.; Cubison, M. J.; Gonin, M.; Worsnop, D. R. *A
  field-deployable, chemical ionization time-of-flight mass spectrometer.* Atmos. Meas.
  Tech. 2011, 4:1471–1479. [link](https://doi.org/10.5194/amt-4-1471-2011)

[kf06]: #kf06
[kf07]: #kf07
[bl07]: #bl07
[bo09]: #bo09
[du19]: #du19
[sch17]: #sch17
[sch14]: #sch14
[sum07]: #sum07
[hy15]: #hy15
[wa21]: #wa21
[platt]: #platt
[good70]: #good70
[good70b]: #good70b
[sha66]: #sha66
[sha69]: #sha69
[sab12]: #sab12
[sab13]: #sab13
[kol04]: #kol04
[dus25]: #dus25
[han95]: #han95
[pfe20]: #pfe20
[ska04]: #ska04
[ska07]: #ska07
[nag06]: #nag06
[sek11]: #sek11
[sek12]: #sek12
[fuj23]: #fuj23
[asa23]: #asa23
[tak26]: #tak26
[mat23]: #mat23
[ewi09]: #ewi09
[jok12]: #jok12
[zha26]: #zha26
[san16]: #san16
[ris19]: #ris19
[dor21]: #dor21
[gom22]: #gom22
[shc24]: #shc24
[shc25]: #shc25
[easyic]: #easyic
[leb23]: #leb23
[ash26]: #ash26
[mar16]: #mar16
[wes18]: #wes18
[nist]: #nist
[wan03]: #wan03
[scn03]: #scn03
[mat17]: #mat17
[tan03]: #tan03
[kar18]: #kar18
[ish26]: #ish26
[sch03]: #sch03
[ver08]: #ver08
[ber11]: #ber11
