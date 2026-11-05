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
    for line in ENV_FILE.read_text().splitlines():
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


def _parse_answer(stdout: str) -> Any:
    """The model's final JSON object from Goose's output: the last top-level
    object with a ``question_id``, or the last object at all."""
    decoder = json.JSONDecoder()
    found = []
    i = 0
    while i < len(stdout):
        if stdout[i] == "{":
            try:
                obj, end = decoder.raw_decode(stdout[i:])
                found.append(obj)
                i += end
                continue
            except ValueError:
                pass
        i += 1
    for obj in reversed(found):
        if isinstance(obj, dict) and "question_id" in obj and "answer" in obj:
            return obj["answer"]
        if isinstance(obj, dict) and isinstance(
            obj.get("result"), str
        ):  # --output-format json wraps the reply
            inner = _parse_answer(obj["result"])
            if inner is not None:
                return inner
    return None


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
    (folder / "header.json").write_text(json.dumps(header, indent=1, default=str))
    (folder / "PREREG.sha256").write_text(
        "".join(f"{_sha256(p)}  {p.relative_to(ROOT)}\n" for p in PREREG_FILES)
        + f"frozen {stamp} before the first model call\n"
    )
    shutil.copy(RECIPE, folder / RECIPE.name)
    print(f"run {folder}", flush=True)

    try:
        with open(folder / "results.jsonl", "a") as out:
            for rep in range(args.reps):
                for question in questions:
                    record = episode(question, rep, folder, env, args)
                    out.write(json.dumps(record, default=str) + "\n")
                    out.flush()
                    print(
                        f"{question.id:<4} rep{rep} {record['verdict']:<8} {record['seconds']:6.1f}s"
                        + (f"  {record['problems'][:2]}" if record["problems"] else ""),
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
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, env=env, timeout=args.timeout
        )
        stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as e:
        stdout, stderr, code = (e.stdout or ""), (e.stderr or "") + "\nTIMEOUT", -1
    seconds = time.monotonic() - started
    (folder / "transcripts" / f"{question.id}-rep{rep}.stdout.txt").write_text(stdout)
    (folder / "transcripts" / f"{question.id}-rep{rep}.stderr.txt").write_text(stderr)
    for fmt, suffix in (("json", "json"), ("markdown", "md")):
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
                str(folder / "transcripts" / f"{question.id}-rep{rep}.{suffix}"),
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
    answer = _parse_answer(stdout) if code == 0 else _parse_answer(stdout)
    # Goose exited without an answer: a provider error, or the harness's own
    # fault (a bad recipe, a missing extension): infra either way, never the
    # model's miss, and the stderr tail says which
    infra = code != 0 and answer is None
    verdict = "infra" if infra else ai_golden.verdict_of(question, answer)
    problems = [] if answer is None else ai_golden.check_answer(question, answer)
    tool_calls = len(re.findall(r'"type"\s*:\s*"tool_(use|call|request)"', stdout))
    return {
        "question": question.id,
        "category": question.category,
        "rep": rep,
        "answer": answer,
        "verdict": verdict,
        "problems": problems,
        "known": ai_golden.recorded(question.expected),
        "tool_calls": tool_calls,
        "seconds": round(seconds, 1),
        "exit_code": code,
        "infra": infra,
        "stdout_bytes": len(stdout.encode()),
        "error": stderr.strip()[-500:] if infra else "",
        "provider_error": bool(INFRA.search(stderr + stdout)) if infra else False,
    }


def summarize(folder: Path) -> None:
    records = [
        json.loads(line)
        for line in (folder / "results.jsonl").read_text().splitlines()
        if line.strip()
    ]
    header = json.loads((folder / "header.json").read_text())
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
        "| Question | Rep | Verdict | Problems | Tool calls | s |",
        "|---|---|---|---|---|---|",
    ]
    for r in records:
        lines.append(
            f"| {r['question']} | {r['rep']} | {r['verdict']} | {'; '.join(r['problems'][:3])} | {r['tool_calls']} | {r['seconds']} |"
        )
    (folder / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[: 4 + len(ai_golden.CATEGORIES) + 2]))


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
        "--timeout", type=float, default=1800, help="seconds per episode"
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
    args = parser.parse_args(argv)
    if args.command == "dryrun":
        print(dryrun_gate())
        return 0
    if args.command == "summarize":
        summarize(args.folder)
        return 0
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
