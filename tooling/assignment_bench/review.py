"""``bench review``: the engines' top disagreements as verdicts to give (step 4.3).

The confidence curve learns from verdicts, and a verdict teaches it most where
the engines disagree. On the bench both engines have read every sample: the
in-app run, and the reference's run published beside it. Each row of the
reference's run carries the tier the reference gave it (``engine_tier``) and
the tier this server reads off the same row (``tier``); where the two differ
(the ledger's ``tier_disagrees``) the in-app run's row on the same peak is a
row worth a verdict.

``sheet`` reads the bench database (read-only) and writes, per set, the
brightest such rows to a review sheet under the bench's home: the peak, the
in-app reading and the reference's, and empty columns for the verdict, its
evidence level, what that evidence showed, the claim and a note. It leaves out a row that already has a live
verdict on its identity, and it never shows the evidence or P(correct): the
verdict is about the data, not the number (guardrail 5 of
``docs/dev/verification_calibration_loop.md``). A person fills it in, looking
at each peak in the app.

``record`` starts the bench's backend, posts every filled row as a verdict on
the in-app row through the verify route (signed in as the bench's account),
and with ``recalibrate`` refits the confidence curve of each instrument class
the recorded rows hold, once per class, through the recalibration route,
printing its ECE before and after. The app keeps one curve per class, so the
refit asks for no profile: the route fits every verdict of the class, and the
curve is a profile's where they all share one.
"""

from __future__ import annotations

import csv
import datetime
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


#: The sheet's columns, in order. The last seven are the reviewer's.
COLUMNS = (
    "set",
    "sample_item_id",
    "sample_peak_id",
    "mz",
    "intensity",
    "profile",
    "peak_assignment_id",
    "formula",
    "mechanism",
    "tier",
    "reference_formula",
    "reference_mechanism",
    "reference_engine_tier",
    "reference_tier",
    "verdict",
    "evidence_level",
    "evidence_detail",
    "independent_line",
    "claimed_inchikey",
    "claimed_name",
    "note",
)

VERDICTS = ("confirmed", "rejected", "unsure")
EVIDENCE_LEVELS = ("reference_standard", "msms", "orthogonal", "pattern", "visual")

#: What a confirmation's evidence showed, in each evidence level's words, and
#: the line beside a reference standard (``EVIDENCE_DETAILS`` and
#: ``INDEPENDENT_LINES`` in the backend's ``peak_assignments/claims.py``, whose
#: rules the verify route applies, at version 2 of the claims' mapping).
EVIDENCE_DETAILS = {
    "msms": ("library", "fragments", "loss"),
    "orthogonal": ("separation", "data_and_list", "class", "context_list", "homologue"),
    "pattern": ("split_pinned", "split_open"),
}
INDEPENDENT_LINES = ("msms", "separation", "instrument")

#: The evidence levels whose confirmation must say what it showed.
DETAIL_REQUIRED = ("msms", "orthogonal")

#: The details whose claim names a class (3b) by name alone, no InChIKey.
CLASS_DETAILS = ("loss", "class")

#: Rows per set a sheet holds by default.
TOP = 25

#: The recalibration route's instrument class for a bench set's.
INSTRUMENT_CLASS = {"orbitrap": "orbi", "tof": "tof"}

#: What an id put into the query's text may hold.
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ReviewError(RuntimeError):
    """A review step that cannot go on; the message says why."""


def _quoted(ids) -> str:
    out = []
    for value in ids:
        if not _ID.match(str(value)):
            raise ReviewError(f"refusing an id that is not one: {value!r}")
        out.append(f"'{value}'")
    return ", ".join(out)


def disagreements_sql(samples: list[str], reference_runs: list[str]) -> str:
    """The in-app rows on the peaks where the reference's tier and this server's differ.

    One row per peak: the in-app run of each sample is its latest completed
    one that is not a decoy, the row an analyte's monoisotopic reading with an
    evidence, and the peak one no live verdict judged under that reading.
    """
    return f"""
        with inapp as (
            select distinct on (r.sample_item_id)
                r.sample_item_id, r.peak_assignment_run_id,
                r.config -> 'resolved_profile' ->> 'profile' as profile
            from peak_assignment_run r
            where r.sample_item_id in ({_quoted(samples)})
              and r.engine = 'mascope' and r.decoy is null
              and r.status = 'completed'
            order by r.sample_item_id, r.peak_assignment_run_utc_created desc
        )
        select ref.sample_item_id, ref.sample_peak_id,
               ref.sample_peak_mz as mz, ref.sample_peak_intensity as intensity,
               i.profile, a.peak_assignment_id,
               a.assigned_formula as formula, am.ionization_mechanism as mechanism,
               a.tier, ref.assigned_formula as reference_formula,
               rm.ionization_mechanism as reference_mechanism,
               ref.engine_tier as reference_engine_tier, ref.tier as reference_tier
        from peak_assignment ref
        join inapp i on i.sample_item_id = ref.sample_item_id
        join peak_assignment a
          on a.peak_assignment_run_id = i.peak_assignment_run_id
         and a.sample_peak_id = ref.sample_peak_id
        left join ionization_mechanism am
          on am.ionization_mechanism_id = a.ionization_mechanism_id
        left join ionization_mechanism rm
          on rm.ionization_mechanism_id = ref.ionization_mechanism_id
        where ref.peak_assignment_run_id in ({_quoted(reference_runs)})
          and ref.engine_tier is not null and ref.engine_tier <> ref.tier
          and a.role = 'M0' and a.assigned_formula is not null
          and a.provenance ->> 'evidence' is not null
          and not exists (
              select 1 from assignment_verification v
              where v.superseded_utc is null
                and v.sample_item_id = a.sample_item_id
                and v.sample_peak_id = a.sample_peak_id
                and v.assigned_formula is not distinct from a.assigned_formula
                and v.ionization_mechanism_id is not distinct from a.ionization_mechanism_id
          )
    """


def top_rows(rows: list[dict], top: int) -> list[dict]:
    """The ``top`` brightest readings, one row per in-app reading, brightest first.

    A set's samples repeat one acquisition's ions, so its brightest
    disagreements are one ion sample after sample. A verdict judges a reading
    in one sample, and the curve learns more from ``top`` readings than from
    one reading ``top`` times: each formula and mechanism comes once, from the
    sample where it is brightest.
    """
    ordered = sorted(
        rows,
        key=lambda r: (
            -(r.get("intensity") or 0.0),
            r["sample_item_id"],
            r["sample_peak_id"],
        ),
    )
    chosen: dict[tuple, dict] = {}
    for row in ordered:
        reading = (row.get("formula"), row.get("mechanism"))
        if reading not in chosen:
            chosen[reading] = {
                **row,
                "mz": round(float(row["mz"]), 5) if row.get("mz") is not None else None,
                "intensity": (
                    round(float(row["intensity"]))
                    if row.get("intensity") is not None
                    else None
                ),
            }
        if len(chosen) == top:
            break
    return list(chosen.values())


def write_sheet(rows: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({**{c: "" for c in COLUMNS}, **row})
    return out.getvalue()


@dataclass
class Filled:
    """The rows of a sheet a reviewer filled in, and the ones it cannot post."""

    rows: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _claim_problem(verdict, level, inchikey, name, detail, line) -> str | None:
    """What the verify route would refuse of a filled row's claim, or None."""
    confirmed = verdict == "confirmed"
    if confirmed and level is None:
        return "a confirmation needs an evidence level"
    if line is not None:
        if not confirmed or level != "reference_standard":
            return "independent_line goes with a confirmation on reference_standard"
        if line not in INDEPENDENT_LINES:
            return (
                f"independent line {line!r} is none of {', '.join(INDEPENDENT_LINES)}"
            )
    if detail is not None:
        words = EVIDENCE_DETAILS.get(level)
        if not confirmed or words is None:
            return (
                "evidence_detail goes with a confirmation on msms, orthogonal "
                "or pattern"
            )
        if detail not in words:
            return (
                f"evidence detail {detail!r} on {level} is none of {', '.join(words)}"
            )
    if confirmed and level in DETAIL_REQUIRED and detail is None:
        return f"a confirmation on {level} says what it showed in evidence_detail"
    if inchikey is not None and not confirmed:
        return "only a confirmation names a structure"
    if confirmed and level in DETAIL_REQUIRED and detail in CLASS_DETAILS:
        if inchikey is not None:
            return "a class claim names its class in claimed_name, no claimed_inchikey"
        if name is None:
            return "a class claim names its class in claimed_name"
        return None
    if name is not None and inchikey is None:
        return "claimed_name goes with claimed_inchikey"
    names_structure = level == "reference_standard" or level in DETAIL_REQUIRED
    if confirmed and names_structure and inchikey is None:
        return f"a confirmation on {level} names its structure in claimed_inchikey"
    return None


def read_filled(text: str) -> Filled:
    """The rows with a verdict, each checked before anything is posted.

    The verify route's rules, applied first: a confirmation needs an evidence
    level, one on a reference standard, MS/MS or an orthogonal technique names
    its structure by InChIKey, only a confirmation names one, and a name goes
    with an InChIKey. A level or a verdict the route does not know is a
    problem, never guessed at.
    """
    filled = Filled()
    reader = csv.DictReader(io.StringIO(text))
    missing = [
        c
        for c in ("peak_assignment_id", "sample_item_id", "verdict")
        if c not in (reader.fieldnames or [])
    ]
    if missing:
        raise ReviewError(f"not a review sheet: no {', '.join(missing)} column")
    for line, row in enumerate(reader, start=2):
        verdict = (row.get("verdict") or "").strip().lower()
        if not verdict:
            continue
        level = (row.get("evidence_level") or "").strip().lower() or None
        if verdict not in VERDICTS:
            filled.problems.append(
                f"line {line}: verdict {verdict!r} is none of {', '.join(VERDICTS)}"
            )
            continue
        if level is not None and level not in EVIDENCE_LEVELS:
            filled.problems.append(
                f"line {line}: evidence level {level!r} is none of {', '.join(EVIDENCE_LEVELS)}"
            )
            continue
        inchikey = (row.get("claimed_inchikey") or "").strip() or None
        name = (row.get("claimed_name") or "").strip() or None
        detail = (row.get("evidence_detail") or "").strip().lower() or None
        independent = (row.get("independent_line") or "").strip().lower() or None
        problem = _claim_problem(verdict, level, inchikey, name, detail, independent)
        if problem:
            filled.problems.append(f"line {line}: {problem}")
            continue
        filled.rows.append(
            {
                **row,
                "verdict": verdict,
                "evidence_level": level,
                "evidence_detail": detail,
                "independent_line": independent,
                "claimed_inchikey": inchikey,
                "claimed_name": name,
                "line": line,
            }
        )
    return filled


def stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


@dataclass
class Review:
    """``sheet`` and ``record`` over one bench home and its instance."""

    reviews: Path
    instance: object
    query: Callable[[str], list[dict]]
    connect: Callable[[str], object]
    load_state: Callable[[], dict]
    write_private: Callable[[Path, str], None]
    prepare: Callable[[], None] | None = None
    say: Callable[[str], None] = print

    def sheet(self, sets: list[str] | None = None, top: int = TOP) -> Path:
        if top < 1:
            raise ReviewError(f"top must be 1 or more, not {top}")
        state = self.load_state()
        reference = state.get("reference") or {}
        if not reference.get("runs"):
            raise ReviewError(
                "the bench holds no reference runs: run `bench.py reference` first"
            )
        names = list(sets) if sets else list(state["sets"])
        unknown = [n for n in names if n not in state["sets"]]
        if unknown:
            raise ReviewError(
                f"the bench has no set {', '.join(unknown)}; it holds {', '.join(state['sets'])}"
            )
        rows: list[dict] = []
        for name in names:
            runs = {
                sample: entry["run_id"]
                for sample, entry in (reference["runs"].get(name) or {}).items()
                if entry.get("run_id")
            }
            if not runs:
                self.say(f"set {name}: no reference run, nothing to review")
                continue
            found = self.query(disagreements_sql(sorted(runs), sorted(runs.values())))
            chosen = top_rows(found, top)
            readings = {(r.get("formula"), r.get("mechanism")) for r in found}
            self.say(
                f"set {name}: {len(found)} peak(s) of {len(readings)} reading(s) where "
                f"the tiers disagree and no verdict is given; the {len(chosen)} "
                "brightest readings on the sheet"
            )
            rows += [{**row, "set": name} for row in chosen]
        self.reviews.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self.reviews / f"{stamp()}.csv"
        self.write_private(path, write_sheet(rows))
        self.say(
            f"wrote {len(rows)} row(s) to {path}: fill in verdict, evidence_level "
            "and note, then `bench.py review --record` it"
        )
        return path

    def record(self, sheet: Path, recalibrate: bool = False) -> int:
        filled = read_filled(sheet.read_text(encoding="utf-8"))
        for problem in filled.problems:
            self.say(f"refused: {problem}")
        if filled.problems:
            self.say("nothing posted: fix the sheet and record it again")
            return 1
        if not filled.rows:
            self.say("no row of the sheet holds a verdict: nothing to record")
            return 0
        state = self.load_state()
        classes = {
            name: INSTRUMENT_CLASS.get(
                held.get("instrument_class") or "orbitrap", "orbi"
            )
            for name, held in state["sets"].items()
        }
        self.instance.check_env()
        self.instance.bind()
        if self.prepare is not None:
            self.prepare()
        base = self.instance.start(peak_assignment=True)
        failed = 0
        try:
            api = self.connect(base)
            # One refit per instrument class: the app keeps one curve per
            # class, so a second refit of a class would replace the first.
            refits: set[str] = set()
            for row in filled.rows:
                payload = {
                    "peak_assignment_id": row["peak_assignment_id"],
                    "verdict": row["verdict"],
                    "evidence_level": row["evidence_level"],
                    "note": (row.get("note") or "").strip() or None,
                }
                # The claim's keys only where it names one.
                for key in (
                    "evidence_detail",
                    "independent_line",
                    "claimed_inchikey",
                    "claimed_name",
                ):
                    if row.get(key) is not None:
                        payload[key] = row[key]
                answer = api.post(
                    f"/api/peak-assignments/sample/{row['sample_item_id']}/verify",
                    payload,
                )
                if answer is None:
                    failed += 1
                    self.say(
                        f"line {row['line']}: refused: {api.errors[-1] if api.errors else 'no answer'}"
                    )
                    continue
                if row["verdict"] != "unsure":
                    refits.add(classes.get(row.get("set"), "orbi"))
            self.say(
                f"recorded {len(filled.rows) - failed} of {len(filled.rows)} verdict(s)"
            )
            if recalibrate:
                for inst in sorted(refits):
                    # No profile asked for: the route fits every verdict of
                    # the class, and the curve is a profile's only where they
                    # all share it.
                    answer = api.post(
                        f"/api/peak-assignments/calibration/{inst}/recalibrate", {}
                    )
                    if answer is None:
                        failed += 1
                        self.say(
                            f"recalibration of {inst} refused: "
                            f"{api.errors[-1] if api.errors else 'no answer'}"
                        )
                        continue
                    profile = answer.get("reagent_profile")
                    scope = (
                        f"profile {profile}"
                        if profile
                        else "pooled across reagent profiles, its adduct weights kept"
                    )
                    self.say(
                        f"recalibration of {inst} ({scope}): {answer.get('message')} "
                        f"(ECE {answer.get('before_ece')} -> {answer.get('after_ece')})"
                    )
        finally:
            self.instance.stop()
        return 1 if failed else 0
