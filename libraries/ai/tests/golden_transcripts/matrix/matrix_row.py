"""
One row of the model matrix: the golden questions through Goose and a model,
scored by the fixture's own checker.

The protocol is ``golden_transcripts/README.md`` and section 3.4 of
``docs/dev/ai_analyst_shell.md``; this driver makes a run of it reproducible
from one command, as the in-house open-model bench's launcher did (its
pattern: keys from an env file never printed, a dry run of the scorer before
any model call, one folder per run with one record per episode, provider
errors counted apart as infra, the request's provider pin recorded)::

    python matrix_row.py dryrun
    python matrix_row.py run --provider nebius --model nvidia/Nemotron-3.5-Super-VL
    python matrix_row.py run --provider openrouter --model nvidia/nemotron-3.5-super-vl \\
        --pin primeintellect/nvfp4 --reps 3
    python matrix_row.py summarize ~/mascope-bench/runs/<stamp>

A run folder (``--runs-root``, default ``~/mascope-bench/runs``) holds:

- ``header.json``: the bundle, the demo stack's build tag, the toolkit's
  commit, the runtime and its version, the provider, the model id, the
  precision the provider reports, the reasoning setting, the pin, the
  sampling, the step cap, the repetitions, and the dry run's table;
- ``PREREG.sha256``: the hashes of the fixture, this driver, the recipe and
  the note, written before the first model call;
- ``transcripts/<question>-rep<n>.json`` and ``.md``: Goose's session export,
  every tool call and result as the model saw them;
- ``results.jsonl``: one record per episode: question, repetition, the
  model's answer, the checker's verdict and problems, tool calls, seconds,
  the infra flag;
- ``summary.md``: the matrix row: correct / wrong / missing per class, infra
  apart. ``sloppy`` is a reviewer's verdict, added by hand to the transcript.

Keys come from ``~/mascope-bench/.env`` (``OPENROUTER_API_KEY``,
``NEBIUS_API_KEY``; mode 600) and are never printed. The Mascope token is an
``ai-agent`` token minted over the demo login for the run and regenerated at
its end, so it dies with the run. Only the published demo dataset is in a
transcript, never a token.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))  # the fixture, libraries/ai/tests
import ai_golden  # noqa: E402


ROOT = HERE.parents[4]
RECIPE = HERE / "recipe.yaml"
NOTE = ROOT / "docs" / "dev" / "ai_analyst_shell.md"
#: What the pre-registration hashes: the checker, the driver, the recipe, the note.
PREREG_FILES = (
    HERE.parents[1] / "ai_golden.py",
    Path(__file__).resolve(),
    RECIPE,
    NOTE,
)

ENV_FILE = Path(
    os.environ.get("MASCOPE_BENCH_ENV", "~/mascope-bench/.env")
).expanduser()
RUNS_ROOT = Path(
    os.environ.get("MASCOPE_BENCH_RUNS", "~/mascope-bench/runs")
).expanduser()

PROVIDERS = {
    # name: (Goose provider, key variable, base host for OPENAI_HOST, models route)
    "nebius": (
        "openai",
        "NEBIUS_API_KEY",
        "https://api.tokenfactory.nebius.com",
        "/v1/models?verbose=true",
    ),
    "openrouter": (
        "openrouter",
        "OPENROUTER_API_KEY",
        "https://openrouter.ai/api",
        "/v1/models",
    ),
}

#: The sampling the bench ran every model with.
SAMPLING = {"temperature": 1.0, "top_p": 0.95, "max_tokens": 32768}

INFRA = re.compile(
    r"(429|rate limit|timed? ?out|502|503|504|connection (error|refused|reset)|ECONNRESET)",
    re.I,
)


# --- helpers -----------------------------------------------------------------------


def _env_file() -> dict[str, str]:
    if not ENV_FILE.is_file():
        sys.exit(
            f"no key file at {ENV_FILE}: create it (mode 600) with the provider keys"
        )
    mode = ENV_FILE.stat().st_mode & 0o777
    if mode & 0o077:
        sys.exit(f"{ENV_FILE} is mode {mode:o}; make it 600")
    values = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("'\"")
    return values


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()


def _http(method: str, url: str, **kwargs: Any) -> Any:
    import requests

    response = requests.request(method, url, timeout=60, **kwargs)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return None


def _mint_token(url: str, email: str, password: str) -> tuple[str, Any]:
    """An ``ai-agent`` token of the demo user, over a session: the one a
    deployed agent connects with. Regenerating it again at the end of the run
    invalidates this one."""
    import requests

    session = requests.Session()
    session.post(
        f"{url}/api/auth/login",
        data={"username": email, "password": password},
        timeout=30,
    ).raise_for_status()
    minted = session.post(
        f"{url}/api/auth/access_token/regenerate",
        json={"service_name": "ai-agent"},
        timeout=30,
    )
    minted.raise_for_status()
    return minted.json()["access_token"], session


def _stack_header(url: str, token: str) -> dict[str, Any]:
    """What the demo stack says of itself: its version, the bundle."""
    headers = {"Authorization": f"Bearer {token}", "X-Service-Name": "ai-agent"}
    out: dict[str, Any] = {"url": url}
    try:
        out["server"] = _http("GET", f"{url}/api/version", headers=headers)
    except Exception as e:  # the route differs between releases
        out["server"] = f"unknown ({type(e).__name__})"
    return out


def _served_precision(provider: str, key: str, model: str) -> Any:
    """What the provider's model listing says of the model: Nebius reports the
    precision per model with ``verbose=true``; OpenRouter lists the endpoints."""
    goose_provider, _, host, route = PROVIDERS[provider]
    try:
        listing = _http("GET", host + route, headers={"Authorization": f"Bearer {key}"})
    except Exception as e:
        return f"not read ({type(e).__name__})"
    rows = listing.get("data", listing) if isinstance(listing, dict) else listing
    hits = [
        row
        for row in rows
        if isinstance(row, dict) and model.lower() in str(row.get("id", "")).lower()
    ]
    return hits[:3] if hits else "model not in the listing"


def answer_shape(question: ai_golden.Question) -> str:
    """The known answer's JSON shape with its values blanked: what the
    prompt gives the model, as the bench gave each task its submit shape."""

    def blank(value: Any) -> Any:
        if isinstance(value, ai_golden.Close):
            return "<number>"
        if isinstance(value, ai_golden.Instant):
            return "<timestamp>"
        if isinstance(value, ai_golden.Words):
            return "<why it is not available>"
        if isinstance(value, bool):
            return "<true/false>"
        if isinstance(value, (int, float)):
            return "<number>"
        if isinstance(value, str) or value is None:
            return "<text or null>"
        if isinstance(value, dict):
            return {key: blank(inner) for key, inner in value.items()}
        if isinstance(value, (list, tuple)):
            return (
                [blank(value[0])] + (["..."] if len(value) > 1 else []) if value else []
            )
        return value

    shaped = blank(question.expected)
    if question.category == "decline":
        shaped = {
            "available": "<true/false>",
            "reason": "<why, when not available>",
            "value": "<the value, when available>",
        }
    return json.dumps(shaped)


def _text(output: Any) -> str:
    """A subprocess's output as text: bytes decoded, None nothing."""
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return str(output)


def _goose_env(
    provider: str,
    model: str,
    pin: str | None,
    keys: dict[str, str],
    mascope: dict[str, str],
) -> dict[str, str]:
    goose_provider, key_var, host, _ = PROVIDERS[provider]
    if not keys.get(key_var):
        sys.exit(f"{key_var} is empty in {ENV_FILE}")
    env = {
        **os.environ,
        "GOOSE_DISABLE_KEYRING": "1",
        "GOOSE_PROVIDER": goose_provider,
        "GOOSE_MODEL": model,
        **mascope,
    }
    if provider == "nebius":
        env["OPENAI_API_KEY"] = keys[key_var]
        env["OPENAI_HOST"] = host
    else:
        env["OPENROUTER_API_KEY"] = keys[key_var]
        extra: dict[str, Any] = {"reasoning": {"enabled": True}, **SAMPLING}
        if pin:
            route, _, quant = pin.partition("/")
            extra["provider"] = {
                "order": [route],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
            if quant:
                extra["provider"]["quantizations"] = [quant]
        env["OPENROUTER_PARAMETERS"] = json.dumps(extra)
    return env


def _goose_version(env: dict[str, str]) -> str:
    try:
        return subprocess.run(
            ["goose", "--version"], capture_output=True, text=True, env=env, timeout=30
        ).stdout.strip()
    except Exception as e:
        sys.exit(f"goose is not runnable: {e}")


def _goose_output(stdout: str) -> dict[str, Any] | None:
    """Goose's ``--output-format json`` document: the messages and the
    metadata, found past the banner it prints first."""
    start = stdout.find("{")
    while start != -1:
        try:
            doc = json.loads(stdout[start:])
        except ValueError:
            start = stdout.find("{", start + 1)
            continue
        return doc if isinstance(doc, dict) and "messages" in doc else None
    return None


def _as_json(value: Any) -> Any:
    """*value*, decoded once when it is a JSON text: a model that writes its
    answer as a string holding JSON answered the JSON."""
    if isinstance(value, str):
        text = value.strip()
        if (
            text[:1] in "[{"
            or text in ("null", "true", "false")
            or _NUMBER.fullmatch(text)
        ):
            try:
                return json.loads(text)
            except ValueError:
                return value
    return value


_NUMBER = re.compile(r"-?\d+(\.\d+)?([eE][-+]?\d+)?")


def _parse_answer(stdout: str) -> tuple[Any, dict[str, Any]]:
    """The model's answer, and what else the episode's output says.

    The answer is the ``answer`` of the recipe's final output (the
    ``recipe__final_output`` call, or the last assistant text that is the
    same JSON object); None when the model gave none. The rest: the tool
    calls made, by tool, and Goose's token counts.
    """
    doc = _goose_output(stdout)
    if doc is None:
        return None, {"tool_calls": 0, "tools": {}, "tokens": {}}
    answer = None
    tools: dict[str, int] = {}
    for message in doc.get("messages", ()):
        for part in message.get("content", ()):
            if part.get("type") == "toolRequest":
                call = (part.get("toolCall") or {}).get("value") or {}
                name = str(call.get("name", ""))
                if name == "recipe__final_output":
                    answer = _as_json((call.get("arguments") or {}).get("answer"))
                else:
                    tools[name] = tools.get(name, 0) + 1
            elif part.get("type") == "text" and message.get("role") == "assistant":
                try:
                    final = json.loads(part["text"])
                except (ValueError, TypeError):
                    continue
                if isinstance(final, dict) and "answer" in final:
                    answer = _as_json(final["answer"])
    tokens = {
        key: doc.get("metadata", {}).get(key)
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }
    return answer, {"tool_calls": sum(tools.values()), "tools": tools, "tokens": tokens}


def lenient(answer: Any) -> tuple[Any, bool]:
    """*answer* with every number written as text read as the number, and
    whether anything was read so.

    The fixture's checker holds a solver to the type it asked for; a model
    that writes 2 as "2" has the value right and the shape wrong. The bench
    scored such answers correct by reading the number, so the driver does
    the same before the checker, and records that it did: the reviewer's
    call whether that is sloppy.
    """
    coerced = False

    def walk(value: Any) -> Any:
        nonlocal coerced
        if isinstance(value, str) and _NUMBER.fullmatch(value.strip()):
            coerced = True
            number = float(value)
            return int(number) if number.is_integer() and "." not in value else number
        if isinstance(value, dict):
            return {key: walk(inner) for key, inner in value.items()}
        if isinstance(value, list):
            return [walk(inner) for inner in value]
        return value

    return walk(answer), coerced


def score(question: ai_golden.Question, raw: Any) -> dict[str, Any]:
    """The checker's verdict on a model's *raw* answer, read leniently."""
    answer, coerced = lenient(raw)
    return {
        "answer": answer,
        "answer_raw": raw,
        "coerced": coerced,
        "verdict": ai_golden.verdict_of(question, answer),
        "problems": [] if answer is None else ai_golden.check_answer(question, answer),
    }


def rescore(folder: Path) -> None:
    """Score a run's stored answers again with the current checker, keeping
    the first scoring as ``results.orig.jsonl`` (as the bench kept its), and
    rebuild the summary."""
    results = folder / "results.jsonl"
    original = folder / "results.orig.jsonl"
    if not original.exists():
        shutil.copy(results, original)
    questions = {q.id: q for q in ai_golden.QUESTIONS}
    records = []
    for line in results.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        raw = record.get("answer_raw", record.get("answer"))
        if record.get("verdict") != "infra":
            record.update(score(questions[record["question"]], raw))
        records.append(record)
    results.write_text(
        "".join(json.dumps(r, default=str) + "\n" for r in records), encoding="utf-8"
    )
    summarize(folder)


# --- the run -------------------------------------------------------------------


def dryrun_gate() -> str:
    """The checker's dry run: the run refuses to start while it fails."""
    runs = ai_golden.dryrun()
    table = ai_golden.dryrun_table(runs)
    if not all(run.ok for run in runs):
        print(table)
        sys.exit("the checker's dry run fails: fix it before any model runs")
    return table


def run(args: argparse.Namespace) -> int:
    keys = _env_file()
    table = dryrun_gate()
    questions = (
        [ai_golden.question(q.strip()) for q in args.only.split(",")]
        if args.only
        else list(ai_golden.QUESTIONS)
    )
    if args.pilot:
        questions, args.reps = questions[:1], 1

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^A-Za-z0-9.]+", "-", args.model).strip("-").lower()
    folder = RUNS_ROOT / f"{stamp}-{slug}-goose"
    (folder / "transcripts").mkdir(parents=True)

    token, session = _mint_token(args.url, args.email, args.password)
    mascope = {
        "MASCOPE_URL": args.url,
        "MASCOPE_ACCESS_TOKEN": token,
        "MASCOPE_WORKSPACE": args.workspace,
    }
    env = _goose_env(args.provider, args.model, args.pin, keys, mascope)
    goose_version = _goose_version(env)
    _, key_var, _, _ = PROVIDERS[args.provider]

    header = {
        "run": folder.name,
        "started": stamp,
        "bundle": ai_golden.BUNDLE,
        "stack": {**_stack_header(args.url, token), "build_tag": args.stack_tag},
        "toolkit_commit": _git("rev-parse", "HEAD"),
        "toolkit_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "runtime": {
            "name": "goose",
            "version": goose_version,
            "max_turns": args.max_turns,
            "recipe": RECIPE.name,
        },
        "provider": args.provider,
        "model": args.model,
        "weights": args.weights,
        "precision_served": _served_precision(args.provider, keys[key_var], args.model),
        "reasoning": "enabled in the request"
        if args.provider == "openrouter"
        else "provider default (not settable through the openai provider)",
        "pin": args.pin
        or ("none needed" if args.provider == "nebius" else "NONE: fallbacks on"),
        "sampling": SAMPLING
        if args.provider == "openrouter"
        else "provider default (the openai provider carries no sampling)",
        "repetitions": args.reps,
        "questions": [q.id for q in questions],
        "dryrun": table,
        "prereg": {p.name: _sha256(p) for p in PREREG_FILES},
    }
    (folder / "header.json").write_text(
        json.dumps(header, indent=1, default=str), encoding="utf-8"
    )
    (folder / "PREREG.sha256").write_text(
        "".join(f"{_sha256(p)}  {p.relative_to(ROOT)}\n" for p in PREREG_FILES)
        + f"frozen {stamp} before the first model call\n"
    )
    shutil.copy(RECIPE, folder / RECIPE.name)
    print(f"run {folder}", flush=True)

    try:
        import concurrent.futures as cf

        jobs = [(question, rep) for rep in range(args.reps) for question in questions]
        with (
            open(folder / "results.jsonl", "a", encoding="utf-8") as out,
            cf.ThreadPoolExecutor(args.parallel) as pool,
        ):
            futures = {
                pool.submit(episode, question, rep, folder, env, args): (question, rep)
                for question, rep in jobs
            }
            # In the order they finish: one slow question holds nothing back
            for future in cf.as_completed(futures):
                question, rep = futures[future]
                try:
                    record = future.result()
                except Exception as e:  # the driver's own fault, recorded, not fatal
                    record = {
                        "question": question.id,
                        "category": question.category,
                        "rep": rep,
                        "answer": None,
                        "answer_raw": None,
                        "coerced": False,
                        "verdict": "infra",
                        "problems": [],
                        "known": ai_golden.recorded(question.expected),
                        "tool_calls": 0,
                        "tools": {},
                        "tokens": {},
                        "seconds": None,
                        "exit_code": None,
                        "infra": True,
                        "stdout_bytes": 0,
                        "timed_out": False,
                        "export_error": "",
                        "error": f"driver: {type(e).__name__}: {e}"[:500],
                        "provider_error": False,
                    }
                out.write(json.dumps(record, default=str) + "\n")
                out.flush()
                print(
                    f"{question.id:<4} rep{rep} {record['verdict']:<8} "
                    f"{(record['seconds'] or 0):6.1f}s  {record['tool_calls']} call(s)"
                    + ("  (numbers read from text)" if record.get("coerced") else "")
                    + ("  TIMEOUT" if record.get("timed_out") else "")
                    + (f"  {record['problems'][:2]}" if record["problems"] else "")
                    + (f"  {record['error'][-120:]!r}" if record["error"] else ""),
                    flush=True,
                )
    finally:
        # The token dies with the run
        try:
            session.post(
                f"{args.url}/api/auth/access_token/regenerate",
                json={"service_name": "ai-agent"},
                timeout=30,
            )
        except Exception:
            pass
    summarize(folder)
    return 0


def episode(
    question: ai_golden.Question,
    rep: int,
    folder: Path,
    env: dict[str, str],
    args: argparse.Namespace,
) -> dict[str, Any]:
    name = f"{folder.name}-{question.id}-rep{rep}"
    started = time.monotonic()
    command = [
        "goose",
        "run",
        "--recipe",
        str(RECIPE),
        "--params",
        f"question_id={question.id}",
        "--params",
        f"question={question.text}",
        "--params",
        f"shape={answer_shape(question)}",
        "--params",
        f"mascope_ai={args.mascope_ai}",
        "--name",
        name,
        "--max-turns",
        str(args.max_turns),
        "--output-format",
        "json",
    ]
    timed_out = False
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, env=env, timeout=args.timeout
        )
        stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as e:
        # The partial output arrives as bytes here: the model did not finish
        # within the wall clock, which the record says (the bench's CAP)
        stdout, stderr, code = _text(e.stdout), _text(e.stderr) + "\nTIMEOUT", -1
        timed_out = True
    except OSError as e:
        stdout, stderr, code = "", f"goose could not be run: {e}", -2
    seconds = time.monotonic() - started
    where = folder / "transcripts" / f"{question.id}-rep{rep}"
    where.with_suffix(".stdout.txt").write_text(
        stdout, encoding="utf-8", errors="replace"
    )
    where.with_suffix(".stderr.txt").write_text(
        stderr, encoding="utf-8", errors="replace"
    )
    export_error = ""
    for fmt, suffix in (("json", "json"), ("markdown", "md")):
        try:
            subprocess.run(
                [
                    "goose",
                    "session",
                    "export",
                    "--name",
                    name,
                    "--format",
                    fmt,
                    "-o",
                    str(where.with_suffix(f".{suffix}")),
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=300,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            export_error = f"{type(e).__name__}: {e}"[:200]
    raw, about = _parse_answer(stdout)
    scored = score(question, raw)
    # Goose exited without an answer: a model that ran out of wall clock is
    # missing (it did not finish); otherwise a provider error, or the
    # harness's own fault (a bad recipe, a missing extension): infra either
    # way, never the model's miss, and the stderr tail says which
    infra = code != 0 and raw is None and not timed_out
    if infra:
        scored["verdict"] = "infra"
    return {
        "question": question.id,
        "category": question.category,
        "rep": rep,
        **scored,
        "known": ai_golden.recorded(question.expected),
        "tool_calls": about["tool_calls"],
        "tools": about["tools"],
        "tokens": about["tokens"],
        "seconds": round(seconds, 1),
        "exit_code": code,
        "infra": infra,
        "stdout_bytes": len(stdout.encode()),
        "timed_out": timed_out,
        "export_error": export_error,
        "error": stderr.strip()[-500:] if (infra or timed_out) else "",
        "provider_error": bool(INFRA.search(stderr + stdout)) if infra else False,
    }


def recover(folder: Path) -> None:
    """Rebuild ``results.jsonl`` from the transcripts on disk: every
    ``<question>-rep<n>.stdout.txt`` the episodes wrote, scored now. For a
    run whose driver stopped before writing its records; an episode with no
    transcript is not recovered, and the summary says how many were."""
    questions = {q.id: q for q in ai_golden.QUESTIONS}
    records = []
    for stdout_file in sorted((folder / "transcripts").glob("*-rep*.stdout.txt")):
        qid, _, rep = stdout_file.name[: -len(".stdout.txt")].rpartition("-rep")
        if qid not in questions:
            continue
        stdout = stdout_file.read_text(encoding="utf-8", errors="replace")
        stderr_file = stdout_file.with_name(f"{qid}-rep{rep}.stderr.txt")
        stderr = (
            stderr_file.read_text(encoding="utf-8", errors="replace")
            if stderr_file.is_file()
            else ""
        )
        raw, about = _parse_answer(stdout)
        scored = score(questions[qid], raw)
        timed_out = "TIMEOUT" in stderr
        if raw is None and not timed_out and not stdout.strip():
            scored["verdict"] = "infra"
        records.append(
            {
                "question": qid,
                "category": questions[qid].category,
                "rep": int(rep),
                **scored,
                "known": ai_golden.recorded(questions[qid].expected),
                "tool_calls": about["tool_calls"],
                "tools": about["tools"],
                "tokens": about["tokens"],
                "seconds": None,
                "exit_code": None,
                "infra": scored["verdict"] == "infra",
                "stdout_bytes": len(stdout.encode()),
                "timed_out": timed_out,
                "export_error": "",
                "error": stderr.strip()[-500:]
                if (scored["verdict"] == "infra" or timed_out)
                else "",
                "provider_error": bool(INFRA.search(stderr)),
                "recovered": True,
            }
        )
    records.sort(key=lambda r: (r["rep"], list(questions).index(r["question"])))
    results = folder / "results.jsonl"
    if results.exists():
        shutil.copy(results, folder / "results.orig.jsonl")
    results.write_text(
        "".join(json.dumps(r, default=str) + "\n" for r in records), encoding="utf-8"
    )
    print(f"recovered {len(records)} episode(s) from transcripts")
    summarize(folder)


def summarize(folder: Path) -> None:
    records = [
        json.loads(line)
        for line in (folder / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    header = json.loads((folder / "header.json").read_text(encoding="utf-8"))
    lines = [
        f"# Matrix row: {header['model']} via {header['provider']} through goose {header['runtime']['version']}",
        "",
        f"Run `{folder.name}`; bundle {header['bundle']}; toolkit {header['toolkit_commit'][:9]}; pin {header['pin']}; "
        f"reasoning {header['reasoning']}; {header['repetitions']} repetition(s).",
        "",
        "| Class | Episodes | Correct | Wrong | Missing | Infra (excluded) |",
        "|---|---|---|---|---|---|",
    ]
    for category in ai_golden.CATEGORIES:
        rows = [r for r in records if r["category"] == category]
        scored = [r for r in rows if r["verdict"] != "infra"]
        count = lambda v: sum(1 for r in scored if r["verdict"] == v)  # noqa: E731
        lines.append(
            f"| {category} | {len(scored)} | {count('correct')} | {count('wrong')} | {count('missing')} | {len(rows) - len(scored)} |"
        )
    lines += [
        "",
        "**wrong** = stated but false, or a value where none is available (the fabrication measure); **missing** = abstained or unfinished; "
        "wrong and missing are never added. **sloppy** is the reviewer's verdict, written into the transcript. Provider errors are infra, excluded.",
        "",
        "## Episodes",
        "",
        "| Question | Rep | Verdict | Problems | Tool calls | s | Tokens |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in records:
        lines.append(
            f"| {r['question']} | {r['rep']} | {r['verdict']} | {'; '.join(r['problems'][:3])} | {r['tool_calls']} | {r['seconds']} | {(r.get('tokens') or {}).get('total_tokens') or ''} |"
        )
    (folder / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[: 4 + len(ai_golden.CATEGORIES) + 2]))


#: What a transcript never carries, whatever a tool result held.
SECRET = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{16,}|([A-Za-z0-9_\-]{48,})")


def _scrubbed(text: str, scrub: tuple[str, ...] = ()) -> str:
    for word in scrub:
        if word:
            text = text.replace(word, "[redacted]")
    return SECRET.sub(lambda m: (m.group(1) or "") + "[redacted]", text)


def _clipped(text: str, chars: int = 1500) -> str:
    return (
        text
        if len(text) <= chars
        else text[:chars] + f" ... [{len(text) - chars} more characters]"
    )


def transcript(folder: Path, scrub: tuple[str, ...] = ()) -> str:
    """The run as ``golden_transcripts/README.md`` keeps it: the header, the
    verdict table, then every episode's tool calls and results as the model
    saw them, from Goose's output. Secrets are scrubbed; the reviewer's
    column (sloppy, a corrected verdict) is left for the reviewer."""
    header = json.loads((folder / "header.json").read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in (folder / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    questions = {q.id: q for q in ai_golden.QUESTIONS}
    lines = [
        f"# Golden questions: {header['model']} through Goose, {header['started'][:10]}",
        "",
        "## Header",
        "",
        f"- Run `{header['run']}`, started {header['started']}.",
        f"- Demo bundle {header['bundle']}; stack build `{header['stack'].get('build_tag')}`; "
        f"toolkit commit `{header['toolkit_commit'][:12]}` ({header['toolkit_branch']}).",
        f"- Runtime: goose {header['runtime']['version']}, recipe `{header['runtime']['recipe']}`, "
        f"max turns {header['runtime']['max_turns']}; the MCP server `mascope-ai mcp` as its extension.",
        f"- Provider {header['provider']}; model id `{header['model']}`; weights {header['weights'] or 'as the model id names them'}.",
        f"- Precision served: `{json.dumps(header['precision_served'], default=str)[:300]}`.",
        f"- Reasoning: {header['reasoning']}. Pin: {header['pin']}. Sampling: {json.dumps(header['sampling'])}.",
        f"- Repetitions: {header['repetitions']}; questions: {', '.join(header['questions'])}.",
        f"- Pre-registration: {', '.join(f'{k} {v[:12]}' for k, v in header['prereg'].items())}.",
        "",
        "The checker's dry run before the run:",
        "",
        "```",
        header["dryrun"],
        "```",
        "",
        "## Verdicts",
        "",
        "Checker verdicts are the fixture's (`ai_golden.check_answer`): correct, wrong (stated but false, or a value "
        "where none is available), missing, infra (excluded). The reviewer's column holds the hand verdict where it "
        "differs (sloppy: right, an instruction not followed), empty where it agrees.",
        "",
        "| Question | Rep | Model's answer | Known answer | Checker | Reviewer |",
        "|---|---|---|---|---|---|",
    ]
    for r in records:
        cell = lambda v: _scrubbed(json.dumps(v, default=str), scrub).replace(
            "|", "\\|"
        )  # noqa: E731
        lines.append(
            f"| {r['question']} | {r['rep']} | {_clipped(cell(r['answer']), 300)} | {_clipped(cell(r['known']), 300)} | "
            f"{r['verdict']}{' (' + '; '.join(r['problems'][:2]) + ')' if r['problems'] else ''}"
            f"{' numbers read from text' if r.get('coerced') else ''} |  |"
        )
    lines += ["", "## Episodes", ""]
    for r in records:
        question = questions.get(r["question"])
        lines += [
            f"### {r['question']} rep {r['rep']}: {r['verdict']}",
            "",
            f"> {question.text if question else r['question']}",
            "",
            f"{r['seconds']} s; {r['tool_calls']} tool call(s); tokens {json.dumps(r.get('tokens'))}"
            + (f"; error: {_scrubbed(r['error'], scrub)}" if r.get("error") else ""),
            "",
        ]
        stdout_file = (
            folder / "transcripts" / f"{r['question']}-rep{r['rep']}.stdout.txt"
        )
        doc = (
            _goose_output(stdout_file.read_text(encoding="utf-8"))
            if stdout_file.is_file()
            else None
        )
        for message in (doc or {}).get("messages", ()):
            for part in message.get("content", ()):
                kind = part.get("type")
                if kind == "toolRequest":
                    call = (part.get("toolCall") or {}).get("value") or {}
                    lines.append(
                        f"- **{call.get('name')}** `{_scrubbed(json.dumps(call.get('arguments'), default=str), scrub)}`"
                    )
                elif kind == "toolResponse":
                    result = (part.get("toolResult") or {}).get("value") or {}
                    texts = [
                        c.get("text", "")
                        for c in result.get("content", ())
                        if c.get("type") == "text"
                    ]
                    lines += [
                        "",
                        "  ```",
                        "  "
                        + _clipped(_scrubbed(" ".join(texts), scrub)).replace(
                            "\n", "\n  "
                        ),
                        "  ```",
                        "",
                    ]
                elif kind == "text" and message.get("role") == "assistant":
                    lines += [
                        "",
                        "  Final: `"
                        + _clipped(_scrubbed(part.get("text", ""), scrub), 600)
                        + "`",
                        "",
                    ]
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("dryrun", help="the checker's own check, the gate of every run")
    runner = commands.add_parser(
        "run", help="one row: every question, --reps times, through goose"
    )
    runner.add_argument("--provider", choices=sorted(PROVIDERS), required=True)
    runner.add_argument(
        "--model", required=True, help="the model id as the provider names it"
    )
    runner.add_argument(
        "--pin", help="openrouter: <provider>/<quantization> to pin, fallbacks off"
    )
    runner.add_argument(
        "--weights", default="", help="the Hugging Face repository the model id names"
    )
    runner.add_argument("--reps", type=int, default=3)
    runner.add_argument("--only", help="question IDs, comma-separated")
    runner.add_argument(
        "--pilot",
        action="store_true",
        help="one question, one repetition: settle the harness",
    )
    runner.add_argument("--max-turns", type=int, default=30)
    runner.add_argument(
        "--parallel",
        type=int,
        default=3,
        help="episodes run at once (each its own Goose session)",
    )
    runner.add_argument(
        "--timeout",
        type=float,
        default=3600,
        help="wall clock per episode in seconds (the bench's 60 minutes); past it the episode is missing",
    )
    runner.add_argument(
        "--url", default=os.environ.get("MASCOPE_SDK_TEST_URL", "http://127.0.0.1:8080")
    )
    runner.add_argument(
        "--email", default=os.environ.get("MASCOPE_SDK_TEST_EMAIL", "demo@mascope.app")
    )
    runner.add_argument(
        "--password",
        default=os.environ.get("MASCOPE_SDK_TEST_PASSWORD", "mascope-demo"),
    )
    runner.add_argument("--workspace", default=ai_golden.WORKSPACE)
    runner.add_argument(
        "--stack-tag",
        default=os.environ.get("MASCOPE_VERSION", "unknown"),
        help="the demo stack's image tag",
    )
    runner.add_argument(
        "--mascope-ai",
        default=shutil.which("mascope-ai") or "mascope-ai",
        help="the mascope-ai executable the recipe runs",
    )
    summarizer = commands.add_parser(
        "summarize", help="rebuild summary.md of a run folder"
    )
    summarizer.add_argument("folder", type=Path)
    recoverer = commands.add_parser(
        "recover", help="rebuild a run's results from the transcripts on disk"
    )
    recoverer.add_argument("folder", type=Path)
    rescorer = commands.add_parser(
        "rescore", help="score a run's stored answers again with the current checker"
    )
    rescorer.add_argument("folder", type=Path)
    renderer = commands.add_parser(
        "transcript",
        help="render a run folder as the transcript golden_transcripts/ keeps",
    )
    renderer.add_argument("folder", type=Path)
    renderer.add_argument(
        "-o", "--output", type=Path, help="write it here instead of stdout"
    )
    renderer.add_argument(
        "--scrub",
        action="append",
        default=[],
        help="a string that must not appear (a token)",
    )
    args = parser.parse_args(argv)
    if args.command == "dryrun":
        print(dryrun_gate())
        return 0
    if args.command == "summarize":
        summarize(args.folder)
        return 0
    if args.command == "recover":
        recover(args.folder)
        return 0
    if args.command == "rescore":
        rescore(args.folder)
        return 0
    if args.command == "transcript":
        text = transcript(args.folder, tuple(args.scrub))
        if args.output:
            args.output.write_text(text, encoding="utf-8")
            print(f"{args.output}: {len(text)} characters")
        else:
            print(text)
        return 0
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
