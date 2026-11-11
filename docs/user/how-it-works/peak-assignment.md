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
  against any [reference lists](../guides/reference-lists.md) your deployment has
  loaded; the best-fitting known composition wins the peak. A reference list is matched only within its own element,
  carbon and mass window, under a ceiling the sample's chemistry context sets; only in
  the polarity it is detected in; and it contributes radicals only if it allows them.
  The target library is read through the ionization mechanisms of the sample's mode; a
  reference list also through the channels the run opens for itself (*A list through
  the channels a run opens*, below). Only mechanisms of one molecule, a single charge
  and no neutral loss are read: a dimer (`[2M+H]+`), a doubly charged ion
  (`[M+2H]2+`) or a neutral loss (`[M+H-H2O]+`) on the mode is left out of the run,
  and the server logs it once.
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

**What lets a secondary channel's reading reach assigned.** A formula the search reads
through a secondary channel, carbonate on a nitrate source or ammonium on a uronium one,
is capped at *candidate* unless something says which molecule clustered with the
channel's carrier: a loaded reference list or your target library names the molecule, or
the sample commits the same molecule through one of the mode's own channels. The
reading's own isotope lines are not enough on their own. A 13C line proves how many
carbons the ion holds, never which molecule clustered: benzene and methanol read through
the carbonate channel reached *assigned* on that line alone. The lines still count for
the reading as any line does, in its fit and as a second peak of its ion (*one peak
only*, below). The row's verdict records whether it had them.

**A list through the channels a run opens.** A reference list names a compound, not the
channel it is seen through, so Stage A reads a list through the channels the run opened
for itself as well as through the mode's own mechanisms. On a charge-transfer source,
whose mode declares electron transfer alone, a cyclic siloxane's methyl-loss ion, its
base peak there, and its protonated molecule beside it are named from the list. A
reading through an opened channel is capped at *candidate* unless its own isotope lines
or the same compound through one of the mode's own channels corroborate it. The list
already names its molecule, which is what a search's reading has to find elsewhere, so
here an isotope line is enough. Through a channel
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

**Ladders of the source's own.** A positive source carries solvent vapour, and builds
clusters of it: two molecules sharing a proton, a molecule on a hydronium, a molecule
less a hydride holding a second one. Ethanol's dimer at m/z 93.091 implies a neutral,
C4H12O2, that is no molecule, so no stage can read it; acetone's dimer at m/z 117.091 is
also protonated C6H12O2, an ordinary acid or ester, and a stage reads it as one. A
source's solvent is not the sample. The charge-transfer and urea sources list water,
methanol, ethanol and acetone, and the pass claims the rungs of their ladders that a work
names: methanol's protonated dimer to tetramer [Grimsrud & Kebarle 1973][gri73], [Larson
& McMahon 1982][lar82]; ethanol's protonated dimer and acetone's to its tetramer [Larson &
McMahon 1982][lar82]; each solvent's protonated molecule with one water [Meot-Ner
1984][mot84], and methanol's dimer with one; and ethanol and acetone less a hydride
holding a second molecule, all in the ion clustering data of the [NIST WebBook][nist]. A
mixed cluster of two solvents, ethanol's trimer and methanol less a hydride are named by
no work found and left to the stages.

A ladder claims a rung on its own shape, not on a name alone, and every one of these must
hold:

- **the ladder grows from an ion the spectrum shows**: the protonated solvent, or the
  solvent less a hydride where the run searches hydride abstraction, at its mass and at a
  thousandth of the base peak or more. That ion is not claimed: protonated acetone is the
  sample's acetone to the stages;
- **the ladder is contiguous from it**: the ion one step below the rung is in the
  spectrum, so a dimer stands on its monomer and a trimer on its dimer;
- **every rung's mass fits at the run's precision**, against the masses the source's own
  ions corrected, and so does the step: the two peaks are one solvent's or one water's
  exact mass apart;
- **the heights fall along the ladder**: a rung stands no more than three times the rung
  below it. A proton-bound dimer is the more stable ion where the vapour is dense, so the
  fall is not asked to the count; a rung several times taller carries something else;
- **a rung displaces a molecule only where its ladder outweighs it.** Where the rung's ion
  also reads as a molecule through one of the run's channels, a closed-shell formula with
  carbon in it, the rung takes the peak only if the ion it grew from stands at least ten
  times taller than any peak that molecule shows of its own through another of the run's
  channels. Acetone's dimer displaces C6H12O2 on a spectrum where protonated acetone is
  loud and C6H12O2's radical cation is not there; where the molecule shows its own ion,
  the stages read the peak;
- **a rung holding water never displaces a molecule.** A humid source hydrates every ion
  it makes, the proton's own ladder first [Good et al. 1970a][good70] and the analytes'
  ions with it [Sunner et al. 1988][sun88], so a step of one water is the commonest
  spacing in a spectrum and no evidence that the lower peak is a solvent's. Ethanol on a
  hydronium, whose neutral is no molecule, is claimed on its ladder; acetone on a
  hydronium, which is also protonated C3H8O2, is left to the stages.

On a nitrate source the reagent's acid dimer core, (HNO3)2.NO3- [Jokinen et al.
2012][jok12], is rung zero of a water ladder of its own, on any instrument: its first
three hydrates are claimed under the same rules, one water at a time above it. No work
found names them, and their rows say what shows them: the ladder in the spectrum. How far
a reagent's water ladder reaches over a batch is the batch's question, not one spectrum's.

A rung's row names its ion and the works that name it, as a library ion's does, with its
ladder beside them: the ion it grew from, the rung below and the step between them, and
the molecules its ion also reads as, each with the peak that molecule showed elsewhere if
it showed one. Where one ion is a rung of two ladders, methanol's dimer and ethanol on a
hydronium among them, the row names the first ladder that holds and lists the other. A
rung's isotope lines are claimed with it.

**How the rows read.** A reagent row names no compound, so the sample's assignment ledger
shows its ion in the formula column instead, written with its charge (`Br-`, `C16H10+`),
and the inspector heads the card with it; the *reagent* chip beside it says the source
made it. An ion's isotope lines are claimed with it and are its family, as an analyte's
isotopologues are the analyte's. A line is one peak your instrument resolves: on a TOF,
the peak one mass unit above the ion is its 13C, 15N, 2H and 17O lines together, claimed
as one line at their combined height. The lines fold under the ion's monoisotopic row,
counted in its **+N** marker, and the inspector lists them in the ion's isotopologue
table against the abundances its isotope pattern predicts. A fragment's lines fold under
the fragment the same way. The *reagent* count above the ledger still counts every peak
the source accounts for, the lines included. If you assign a compound to an ion's peak by
hand, its lines are unassigned with it, as an analyte's isotopologues are when their
compound is replaced. A run made before Mascope linked the lines to their ion lists them
as rows of their own and writes the ion without its charge; running the assignment again
links them.

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
the lines of your target library that it matched. That takes at least eight lines, of
at least eight different ions. A bright ion can match many lines alone, but they all
share its own mass error: counted as lines, a few such ions would put the sample's
centre at their own error and its width at how well their lines agree. Below either
count, the width is the instrument class's, and the centre is the median mass error of
the reagent ions the run claimed, if it claimed at least three and their offset exceeds
that width; otherwise the sample is scored on its nominal masses. The reagent ions
stand in only there: they sit at the low end of the mass range and need not agree with the
offset your library measures. A formula measured on its own - a batch peak's *Use
this*, the inspector's measured evidence and its alternatives - is held to the same
counts, and holds too few ions to measure either. It is scored at the instrument
class's width and at the centre the sample's latest run in Mascope scored at, whether
that was your library's offset or the reagent ions'. An imported run does not count. A
sample with no such run is centred on its own reagent ions, as a run would be. The
batch's *Search untargeted* (below) ranks the candidates it finds in a sample at that
same width and centre, as a run's own search ranks at the centre the run scores the
sample at. The formulas it chose are then held to the same counts, as a run's are: eight
or more ions in the sample measure a width and centre of their own, and fewer are
measured at the centre they were ranked at.

How closely a line's height must follow its predicted share is the run's own, too. By
default a clean line is held to 5% of its share, and a missing line counts against the
formula wherever its expected signal-to-noise - the main line's times the share - is at
least 3. On an Orbitrap that is too strict for a weak line. Each scan keeps a peak only
where it clears that scan's noise, and the file's peak list averages the scans, so a
faint 13C line reads low or is missing altogether. How much depends on the file and on
how it was acquired, its number of microscans among them. So each run measures it on
the envelopes its lists confirmed: for every range of expected signal-to-noise, how far
below its share a 13C line reads in that file, and how often it is there at all
([Barlow et al. 1972][ba72] for the monotone fit). A line then costs nothing anywhere
between that low reading and its full share, and a missing line counts only where that
file's lines are usually present. When the composition search tries a candidate
formula, a peak more than 40% off its line's share is taken for another compound's and
the line counts as missing. On a file with a measured response (its own, or the one
pooled from its batch), the lower end of that cut moves down to the file's low reading,
so a faint 13C line is found rather than counted against the formula. Above the share
the 40% cut stays. A file with too few confirmed lines takes the
measurement pooled with the other files of its batch acquired at the same settings, and
failing that the defaults. On a TOF the run measures one offset and one spread of how
its lines read against their shares, since a TOF's signal-to-noise column is not read
that way yet. The run records which of these it used, and a formula measured on its own
afterwards - *Use this*, the inspector's evidence - is read at the tolerance the
sample's latest run in Mascope recorded. A decoy run (below) is never that run, and its
lines are pooled for no other file. A sample with no such run, one only folded into
the batch ledger, is measured at the defaults, though its folded rows were scored at a
tolerance fitted on the spot.

An Orbitrap file whose peaks record the scans they are present in (*Scan Presence* on
[Peak Detection](peak-detection.md)) needs no such measurement for its lines: it
counts the loss instead. Its lines are read by the count, and the run's measurement
is what a file without the counts reads:

- **A line is read at its mean per scan**, against the main line read the same way. A
  scan keeps a line only where it clears about twice its noise, so the scans that keep
  a dim line are the ones its noise lifted, and its mean over them reads high: 1.4
  times its share where half the scans keep it. The fit score instead estimates the
  line's mean over every scan from how many scans kept it and how far above the noise
  they read it, the censored-normal estimate ([Tobin 1958][tob58];
  [Amemiya 1984][ame84]). A line in every scan reads as the peak list has it. The line
  is then held to its share at the default 5%, since the shortfall the measurement
  allows for is gone. A line some scans missed and that has no signal-to-noise keeps
  the measurement.
- The untargeted search's 40% window, which keeps a line only near its share, reads
  the share by the count too. Its lower end stays at 40% below the share and does not
  move down to the file's low reading, since the count has already taken that out.

On simulated files of 20 to 100 scans, the estimate reads a line at 0.99 to 1.00 of its
share, the median whatever share of the scans keeps it. On the demo raws, 13C lines
missing one or two of their main line's four scans read 0.77 of their share in the peak list, 1.08 over the scans that keep them, and 0.90 by
the estimate, where lines in every scan read 0.96. The 81Br line of the demo's bromine
ions reads within 0.2 of its natural share for 97.4% of 2,908 lines, from 96.6%; the 85
that read differently are closer to natural for 93% of them.

A missing line counts as on a file without the counts: from the signal-to-noise the run
measured its lines to be present at. The count could set that threshold too, from where
the scans holding the main line would have put the line in the peak list. As a yes-or-no
rule it charged lines the peak list holds only about half the time: the demo's lists hold
42% of the predicted 13C lines at an expected signal-to-noise of 4.2 to 5, the rule's
threshold on a file of four scans. It ranked the demo's true formulas a little lower, so
it is not used.

The estimate assumes a line's level is the same in every scan. Where a compound varies
within the file, the scans that keep its dim line are also the scans where there is most
of it, and the line reads high. A compound present in only part of the file is read as if
the scans without it were below the noise.

A predicted line is a peak your instrument can resolve, not a single isotopologue. On a
TOF, the peak one mass unit above a compound holds its 13C line and also its 15N, 2H and
17O lines, a few mDa apart. The untargeted search reads that peak against the sum of all
four, at the resolving power recorded for the file, and so does the check that reads a
peak as a neighbour's isotope line (*a neighbour's isotope line*, below). A line too
faint to predict on its own still counts when it sits less than one peak width from a
line that is predicted: it is added to that line, since one peak that wide holds both.
A file with no recorded resolving power is read one isotopologue per line.

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
  formula from at least one other, no second ionization channel of the run committed
  the same neutral, and no homologous series of the batch names it (*Detecting series*,
  below). A compound matched from a list is asked this too. When the run
  searches formulas, the search also looks at that compound's peak: the formulas its
  element ranges hold for the mass are scored beside the list's, and any the evidence
  cannot tell apart count as rivals, named in the reason. A radical does not count,
  since the run never holds one at *assigned*. A list's presence is evidence for a
  formula, not a label on it, so a list compound is only held at *assigned* where
  nothing plausible competes with it.
- **one peak only** — the formula search found the formula on one peak, and nothing
  else in the run saw it: no line of its own isotope pattern is committed beside it, no
  second ionization channel committed the same neutral, no homologous series of the
  batch names it, and it was not matched from a list. However well it fits, one line is one observation. A faint line matches its
  isotope pattern perfectly by having no other line to match, and on a TOF its mass
  error is judged against a width of several ppm, so the top tier asks for a second
  one: the isotope or adduct evidence [Schymanski et al. 2014][sch14] ask of a formula
  before calling it unequivocal. An isotope line whose mass error does not follow the
  row's (*off its M0*, below) does not count, since it is more likely another peak
  inside the matching window; a line *in doubt* does. A formula matched from your
  target library or a reference list is not asked: the list is its second
  observation.
- **expected line absent** — the formula's isotope pattern predicts a line the
  spectrum should show, and no peak is there. A line is expected where the fit
  score would count it missing (above): where its expected signal-to-noise, the main
  line's times the line's share, reaches the threshold the run measured for that
  file, or 3 where it measured none; on a peak with no signal-to-noise, where the line
  is at least 10% of the main line. It is looked for within the run's matching
  window, both about where the formula puts it and about where the main line's own
  mass error moves it. The fit only weighs a missing line by its share, so a bromide
  reading whose 81Br line, almost as tall as its main line, is missing could still
  reach the top band; isotope patterns are a constraint on a formula, not a
  decoration of it ([Kind & Fiehn 2007][kf07]; [Böcker et al. 2009][bo09]). Every
  committed formula is asked, one from your target library, a reference list or your
  own hand included, and a second channel does not lift the cap: it says the molecule
  is in the sample, and cannot put a missing line into the spectrum. A formula you
  assign by hand is asked when you assign it, at the threshold and window its run
  asked the run's own rows at; this is the one rule a hand assignment is asked, since
  the others weigh how the run chose between readings. A peak you assign by hand as
  one of a compound's isotopologues, such as its 81Br line, is not asked: the rule
  reads a formula's lines from its main one. On a run made before Mascope looked for
  missing lines, a hand assignment is not asked either. A line where the
  only peak is one the run set aside as an *artifact*, a side lobe of a bright
  neighbour, is not tested: an artifact neither confirms a line nor refutes it, and
  where a side lobe and another peak share the window, the other peak is read as the
  line. Neither is a line beyond the first or last peak of the spectrum, which the run
  takes as the ends of what it measured. On a TOF a bromine or chlorine reading must
  show its own M+2 line however faint the pattern, since its mass alone cannot tell
  the halogen. The isotopologue table lists the lines that were not found under the
  ones that were, and the spectrum draws them as empty circles at their predicted
  heights.
- **peak not separated** — another peak sits so close to this one that the instrument did
  not separate the two. Two lines of one width show as two peaks only where their sum has
  two maxima, and how far apart that takes depends on their heights: 0.85 widths at half
  maximum for two lines of equal height, 1.65 for a pair at 1:50, and further still for a
  small line on a tall one's flank ([Robertson & Fryer 1969][rf69];
  [Schilling et al. 2002][sw02]). Closer than that, the sum is one peak with a shoulder.
  The peak picker of a TOF fits several lines to one such peak and can report both, but
  then neither one's centroid nor its height is a single ion's own: the mass and the
  intensity the fit scored are a blend. The width is the one the file's resolving power
  gives at the peak's m/z, the width detection fitted the file's lines with, and a file
  the run read no resolving power for is not asked. Every other peak of the spectrum
  counts as a neighbour, a source ion or a peak nothing explains included, except a line
  of the row's own isotope pattern and a peak under 0.3 of this one's height. So the
  shorter peak of a pair is always asked, and the taller only beside a neighbour at least
  0.3 of its height. Two lines 0.85 widths apart blend into one maximum. Beside a line 0.95
  as tall, that maximum sits a quarter of a width off the taller line and 19% over its
  height; beside one half as tall, 0.07 widths and 8%; beside one at 0.3, 0.04 widths and
  4%. On the TOF spectra the rule was read on, a shoulder under 0.3 of the height moves the
  taller peak's maximum a median 1.5 ppm, about that peak's own mass error. One from 0.3 to
  half moves it a median 7 ppm, and for one pair in ten by the whole match window. A small
  mass error does not clear such a peak, since the formula may have followed the blend.
  Under 0.3, a shoulder on a TOF can also be the peak's own tail, which the picker's fit
  split off, and on an Orbitrap its ringing. The reason names the nearest neighbour that
  counts, how many widths away it is and how tall against this peak, and the inspector
  links it: click **neighbour at m/z** beside the reason to focus that peak. One thing
  lifts the cap: another ionization channel of the run committing the same neutral on a
  peak the instrument did separate, whose mass and height were measured, by the same test:
  a partner that is the taller peak of a lopsided pair lifts it. A partner the instrument
  did not separate either does not lift it, and neither does a list naming the compound or
  a homologous series of the batch, since the doubt is about the measurement and not about
  the formula. A hand assignment is not asked. On an Orbitrap, whose lines are a few ppm
  wide, the rule seldom fires.
- **a neighbour's isotope line** — a committed neighbouring compound's isotope pattern
  predicts a line on this peak, and the peak is no more than twice as tall as that line,
  so the line could account for all of it. The neighbour must itself be a reading the
  run stands behind, at *candidate* or above. A line is one peak of the file: on a TOF,
  the neighbour's 13C, 15N, 2H and 17O lines one mass unit up are one line, at their
  combined height.
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
  ionization mode treats as secondary with nothing naming its molecule: for the formula
  search's results, no second channel and no list behind it, however many isotope lines
  it has; for a list's reading through a channel the run opened, no isotope line and no
  second channel. The calibration check
  exempts only a line one of whose isotopologues tracks its mass error: your library's
  lines help measure the calibration, but a list names a compound rather than where each
  of its lines has to sit, so a line of it far off calibration with no isotopologue
  behind it is capped like any other. A formula from a loaded reference list is a prior
  matched against every sample rather than a list assembled for your data, and its
  matches play no part in measuring the sample's own mass accuracy. The source's own
  ions do: the monoisotopic lines the run set aside as *reagent* peaks (*The ions a
  source makes of itself*, above) are the best-known masses in the file, and they
  measure the calibration beside the run's corroborated assignments. On a
  charge-transfer source they are the mode's calibration compounds, and on a sample
  with few corroborated assignments they can be what lets it measure a calibration
  at all. A source's brightest lines can read slightly above its analytes, so where
  they are much of what the run measures on, the constant centre leans toward them.
  The calibration's centre can follow the mass range: where the run's assignments show their mass error changing with m/z the
  way a fixed offset in millidaltons does, growing in ppm as the mass falls, each ion is
  measured from the centre at its own m/z, so a small ion a couple of ppm out can sit
  exactly on calibration.

What a row that keeps its tier kept it on: **second channel** (the same neutral committed
through two or more of the run's ionization channels), **second line** (a line of its own
isotope pattern committed with it, named with its m/z), **in a series** (a homologous
series of the batch proposed the formula: *Detecting series*, below, which the reason
names with its step and the assigned compound the series starts at), **on a list** (matched from your
target library or a reference list, named with the list's compound; the formula search
did not elect it), **same ion, settled** (the ion
reads as another molecule too, and something settled which: a second channel - for a
reading through one of the mode's own channels, even where the sample shows the other
molecule too, short of ten times as brightly - the sample showing this molecule
decisively more strongly than the other (see *Auto* above), your target library, or the
other reading being a radical - the sentence says which; where the ion reads as several
molecules, it names one the sample shows before one it does not, with how brightly), **no close
rival** (the evidence separated the formula from every other candidate the run competed
for the peak; where the ion reads another way, it separated the ion from every other ion,
since two readings of one ion are one measurement), or **not measured** (nothing the pass
reads was recorded for the row, which is a statement of absence rather than a finding). An isotopologue **follows its M0**: it is the M0's ion
seen at another isotope, so it takes the M0's answer and loses the top tier with it, and
the inspector shows the M0's reasons beneath its own. What was found about its own line -
in doubt, off its M0, read as the M0's line - is listed above that.

The ledger lists each reading of a neutral as a row of its own, at its own m/z, so a
compound seen through two channels is two rows far apart in the table. **Group by
formula**, in the view menu behind the cog at the end of the tier-chip row, makes it one:
the neutral's strongest reading heads the row (the higher tier, then the better fit), and
the arrow and count beside its ionization open the other readings under it, each with its
own m/z, tier and verdict, and with its own isotopologues when those are unfolded. The
tier chips and the verdict filter apply to each reading before it is grouped, so a
filtered-out reading leaves the next one to head the row, and the counts above the ledger
still count readings. A reagent's ion or a peak nothing explained names no neutral of the
sample and is never grouped.

The inspector reads the same rows. A row whose neutral the run committed through more
than one channel carries a ***Supported by N channels*** badge, and clicking it opens
the neutral's other readings in this ledger, strongest first: each names its channel,
its m/z and its tier, and clicking one focuses its peak. A focused isotopologue opens its
M0's. The badge counts what the run recorded, and the links are the ledger's rows of
that neutral, so a ledger that holds none of them, such as one the batch serves, leaves
the badge as a count only. Below it, where the batch has read how the neutral's channels
follow each other across the batch, the inspector says so in one line
([reading co-variation](#reading-co-variation)).

A run keeps the peaks it read. When a sample's peaks are found again after a run - the
file re-ingested, the peaks detected again - the run's rows name peaks the sample no
longer has. They still show what the run stored, but there is no peak to focus: the
ledger dims them and says how many there are above the table, and the inspector lists
such a reading without a link. Run the assignment again to read the current peaks.

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
  column, beside the formula, folded to a flask with the list's tags beside it; hovering
  the flask gives the first name, its list, the tags and how many more names the run
  matched, so a row's list reading is there without opening the inspector. **Listed
  as, in full**, in the view menu, writes the name and the list out in the column, in
  this ledger and in the *Batch peaks* ledger both, and keeps the setting across a
  reload. A lead is not shown there - it is looked up when a row is inspected.
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

### Searching one peak

*Find more* searches the focused peak on its own: every formula of the grid whose ion,
under one of the chosen ionization mechanisms, puts a line within the m/z window of the
peak, each matched against the sample and scored as a run scores it. A peak with very
many candidates lists the 500 that fit it best; the count beside the peak still says
how many compositions the search found.

Which line of the ion the peak is decides what can be found. Read as the monoisotopic
line of every candidate, a peak that is another line of a compound's ion never finds
that compound: a dibromide shows its 79Br81Br line twice as bright as its monoisotopic
79Br2 line, and a peak one mass unit above a compound is its 13C line. So the search
reads the peak as any line of each candidate's ion at least 1% of the brightest: the 13C
line one unit above a compound, a 34S or 37Cl line two above, a 15N reagent's unlabelled
remainder one below. A labelled reagent's adduct is read at its labelled line, the one
the reagent makes most of - the 15N-nitrate adduct of a compound at its [15N] line.

A candidate found at another line than its monoisotopic one is tagged with that line
(`[13C]`, `[81Br]`, `[14N]`), its m/z error is against it, and assigning it by hand
records the peak as that isotopologue of the compound. Reading every line finds about
twice as many candidates as reading the monoisotopic line alone; most of the extra ones
are compounds whose monoisotopic line the spectrum does not hold, which the fit score
ranks low.

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

An identity that reads the peak as an isotope line of a compound, such as its 13C line,
cannot be pinned there. The line is part of the compound's envelope, not an ion of its
own. *Use this* on it says which batch peak holds the compound's monoisotopic line;
pin the compound on that batch peak instead.

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

### Naming the structure

A formula is not a compound. Many compounds share one formula, and a reference list's
name beside a peak is a candidate, not an identification. A confirmation can say which
compound it is: an **identification claim**. Once you pick an evidence level, the form
shows a **Structure** choice:

- the compounds the row's reference lists name for its formula, each with its list;
- **Unlisted**, last: an InChIKey of your own, with a name. Use it for a compound no
  loaded list names.

A list's key that is not an InChIKey's shape, such as `N/A` or a bare first block, is not
offered: the server would refuse it. Changing a verdict starts from the structure it
names, so a new note or evidence level keeps the claim unless you pick another.

Evidence from a reference standard, MS/MS or an orthogonal technique is a statement
about a structure, so confirming on it needs one. The form picks the first candidate for
you, and *Confirm* waits until a structure is chosen. On the isotope/adduct pattern or a
visual check naming one is optional, and none is picked; an empty *Unlisted* key there
names none.

The confirmation states an identification level, after [Schymanski et al. 2014][sch14]:

| Evidence | Structure | Level |
|---|---|---|
| Reference standard | required | 1: confirmed structure |
| MS/MS | required | 2: probable structure |
| Orthogonal | required | 3: tentative candidate |
| Isotope/adduct pattern | optional | 4: unequivocal formula |
| Visual | optional | none of its own |

The level is fixed when you confirm, so your statement reads the same later. A structure
named on a pattern or a visual check is kept, but it raises nothing: that evidence is
about the formula. A rejection or an unsure verdict states no level, and neither does a
visual confirmation; the row then reads the level the engine gives it. The verdict badge
shows the level and the structure's name; hover it for the evidence and the InChIKey.

A claim is part of its verdict. A later verdict on the same peak replaces it, and a
retracted batch-level verdict takes its claim off every sample it covered. Verdicts
recorded before claims existed keep their evidence level, name no structure and read as
level 4. Naming a structure changes nothing in the labelled record that confidence
calibration is fit on: a confirmation counts once, with or without one.

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

### The verdicts of a batch or dataset

The verdicts leave the app as one table for a whole batch or dataset: the *Verifications*
sheet of the batch data spreadsheet ([Export files](../reference/exports.md#verifications)),
and the SDK's `peak_assignments.verifications_for_batch` and `verifications_for_dataset`.
It has one row per judged peak, as each sample's assignment ledger shows it: the sample's
own current verdict, or the batch-level verdict it borrows, marked so. The sample's own
verdict wins before any filter applies, so asking for the confirmed verdicts never lists a
borrowed confirmation on a peak the sample itself rejected. Each row names the sample's
file and acquisition time, the peak's m/z, the evidence level, the structure a
confirmation names and the level it states and, on the sample's own verdict, the score it
was given when it was judged.

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

### Decoy runs

How well a run agrees with another engine, and how plausible its formulas look,
says nothing about how many rows it would still call *assigned* if the sample's
chemistry were wrong. A **decoy run** measures that. It is the engine run again
on a sample it has already assigned, its *control*, with one thing made wrong
on purpose, and what still reaches *assigned* there is a bound on what the
engine assigns falsely. This is the target-decoy idea of large-scale mass
spectrometry annotation ([Elias and Gygi 2007][eg07],
[Scheubert et al. 2017][sch17]), applied to the whole run; the reference
engine, peaky, checks each of its releases with the same arms.

A decoy run takes one of three arms:

- **A ppm shift** (`mz_shift_ppm`, 9 either way on an Orbitrap): every peak's
  m/z is scaled by a few ppm. The spectrum is the same, its lines sit in the
  same crowded mass-defect band, and no true formula is on them. A shift
  inside the window the control searched and matched in is refused, since the
  true formula would stay in reach: on a TOF, which searches 10 ppm and
  matches lines at 15, the arm is the next one.
- **A dalton shift** (`mz_shift_da`, 0.35 on a TOF): every peak keeps its
  nominal mass and loses its mass defect. Below about m/z 350 that puts the
  lines in the gap no CHNOS formula sits in.
- **Swapped adducts** (`swap_adducts`): each channel of the sample's
  ionization mode is searched through an adduct of the same polarity that the
  chemistry profile does not make (sodium, ammonium, potassium on a positive
  source; chloride, iodide, bromide on a negative one, in that order), and no
  secondary channel is opened. The ions are where they were and every neutral
  read off them is wrong, except where the swap reads an ion the control
  already reads as another neutral, which the comparison counts apart. The
  source's own lines are not swapped: the reagent pre-pass climbs the
  solvent's ladders through the mode's own channels, so the decoy is offered
  the peaks its control is.

Every stage of a decoy run reads the arm: the peak table, the targeted
matcher, the untargeted search, the mass gate and the tiering. And every stage
scores at the control's calibration, the centre and width the control
recorded, rather than measuring its own: fitted on rows that are wrong on
purpose, the run would measure the shift back out of the spectrum, or fail to
calibrate and judge with its mass gate off. A line's height is read at the
control's envelope tolerance for the same reason: the few envelopes a decoy
confirms are wrong formulas, and too few to measure one of its own. The source's own lines are still
claimed as reagent where a ppm shift moves them, since the claim finds them by
their own pattern; a dalton shift moves them out of its reach, and the stages
are then offered them as any peak, which the comparison counts apart.

A decoy is launched through the API, as an assign request whose config carries
a `decoy` block and every other setting as the control's: no setting may
differ, or the arm would bound another run. A decoy the sample's latest
completed run cannot be the control of is refused before any run exists, with
the reason. A decoy run is never read as the sample's result: the run selector
and the runs listing leave it out unless asked (`include_decoys`), a ledger
read that names no run reads the control, the batch fold and its consensus
never see it, its rows take no verdict, and the confidence calibration never
reads one. `tooling/assignment_compare/compare_runs.py --decoy` reads a set's
decoy runs against their controls and reports, per arm, the share of the
control's assigned rows and of their intensity the arm still holds, below and
above m/z 350.

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

Under the lines the run found, the table lists the lines it expected and did not
(*expected line absent*, above): each at its predicted m/z and abundance, reading
**not found**, or **not tested** where only an artifact's peak sat there or the line
fell beyond the spectrum. The spectrum draws the same lines as empty circles at their
predicted heights, beside the theoretical sticks of the lines that were found. A run
made before Mascope looked for missing lines lists none.

--8<-- "_help/assignment-status.md"

The badge sits in a column of its own, after the sample name by default; the
table-controls cog moves or hides it like any other column.

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
A batch peak that one sample's search reads as an isotope line of another batch peak's
composition reads as that line, as it would within one sample. That holds even where the
line is brightest in a sample of its own, whose search read it as a compound: the line
reading wins, and that sample is searched again without the peak.
It writes no per-sample runs: the results appear in the batch ledger and in each
sample's view, marked as untargeted. An assignment run on a sample still takes
precedence for that sample.

### Detecting series

A batch that holds a compound at a confident reading often holds its homologues too: the
same skeleton one CH2 longer, one oxygen richer, one CO2 further on - a homologous
series, the rows of constant mass defect on the Kendrick scale
[Kendrick 1963][ken63], [Hughey et al. 2001][hug01]. Each homologue makes a peak of its
own, and often a dim one, which the formula search alone either reads some other way or
holds at *candidate* because one peak is all that saw it.

*Detect series*, beside *Search untargeted*, walks those series. From every batch peak the
batch holds at *assigned* it steps one unit up and one down - CH2, O, H2O, CO, CO2 and
C2H2O, and the siloxane (C2H6OSi) and CF2 ladders from a compound that carries silicon
or fluorine - and wherever a batch peak nothing assigned, or one held only at
*candidate*, sits at the exact mass of the step's formula through the same ionization,
it proposes that formula and steps on from it, up to three steps from the assigned
compound. The window is the formula search's own: 3 ppm on an Orbitrap, 10 ppm on a TOF.
A batch peak that reads *assigned* because a series proposed its formula is a member of
that series, not a new start, so detecting series again on an unchanged ledger finds the
same series and changes nothing. A detection that stops partway, for example because the
server restarted, leaves the series on the batch peaks it had measured, so detecting series
again finds the same series.

A dense batch holds peaks one unit apart by chance alone, so every kind of step is walked
again at six masses shifted by a few hundredths of a dalton, which land on no chemistry
and so reach only what chance reaches - the target-decoy idea of proteomics
[Elias and Gygi 2007][eg07]. A kind of step is kept only where it proposes at least 12
formulas and at least ten times as many as its decoys reach on average; the share of
its proposals chance would have made, the decoys' mean over its own count, is the run's
false discovery rate. The thresholds were measured on twenty batches of real data: from
ten times, no decoy walked as if it were a unit passed, and no family on a TOF did,
where in the TOF's wider window only about half of the proposals that land on a
candidate agree with its formula.

A kept proposal is measured, not assigned: it is scored in every sample its batch peak
is in, as the untargeted search measures what it finds, and a sample whose peak the
formula's isotope pattern does not reach keeps the peak as it was. Where a sample
already reads the peak another way, the series' formula takes it only where it measures
at least as well. A batch peak already held at *candidate* on the proposed formula is
not measured again; the series is recorded beside it. A row a series vouches for shows
a stair mark beside its formula, whose tooltip names the step and the assigned compound
the series starts at, and a sample's next assignment run counts the series as a second
observation of that formula (*in a series*, above), which lifts it past *one peak only*
and *rivals left standing* and nothing else. On a source with a 15N-labelled reagent a
formula carrying C8S or C2F3 is not proposed: its labelled alias makes the same ion and
takes the same steps. *Detect series* runs as a batch run, its summary listing every kind
of step against its decoys and the batch's false discovery rate; it writes no per-sample
runs. When the chemistry of an ionization mode cannot be resolved, for example because a
sample's file has moved, that mode is not walked, the run says so, and its batch peaks
keep the series an earlier detection found.

### Reading co-variation

--8<-- "_help/batch-co-variation.md"

A compound the batch sees through two channels is two batch peaks at two masses, and each
is a trace across the batch's spectra: its height in every sample, or its area on a TOF.
Where both readings are one molecule, the two traces rise and fall with that molecule's
abundance. Where a formula coincidence put two different compounds under one neutral,
they need not. *Read co-variation*, beside *Detect series*, reads every such pair, per
ionization mode and instrument type:

- **In log intensity.** A source state that favours one channel over the other
  multiplies that channel's signal by a factor. In log space a constant factor shifts the
  whole trace and leaves the correlation as it was.
- **Divided by the reagent signal.** Each spectrum's values are divided by the summed
  intensity of the batch peaks the reagent pass claimed, which is how chemical-ionization
  measurements are normalised [Lee et al. 2014][lee14]. A ledger whose batch peaks hold no
  reagent ion is read without it, and the tooltip says so.
- **With the batch's common mode taken out.** Much of what moves one peak moves all of
  them: the source's sensitivity, the sample flow, a dilution, the day's cycle. Two
  unrelated compounds that both follow it correlate. So in every spectrum the median of
  all persistent peaks, each taken from its own median, is subtracted first: one sweep of
  Tukey's median polish [Tukey 1977][tuk77]. A persistent peak is one present in at least
  half the spectra, and a spectrum with fewer than five of them is left out.
- **Correlated where both are present.** Pearson's r [Pearson 1895][pea95] over the
  spectra both peaks are in, with that count. At 0.6 or more the two channels co-vary,
  below they do not.

Two readings are withheld as *untestable*. With fewer than ten spectra in common, r says
nothing: on twenty batches of real data, more than a quarter of the pairs chance puts
together read as co-varying there. And where one of the two traces spreads by less than
0.02 in log10, about 5 %, it holds nothing to follow, and r against it is its noise's.
Most single sets of a few samples are untestable throughout: co-variation needs a batch.

The reading is shown, never weighed. No tier and no reason reads it until a benchmark
against a null model has measured what it is worth as evidence. A pair running against
each other demotes nothing either: a correct composition can be anti-correlated with its
own parent channel when the source state moves, as an acid read as its radical anion
beside its deprotonated ion is over a multi-day batch. On the same twenty batches, two
channels of one compound read as co-varying about two and a half times as often as two
unrelated peaks of the same prevalence do, which is a reason to look and not yet a
reason to change a tier.

Each batch peak of a pair records the reading, and the ledger shows it in a
**Co-variation** column, which appears once a reading has run: the strongest partner's r,
or *untestable*, with every partner channel on hover. The column sorts by r, the
untestable rows last. The inspector shows the same reading on a sample's peak where the
sample reads the peak as the batch does: *Co-varies with [M+NO3]- across the batch*,
with r. A batch peak keeps its reading for as long as its consensus names the formula
and the ionization it was read for; a new sample's vote, a curation or a rebuild that
moves it takes the reading off, and the next *Read co-variation* replaces every reading.
It runs as a batch run, whose summary counts each verdict per ionization mode and
instrument type and names the thresholds; it changes no tier, no member and no
per-sample run.

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

A batch that holds files of both instrument types keeps a ledger of each. An
Orbitrap's intensities are peak heights and a TOF's are peak areas, so the two
cannot share a trace: each instrument type's samples fold into batch peaks of their
own, even at an m/z the other type also saw. The ledger then shows a picker above the
tier chips, **Orbitrap** or **TOF**, and lists, counts and selects one type's batch
peaks at a time; the batch chart draws only that type's selection. Switching type
clears the other type's rows from the selection. The chart icon on a sample ledger row
of the other type switches the ledger to that type and says so. A batch of one
instrument type shows no picker. A batch folded before the ledger kept the two apart
holds mixed batch peaks until *Rebuild batch ledger* folds its samples again, which
separates them. This is the rule batch alignment keeps too: it
refuses a batch of two instrument types, since one alignment has one intensity unit.
The ledger route and the SDK's `batch_peaks.list` name each batch peak's type in
`instrument_type` (`orbi` or `tof`). A verdict or a pin is on one batch peak, so on
such a batch it covers one instrument type's samples: the Orbitrap and the TOF row of
one species each take their own.

The *Verdict* column, last in the ledger, records and shows a
[batch-level verdict](#batch-level-verdicts) on the species: one judgment that covers
every sample of the batch peak's instrument type without a verdict of its own.

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
consensus formula's. It is folded to a flask and the list's tags, the name on hover,
as in a sample's ledger, and **Listed as, in full** in the view menu writes it out. A batch ledger folded before this column existed shows it once its
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
table as an earlier run left it). A sample folded in again or a batch peak curated by hand
changes the live ledger without making a run of its own. The current run's
`ledger_utc_modified` says when its ledger was last written, so a client that keeps a copy
of the ledger, such as `mascope_viz`'s campaign store, can tell it is out of date without
reading the series again.

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

A row can also carry the engine's own verdict on its identity: `engine_tier`, in the
tier vocabulary of the per-sample import, and `engine_claim`, free text such as an
evidence level. Mascope keeps both on the batch peak beside its own consensus tier,
never in its place: the consensus tier is still measured from the members, and it is
the only tier the batch ledger sorts on and the fold reads. The verdict is shown only
where the consensus names the identity the engine gave it for. A batch peak whose
members did not take up the engine's formula shows none, and a later fold, curation or
rebuild that moves the consensus to another formula takes it off. Each import replaces
the verdicts of the one before, which stay readable under that import in the run
selector. The run's summary counts the verdicts shown (`engine_verdicts`) and those
held back because the consensus names another identity (`engine_verdicts_withheld`).

After such an import the *Batch peaks* ledger has an **Engine tier** column beside
**Tier**, and its view-options menu offers two switches. **Engine disagrees** keeps
the batch peaks whose engine tier differs from the consensus tier. **Engine tier**
makes the tier chips count and filter by the engine's tier, with a **not stated** chip
for the formulas it gave no tier; the **Tier** column, its own filter and the sort
stay on the consensus. Both are off by default. The sample ledger offers
the same **Engine tier** switch on a run imported with engine tiers. The API and the
SDK filter the same way (`engine_tier` and `tier_disagrees` on
`GET /api/batch-peaks/batch/{id}`, and on `mascope.batch_peaks.list`). A batch peak
with no engine tier is left out of either answer, because absence is not agreement.

A row names its adduct by this server's mechanism id. The mechanism table
(`mascope.ionization.list()`) is a catalogue of the adducts a deployment can name,
not a list of channels an engine should search: Mascope's own engine opens channels
by the ionization profile and the file's fingerprint. Send the id of the adduct the
engine found, and leave the rest of the table alone.

## References

- <a id="ken63"></a>Kendrick, E. *A mass scale based on CH2 = 14.0000 for high
  resolution mass spectrometry of organic compounds.* Anal. Chem. 1963, 35(13):2146–2154.
  [link](https://doi.org/10.1021/ac60206a048)
- <a id="hug01"></a>Hughey, C. A.; Hendrickson, C. L.; Rodgers, R. P.; Marshall, A. G.;
  Qian, K. *Kendrick mass defect spectrum: a compact visual analysis for
  ultrahigh-resolution broadband mass spectra.* Anal. Chem. 2001, 73(19):4676–4681.
  [link](https://doi.org/10.1021/ac010560w)
- <a id="pea95"></a>Pearson, K. *Note on regression and inheritance in the case of two
  parents.* Proc. R. Soc. Lond. 1895, 58:240–242.
  [link](https://doi.org/10.1098/rspl.1895.0041)
- <a id="tuk77"></a>Tukey, J. W. *Exploratory Data Analysis.* Addison-Wesley, 1977.
- <a id="lee14"></a>Lee, B. H.; Lopez-Hilfiker, F. D.; Mohr, C.; Kurtén, T.; Worsnop, D. R.;
  Thornton, J. A. *An iodide-adduct high-resolution time-of-flight chemical-ionization mass
  spectrometer: application to atmospheric inorganic and organic compounds.* Environ. Sci.
  Technol. 2014, 48(11):6309–6317. [link](https://doi.org/10.1021/es500362a)
- <a id="kf06"></a>Kind, T.; Fiehn, O. *Metabolomic database annotations via query of
  elemental compositions: mass accuracy is insufficient even at less than 1 ppm.* BMC
  Bioinformatics 2006, 7:234.
  [link](https://bmcbioinformatics.biomedcentral.com/articles/10.1186/1471-2105-7-234)
- <a id="kf07"></a>Kind, T.; Fiehn, O. *Seven Golden Rules for heuristic filtering of
  molecular formulas obtained by accurate mass spectrometry.* BMC Bioinformatics 2007,
  8:105. [link](https://bmcbioinformatics.biomedcentral.com/articles/10.1186/1471-2105-8-105)
  ([open access](https://pmc.ncbi.nlm.nih.gov/articles/PMC1851972/))
- <a id="rf69"></a>Robertson, C. A.; Fryer, J. G. *Some descriptive properties of normal
  mixtures.* Skandinavisk Aktuarietidskrift 1969, 52:137–146.
- <a id="sw02"></a>Schilling, M. F.; Watkins, A. E.; Watkins, W. *Is human height bimodal?*
  The American Statistician 2002, 56(3):223–229.
  [link](https://doi.org/10.1198/00031300265)
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
- <a id="eg07"></a>Elias, J. E.; Gygi, S. P. *Target-decoy search strategy for increased
  confidence in large-scale protein identifications by mass spectrometry.* Nature
  Methods 2007, 4(3):207–214. [link](https://doi.org/10.1038/nmeth1019)
- <a id="sch17"></a>Scheubert, K. et al. *Significance estimation for large-scale
  metabolomics annotations by spectral matching.* Nature Communications 2017, 8:1494.
  [link](https://www.nature.com/articles/s41467-017-01318-5)
- <a id="ba72"></a>Barlow, R. E.; Bartholomew, D. J.; Bremner, J. M.; Brunk, H. D.
  *Statistical inference under order restrictions: the theory and application of
  isotonic regression.* Wiley, 1972.
- <a id="ame84"></a>Amemiya, T. *Tobit models: a survey.* Journal of Econometrics 24,
  3-61, 1984 (the censored normal's estimation).
- <a id="tob58"></a>Tobin, J. *Estimation of relationships for limited dependent
  variables.* Econometrica 26, 24-36, 1958 (the censored normal's likelihood).
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
  Spectrometry Data Center, and its ion clustering data.
  [link](https://doi.org/10.18434/T4D303)
- <a id="gri73"></a>Grimsrud, E. P.; Kebarle, P. *Gas phase ion equilibria studies of the
  hydrogen ion by methanol, dimethyl ether, and water. Effect of hydrogen bonding.* J. Am.
  Chem. Soc. 1973, 95:7939–7943. [link](https://doi.org/10.1021/ja00805a002)
- <a id="lar82"></a>Larson, J. W.; McMahon, T. B. *Formation, thermochemistry, and
  relative stabilities of proton-bound dimers of oxygen n-donor bases from ion cyclotron
  resonance solvent-exchange equilibrium measurements.* J. Am. Chem. Soc. 1982,
  104:6255–6261. [link](https://doi.org/10.1021/ja00387a016)
- <a id="mot84"></a>Meot-Ner (Mautner), M. *The ionic hydrogen bond and ion solvation. 1.
  NH+...O, NH+...N, and OH+...O bonds. Correlations with proton affinity. Deviations due to
  structural effects.* J. Am. Chem. Soc. 1984, 106:1257–1264.
  [link](https://doi.org/10.1021/ja00317a015)
- <a id="sun88"></a>Sunner, J.; Nicol, G.; Kebarle, P. *Factors determining relative
  sensitivity of analytes in positive mode atmospheric pressure ionization mass
  spectrometry.* Anal. Chem. 1988, 60:1300–1307.
  [link](https://doi.org/10.1021/ac00164a012)
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
[rf69]: #rf69
[sw02]: #sw02
[bo09]: #bo09
[du19]: #du19
[eg07]: #eg07
[ken63]: #ken63
[hug01]: #hug01
[sch17]: #sch17
[sch14]: #sch14
[sum07]: #sum07
[hy15]: #hy15
[wa21]: #wa21
[platt]: #platt
[ba72]: #ba72
[ame84]: #ame84
[tob58]: #tob58
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
[gri73]: #gri73
[lar82]: #lar82
[mot84]: #mot84
[sun88]: #sun88
[wan03]: #wan03
[scn03]: #scn03
[mat17]: #mat17
[tan03]: #tan03
[kar18]: #kar18
[ish26]: #ish26
[sch03]: #sch03
[ver08]: #ver08
[ber11]: #ber11
[pea95]: #pea95
[tuk77]: #tuk77
[lee14]: #lee14
