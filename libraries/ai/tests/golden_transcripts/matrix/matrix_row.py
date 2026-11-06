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
  which also stands in for the stdout document of an episode killed at the
  wall clock (Goose writes that document only when a run ends): its tool
  calls, tokens and, as ``silent_s``, how long the model had been silent,
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
import statistics
import subprocess
import sys
import tempfile
import threading
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

#: List prices in USD per million tokens (input, output), by a part of the
#: model id, as the bench recorded them (Nebius, 31 October 2026). An unknown
#: model is priced as the most expensive one seen, so a cap errs on the safe
#: side.
PRICE = {
    "nemotron-3.5-super-vl": (0.30, 0.90),
    "nemotron-3-super": (0.30, 0.90),
    "nemotron-3_5-lightning": (0.06, 0.24),
    "nemotron-3-nano": (0.06, 0.24),
    "gpt-oss-120b": (0.15, 0.60),
}
PRICE_UNKNOWN = (0.60, 3.60)

#: What an episode that ran out of wall clock, or died without a token count,
#: is charged: a long episode's worth of input, so the cap is not fooled.
TOKENS_UNACCOUNTED = (300_000, 10_000)


def price_of(model: str) -> tuple[float, float]:
    for part, price in PRICE.items():
        if part in model.lower():
            return price
    return PRICE_UNKNOWN


def cost_of(tokens: dict[str, Any] | None, model: str) -> float:
    """An episode's list-price cost in USD from its token counts; an episode
    with no counts is charged :data:`TOKENS_UNACCOUNTED`."""
    price_in, price_out = price_of(model)
    tokens = tokens or {}
    tin, tout = tokens.get("input_tokens"), tokens.get("output_tokens")
    if tin is None and tout is None:
        tin, tout = TOKENS_UNACCOUNTED
    return ((tin or 0) * price_in + (tout or 0) * price_out) / 1e6


def spent(runs_root: Path = RUNS_ROOT) -> float:
    """The list-price spend of every run under *runs_root*, from their records
    (an episode without a cost field is costed now, at its model's price)."""
    total = 0.0
    for results in runs_root.glob("*/results.jsonl"):
        try:
            header = json.loads(
                (results.parent / "header.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            header = {}
        for line in results.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            cost = record.get("cost_usd")
            total += (
                cost
                if cost is not None
                else cost_of(record.get("tokens"), header.get("model", ""))
            )
    return total


#: The run's running spend, shared by the episodes: a cap stops new episodes.
SPEND = {"usd": 0.0, "cap": None, "lock": threading.Lock()}


#: What the mcp+python arm tells the model besides: it has a shell, and the
#: toolkit's Python is the way to compute over a Frame. The same path a coding
#: agent took on the first transcript (``golden_transcripts/2026-10-27-...``).
PYTHON_ARM_NOTE = (
    "You also have a shell (the developer extension). To compute over data, run "
    "Python with `{python}`: `from mascope_ai import Toolkit, Session; from "
    "mascope_sdk import MascopeClient; kit = Toolkit(MascopeClient(service_name="
    "'ai-agent'))` reads MASCOPE_URL, MASCOPE_ACCESS_TOKEN and MASCOPE_WORKSPACE "
    "from the environment; `kit.load_peaks(...)` and the other loaders return "
    "Frames whose `.df` is a pandas DataFrame you can aggregate. Print only the "
    "numbers you need. Write nothing to Mascope."
)

#: What the model is told when the server has ``reduce``: the one computation
#: it can ask for over MCP. Given only then, so a row whose toolkit lacks the
#: tool is not told of one it cannot call.
REDUCE_HINT = (
    "To compute over a Frame (a maximum, a median per group, the row an extreme "
    "is in), call `reduce` with the Frame's ID. The few rows a Frame handle shows "
    "are not an answer."
)

#: The sampling the bench ran every model with.
SAMPLING = {"temperature": 1.0, "top_p": 0.95, "max_tokens": 32768}

INFRA = re.compile(
    r"(429|rate limit|timed? ?out|502|503|504|529|overloaded|server error"
    r"|connection (error|refused|reset)|ECONNRESET)",
    re.I,
)

#: How long an episode waits before its retry after a provider error (a 529,
#: a 503): long enough for a short overload to pass.
RETRY_PAUSE_S = 120


def mcp_tools(command: list[str]) -> list[str]:
    """The tools the MCP server *command* lists, asked over stdio with nothing
    configured (the listing needs no stack): the surface this row runs on."""
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "matrix_row", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    env = {k: v for k, v in os.environ.items() if not k.startswith("MASCOPE_")}
    with tempfile.TemporaryDirectory() as scratch:
        env["MASCOPE_AI_SCRATCH"] = scratch
        done = subprocess.run(
            command,
            input="".join(json.dumps(m) + "\n" for m in messages),
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
    for line in done.stdout.splitlines():
        try:
            answer = json.loads(line)
        except ValueError:
            continue
        if answer.get("id") == 2:
            return [t["name"] for t in answer.get("result", {}).get("tools", [])]
    raise RuntimeError(f"{command[0]} listed no tools: {done.stderr.strip()[-300:]}")


def extra_instructions(arm: str, tools: list[str], python: str = sys.executable) -> str:
    """The recipe's ``extra`` parameter for this row: the reduce hint when the
    server has the tool, and the shell note on the mcp+python arm."""
    parts = []
    if "reduce" in tools:
        parts.append(REDUCE_HINT)
    if arm == "mcp+python":
        parts.append(PYTHON_ARM_NOTE.format(python=python))
    return "\n".join(f"- {part}" for part in parts)


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


#: The one run at a time: a second run on the same demo account mints the
#: account's ``ai-agent`` token again, and the first run's token dies with
#: it, so every call its model makes from then on is a 401. The lock names
#: the run holding the stack.
LOCK = RUNS_ROOT / "RUNNING"


def _lock(folder: Path) -> None:
    """Take the lock for *folder*'s run, or exit naming the run that holds it.

    A lock whose process is gone (a killed driver) is taken over, where the
    platform can tell; elsewhere the file has to be removed by hand.
    """
    if LOCK.exists():
        held = LOCK.read_text(encoding="utf-8").split()
        pid = int(held[0]) if held and held[0].isdigit() else None
        alive = True
        if pid is not None and os.name == "posix":
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = True
        if alive:
            sys.exit(
                f"another run holds the stack: {' '.join(held)} ({LOCK}). One run at a "
                "time per demo account: a second run would mint the ai-agent token "
                "again and kill the first run's token. Wait for it, or remove the lock "
                "if its driver is gone."
            )
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(f"{os.getpid()} {folder.name}\n", encoding="utf-8")


def _unlock(folder: Path) -> None:
    try:
        if folder.name in LOCK.read_text(encoding="utf-8"):
            LOCK.unlink()
    except OSError:
        pass


#: What a tool result says when the run's token is no longer the account's:
#: the episode's calls fail from there on, which is the harness's fault.
TOKEN_REVOKED = "not_authenticated"


def _token_revoked(*texts: str) -> bool:
    """Whether an episode's output or export shows the token refused."""
    return any(TOKEN_REVOKED in text for text in texts if text)


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
        shaped = {"available": "<true/false>", "reason": "<why, when not available>"}
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
    answer, tools = _read_messages(doc.get("messages", ()))
    tokens = {
        key: doc.get("metadata", {}).get(key)
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }
    return answer, {"tool_calls": sum(tools.values()), "tools": tools, "tokens": tokens}


def _read_messages(messages: Any) -> tuple[Any, dict[str, int]]:
    """The answer given in *messages*, if any, and the tool calls made, by
    tool: the same reading of Goose's stdout document and of its session
    export, whose messages have the same shape."""
    answer = None
    tools: dict[str, int] = {}
    for message in messages or ():
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
    return answer, tools


def _parse_export(path: Path) -> tuple[Any, dict[str, Any]] | None:
    """What the session export at *path* says of an episode Goose never
    finished: the answer, if one was given, the tool calls made, the tokens
    accumulated over its turns, and ``last_message`` (epoch seconds of the
    last message, so the record can say how long the model was silent before
    the wall clock). Goose writes its stdout document only when a run ends,
    so an episode killed at the wall clock has none, and without the export
    it would read as one of no calls at all. None when there is no export."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or "conversation" not in doc:
        return None
    answer, tools = _read_messages(doc.get("conversation"))
    usage = doc.get("accumulated_usage") or {}
    tokens = {
        key: usage.get(key) for key in ("input_tokens", "output_tokens", "total_tokens")
    }
    created = [
        m.get("created") for m in doc.get("conversation") or () if m.get("created")
    ]
    return answer, {
        "tool_calls": sum(tools.values()),
        "tools": tools,
        "tokens": tokens,
        "last_message": max(created) if created else None,
    }


#: An m/z a model reports is held to this, not the bundle's 0.1 ppm: a model
#: that answers the m/z it queried (four decimals) names the right peak with
#: the wrong precision, which is the reviewer's call (sloppy), not a wrong peak.
MODEL_MZ_PPM = 1.0


def lenient(answer: Any, expected: Any = None) -> tuple[Any, list[str]]:
    """*answer* read as a reviewer would before the checker sees it, and the
    readings made, each named.

    The fixture's checker holds a solver to the shape it asked for. A model
    run is scored on the substance, as the bench scored its: a number written
    as text is the number (``text_number``); a key the known answer lacks,
    given as null, is not an extra (``null_extra``); a list of names is
    compared whatever its order (``reordered``); an interpretation's
    ``scored`` and ``unscored`` are compared without the suggestions the
    fixture's own solver made, which a model never saw (``suggestions``).
    Each reading is recorded, so the reviewer can call it sloppy.
    """
    readings: list[str] = []

    def note(reading: str) -> None:
        if reading not in readings:
            readings.append(reading)

    def walk(value: Any, known: Any) -> Any:
        if (
            isinstance(value, str)
            and _NUMBER.fullmatch(value.strip())
            and not isinstance(
                known, str
            )  # a text the known answer has as text stays one
        ):
            note("text_number")
            number = float(value)
            return int(number) if number.is_integer() and "." not in value else number
        if isinstance(value, dict):
            known_dict = known if isinstance(known, dict) else {}
            out = {}
            for key, inner in value.items():
                if inner is None and known_dict and key not in known_dict:
                    note("null_extra")
                    continue
                out[key] = walk(inner, known_dict.get(key))
            return out
        if isinstance(value, list):
            known_list = known if isinstance(known, (list, tuple)) else []
            items = [
                walk(inner, known_list[i] if i < len(known_list) else None)
                for i, inner in enumerate(value)
            ]
            if (
                known_list
                and all(isinstance(k, str) for k in known_list)
                and all(isinstance(i, str) for i in items)
                and sorted(items) == sorted(known_list)
                and items != list(known_list)
            ):
                note("reordered")
                return list(known_list)
            return items
        return value

    read = walk(answer, expected)
    if (
        isinstance(read, dict)
        and isinstance(expected, dict)
        and {"candidates", "scored", "unscored"} <= set(expected)
    ):
        # The fixture's solver suggested candidates of its own; what Mascope
        # said of them is the solver's answer, not the model's to give
        read = dict(read)
        if read.get("unscored") == [] and expected.get("unscored"):
            note("suggestions")
            read["unscored"] = list(expected["unscored"])
        if isinstance(read.get("scored"), dict) and isinstance(
            expected.get("scored"), dict
        ):
            own = {
                f for f in expected["scored"] if f not in expected.get("candidates", ())
            }
            if own and not own & set(read["scored"]):
                note("suggestions")
                read["scored"] = {
                    **read["scored"],
                    **{f: expected["scored"][f] for f in own},
                }
    return read, readings


def _mz_within_a_ppm(expected: Any) -> Any:
    """The known answer with every ppm-level tolerance widened to
    :data:`MODEL_MZ_PPM`, for a model's answer."""
    if isinstance(expected, ai_golden.Close) and 0 < expected.rel < 1e-5:
        return ai_golden.Close(
            expected.value, rel=MODEL_MZ_PPM * 1e-6, off=expected.off
        )
    if isinstance(expected, dict):
        return {k: _mz_within_a_ppm(v) for k, v in expected.items()}
    if isinstance(expected, (list, tuple)):
        return [_mz_within_a_ppm(v) for v in expected]
    return expected


def score(question: ai_golden.Question, raw: Any) -> dict[str, Any]:
    """The checker's verdict on a model's *raw* answer, read leniently.

    A "not available" given to a question whose answer is available is an
    abstention: missing, never wrong, as the bench scored its nulls. An m/z
    is held to :data:`MODEL_MZ_PPM`; a wider reading is a ``rounded_mz``.
    """
    answer, readings = lenient(raw, ai_golden.recorded(question.expected))
    declined = isinstance(answer, dict) and answer.get("available") is False
    if declined and question.category != "decline":
        return {
            "answer": answer,
            "answer_raw": raw,
            "coerced": bool(readings),
            "readings": readings,
            "verdict": "missing",
            "problems": ["declined a question the surface answers"],
        }
    problems = [] if answer is None else ai_golden.compare(question.expected, answer)
    if problems and answer is not None:
        looser = ai_golden.compare(_mz_within_a_ppm(question.expected), answer)
        if len(looser) < len(problems):
            readings.append("rounded_mz")
            problems = looser
    if (
        answer is not None
        and question.check is not None
        and isinstance(answer, dict)
        and not problems
    ):
        problems += question.check(
            answer,
            {
                "values": {
                    "unscored": [
                        {"formula": f, "origin": "model"}
                        for f in answer.get("unscored", ())
                    ]
                }
            },
        )
    verdict = "missing" if answer is None else ("wrong" if problems else "correct")
    return {
        "answer": answer,
        "answer_raw": raw,
        "coerced": bool(readings),
        "readings": readings,
        "verdict": verdict,
        "problems": problems,
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
    already = spent(RUNS_ROOT)
    if args.budget_usd is not None:
        if already >= args.budget_usd:
            sys.exit(
                f"the runs so far cost ${already:.2f}, at or past the budget of ${args.budget_usd:.2f}"
            )
        SPEND["usd"], SPEND["cap"] = already, args.budget_usd
        print(f"spent so far ${already:.2f} of ${args.budget_usd:.2f}", flush=True)
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
    _lock(folder)
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
    tools = mcp_tools([args.mascope_ai, "mcp"])
    args.extra = extra_instructions(args.arm, tools)

    header = {
        "run": folder.name,
        "started": stamp,
        "bundle": ai_golden.BUNDLE,
        "stack": {**_stack_header(args.url, token), "build_tag": args.stack_tag},
        "toolkit_commit": _git("rev-parse", "HEAD"),
        "toolkit_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "tools": tools,
        "extra_instructions": args.extra,
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
        "arm": args.arm,
        "price_per_mtoken": price_of(args.model),
        "budget_usd": args.budget_usd,
        "spent_before_usd": round(already, 4),
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
                    + (
                        f"  read: {','.join(record.get('readings', []))}"
                        if record.get("readings")
                        else ""
                    )
                    + (
                        "  TIMEOUT"
                        + (
                            f" after {record['silent_s']}s of silence"
                            if record.get("silent_s") is not None
                            else ""
                        )
                        if record.get("timed_out")
                        else ""
                    )
                    + ("  TOKEN REVOKED" if record.get("token_revoked") else "")
                    + (f"  {record['problems'][:2]}" if record["problems"] else "")
                    + (f"  {record['error'][-120:]!r}" if record["error"] else ""),
                    flush=True,
                )
    finally:
        _unlock(folder)
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
    """One episode's record; after a provider error (a 529, a 503, a reset) it
    is tried again, ``--retry-infra`` times at most, after a pause, and the
    record says so. A model that ran out of wall clock is not retried: that
    is its result."""
    record = _attempt(question, rep, folder, env, args)
    tries = 0
    while (
        record["verdict"] == "infra"
        and record.get("provider_error")
        and tries < getattr(args, "retry_infra", 0)
    ):
        tries += 1
        time.sleep(RETRY_PAUSE_S)
        earlier = record["error"]
        record = _attempt(question, rep, folder, env, args, attempt=tries)
        record["retried"] = tries
        record["retried_after"] = earlier[-200:]
    return record


def _attempt(
    question: ai_golden.Question,
    rep: int,
    folder: Path,
    env: dict[str, str],
    args: argparse.Namespace,
    attempt: int = 0,
) -> dict[str, Any]:
    # A retry's Goose session has a name of its own; its transcript files
    # replace the failed attempt's
    name = f"{folder.name}-{question.id}-rep{rep}" + (
        f"-try{attempt + 1}" if attempt else ""
    )
    started = time.monotonic()
    with SPEND["lock"]:
        over = SPEND["cap"] is not None and SPEND["usd"] >= SPEND["cap"]
    if over:
        return {
            "question": question.id,
            "category": question.category,
            "rep": rep,
            "answer": None,
            "answer_raw": None,
            "coerced": False,
            "readings": [],
            "verdict": "infra",
            "problems": [],
            "known": ai_golden.recorded(question.expected),
            "tool_calls": 0,
            "tools": {},
            "tokens": {},
            "seconds": 0.0,
            "exit_code": None,
            "infra": True,
            "stdout_bytes": 0,
            "timed_out": False,
            "export_error": "",
            "error": f"not run: the spend cap of ${SPEND['cap']:.2f} was reached",
            "provider_error": False,
            "cost_usd": 0.0,
        }
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
        "--params",
        "extra=" + getattr(args, "extra", ""),
        "--name",
        name,
        "--max-turns",
        str(args.max_turns),
        "--output-format",
        "json",
    ]
    if args.arm == "mcp+python":
        command += ["--with-builtin", "developer"]
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
    ended = time.time()
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
    silent = None
    if _goose_output(stdout) is None:
        # Killed before Goose wrote its document: the export has the turns
        exported = _parse_export(where.with_suffix(".json"))
        if exported is not None:
            raw, about = exported
            if about["last_message"]:
                silent = round(ended - about["last_message"])
    scored = score(question, raw)
    cost = cost_of(about["tokens"], args.model)
    with SPEND["lock"]:
        SPEND["usd"] += cost
    # Goose exited without an answer: a model that ran out of wall clock is
    # missing (it did not finish); otherwise a provider error, or the
    # harness's own fault (a bad recipe, a missing extension): infra either
    # way, never the model's miss, and the stderr tail says which
    infra = code != 0 and raw is None and not timed_out
    export = where.with_suffix(".json")
    revoked = _token_revoked(
        stdout,
        export.read_text(encoding="utf-8", errors="replace")
        if export.is_file()
        else "",
    )
    if revoked:
        # The token was refused: another run minted the account's token, or
        # the stack lost it. Whatever the model then answered is not its doing
        infra = True
        stderr += "\nTOKEN REVOKED: a tool result says not_authenticated"
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
        "silent_s": silent,
        "export_error": export_error,
        "error": stderr.strip()[-500:] if (infra or timed_out) else "",
        "provider_error": bool(INFRA.search(stderr + stdout)) if infra else False,
        "token_revoked": revoked,
        "cost_usd": cost,
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
        export = stdout_file.with_name(f"{qid}-rep{rep}.json")
        if _goose_output(stdout) is None:
            exported = _parse_export(export)
            if exported is not None:
                raw, about = exported
        scored = score(questions[qid], raw)
        timed_out = "TIMEOUT" in stderr
        if raw is None and not timed_out and not stdout.strip():
            scored["verdict"] = "infra"
        revoked = _token_revoked(
            stdout,
            export.read_text(encoding="utf-8", errors="replace")
            if export.is_file()
            else "",
        )
        if revoked:
            scored["verdict"] = "infra"
            stderr += "\nTOKEN REVOKED: a tool result says not_authenticated"
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
                "token_revoked": revoked,
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


def _load_run(
    spec: "str | Path",
) -> tuple[Path, int, dict[str, Any], list[dict[str, Any]]]:
    """A run folder and its records, from ``<folder>[=<offset>]``: a row
    completed over several runs (a provider outage took a repetition) is
    rendered as one, a later run's repetitions numbered on from the first's
    by *offset*. Each record says which run it is from."""
    text = str(spec)
    offset = 0
    if "=" in text and text.rsplit("=", 1)[1].isdigit():
        text, _, digits = text.rpartition("=")
        offset = int(digits)
    folder = Path(text)
    header = json.loads((folder / "header.json").read_text(encoding="utf-8"))
    records = []
    for line in (folder / "results.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            record["rep"] += offset
            record["run"] = folder.name
            records.append(record)
    return folder, offset, header, records


def class_table(records: list[dict[str, Any]], model: str) -> list[str]:
    """The row's table: per class, the episodes scored, the verdicts, the
    episodes excluded as infra, the median tool calls and seconds of the
    scored episodes, and the list-price cost of all of them."""

    def cost(r: dict[str, Any]) -> float:
        return (
            r["cost_usd"]
            if r.get("cost_usd") is not None
            else cost_of(r.get("tokens"), model)
        )

    lines = [
        "| Class | Episodes | Correct | Wrong | Missing | Infra (excluded) | Median calls | Median s | USD |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for category in ai_golden.CATEGORIES:
        rows = [r for r in records if r["category"] == category]
        scored = [r for r in rows if r["verdict"] != "infra"]
        count = lambda v: sum(1 for r in scored if r["verdict"] == v)  # noqa: E731
        calls = (
            f"{statistics.median(r['tool_calls'] for r in scored):g}" if scored else ""
        )
        seconds = (
            round(statistics.median(r["seconds"] or 0 for r in scored))
            if scored
            else ""
        )
        lines.append(
            f"| {category} | {len(scored)} | {count('correct')} | {count('wrong')} | {count('missing')} "
            f"| {len(rows) - len(scored)} | {calls} | {seconds} | {sum(cost(r) for r in rows):.2f} |"
        )
    lines.append(
        f"| all | {sum(1 for r in records if r['verdict'] != 'infra')} | | | | "
        f"{sum(1 for r in records if r['verdict'] == 'infra')} | | | {sum(cost(r) for r in records):.2f} |"
    )
    return lines


def summarize(folder: Path) -> None:
    _, _, header, records = _load_run(folder)
    lines = [
        f"# Matrix row: {header['model']} via {header['provider']} through goose {header['runtime']['version']}",
        "",
        f"Run `{folder.name}`; bundle {header['bundle']}; toolkit {header['toolkit_commit'][:9]}"
        + (
            f" ({len(header['tools'])} tools{', with reduce' if 'reduce' in header['tools'] else ''})"
            if header.get("tools")
            else ""
        )
        + f"; pin {header['pin']}; "
        f"reasoning {header['reasoning']}; {header['repetitions']} repetition(s).",
        "",
        *class_table(records, header.get("model", "")),
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


def transcript(
    runs: "str | Path | list[str | Path]",
    scrub: tuple[str, ...] = (),
    *,
    clip: int = 1500,
    full: bool = False,
) -> str:
    """The row as ``golden_transcripts/README.md`` keeps it: the header, the
    class table, the verdict of every episode, then each episode's tool calls
    as the model made them, from Goose's output. Secrets are scrubbed; the
    reviewer's column (sloppy, a corrected verdict) is left for the reviewer.

    *runs* is one run folder, or several that together make the row
    (``<folder>=<offset>`` numbers a later run's repetitions on). Tool results
    are clipped to *clip* characters and, unless *full*, shown only for the
    episodes that were not correct: a correct episode's calls say how it got
    there, and the record stays readable.
    """
    specs = [runs] if isinstance(runs, (str, Path)) else list(runs)
    loaded = [_load_run(spec) for spec in specs]
    header = loaded[0][2]
    folders = {folder.name: folder for folder, _, _, _ in loaded}
    order = {q.id: i for i, q in enumerate(ai_golden.QUESTIONS)}
    records = sorted(
        (r for _, _, _, rs in loaded for r in rs),
        key=lambda r: (r["rep"], order.get(r["question"], 99)),
    )
    questions = {q.id: q for q in ai_golden.QUESTIONS}
    commits = sorted({h["toolkit_commit"][:12] for _, _, h, _ in loaded})
    lines = [
        f"# Golden questions: {header['model']} through Goose, {header['started'][:10]}",
        "",
        "## Header",
        "",
    ]
    for folder, offset, h, rs in loaded:
        reps = sorted({r["rep"] for r in rs})
        lines.append(
            f"- Run `{folder.name}`, started {h['started']}: {len(rs)} episode(s), "
            f"repetition(s) {', '.join(map(str, reps))}"
            + (f" (its own numbered from {offset})" if offset else "")
            + "."
        )
    lines += [
        f"- Demo bundle {header['bundle']}; stack build `{header['stack'].get('build_tag')}`; "
        f"toolkit commit{'s' if len(commits) > 1 else ''} {', '.join(f'`{c}`' for c in commits)} ({header['toolkit_branch']})"
        + (
            f"; {len(header['tools'])} tools{', with reduce' if 'reduce' in header['tools'] else ', without reduce'}"
            if header.get("tools")
            else ""
        )
        + ".",
        f"- Runtime: goose {header['runtime']['version']}, recipe `{header['runtime']['recipe']}`, "
        f"max turns {header['runtime']['max_turns']}; the MCP server `mascope-ai mcp` as its extension"
        + (f"; arm {header['arm']}" if header.get("arm") else "")
        + ".",
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
        *class_table(records, header.get("model", "")),
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
            f"{' read: ' + ', '.join(r['readings']) if r.get('readings') else ''} |  |"
        )
    lines += [
        "",
        "## Episodes",
        "",
        "Every tool call as the model made it"
        + (
            "; tool results only for the episodes that were not correct, clipped."
            if not full
            else ", with its result, clipped."
        ),
        "",
    ]
    for r in records:
        question = questions.get(r["question"])
        folder = folders[r["run"]]
        lines += [
            f"### {r['question']} rep {r['rep']}: {r['verdict']}"
            + (f" (run `{r['run']}`)" if len(folders) > 1 else ""),
            "",
            f"> {question.text if question else r['question']}",
            "",
            f"{r['seconds']} s; {r['tool_calls']} tool call(s); tokens {json.dumps(r.get('tokens'))}"
            + (f"; error: {_scrubbed(r['error'], scrub)}" if r.get("error") else ""),
            "",
        ]
        original_rep = r["rep"] - next(o for f, o, _, _ in loaded if f.name == r["run"])
        stdout_file = (
            folder / "transcripts" / f"{r['question']}-rep{original_rep}.stdout.txt"
        )
        doc = (
            _goose_output(stdout_file.read_text(encoding="utf-8"))
            if stdout_file.is_file()
            else None
        )
        if doc is None:
            exported = (
                folder / "transcripts" / f"{r['question']}-rep{original_rep}.json"
            )
            if exported.is_file():
                try:
                    doc = {
                        "messages": json.loads(
                            exported.read_text(encoding="utf-8")
                        ).get("conversation", [])
                    }
                except ValueError:
                    doc = None
        with_results = full or r["verdict"] != "correct"
        for message in (doc or {}).get("messages", ()):
            for part in message.get("content", ()):
                kind = part.get("type")
                if kind == "toolRequest":
                    call = (part.get("toolCall") or {}).get("value") or {}
                    lines.append(
                        f"- **{call.get('name')}** `{_clipped(_scrubbed(json.dumps(call.get('arguments'), default=str), scrub), 400)}`"
                    )
                elif kind == "toolResponse" and with_results:
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
                        + _clipped(_scrubbed(" ".join(texts), scrub), clip).replace(
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
    return "\n".join(lines)


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
    runner.add_argument(
        "--arm",
        choices=["mcp", "mcp+python"],
        default="mcp",
        help="mcp: the toolkit's MCP server alone; mcp+python: also Goose's developer "
        "extension (a shell), so the model can compute over the Frames in Python, as a "
        "coding agent does (the diagnostic arm of the first row)",
    )
    runner.add_argument(
        "--budget-usd",
        type=float,
        default=20.0,
        help="stop starting episodes once every run under the runs root has cost this much at list price",
    )
    runner.add_argument("--only", help="question IDs, comma-separated")
    runner.add_argument(
        "--pilot",
        action="store_true",
        help="one question, one repetition: settle the harness",
    )
    runner.add_argument("--max-turns", type=int, default=30)
    runner.add_argument(
        "--retry-infra",
        type=int,
        default=1,
        help="how many times an episode that ended in a provider error is tried again",
    )
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
    commands.add_parser(
        "spend", help="the list-price spend of every run under the runs root"
    )
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
        help="render a run folder, or the several that make one row, as the transcript golden_transcripts/ keeps",
    )
    renderer.add_argument(
        "folder",
        nargs="+",
        help="a run folder; <folder>=<n> numbers its repetitions on from n (a row completed over several runs)",
    )
    renderer.add_argument(
        "--clip",
        type=int,
        default=1500,
        help="characters of a tool result shown at most",
    )
    renderer.add_argument(
        "--full",
        action="store_true",
        help="show tool results for every episode, not only the ones that were not correct",
    )
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
    if args.command == "spend":
        print(f"${spent(RUNS_ROOT):.2f} at list price over the runs under {RUNS_ROOT}")
        return 0
    if args.command == "recover":
        recover(args.folder)
        return 0
    if args.command == "rescore":
        rescore(args.folder)
        return 0
    if args.command == "transcript":
        text = transcript(
            args.folder, tuple(args.scrub), clip=args.clip, full=args.full
        )
        if args.output:
            args.output.write_text(text, encoding="utf-8")
            print(f"{args.output}: {len(text)} characters")
        else:
            print(text)
        return 0
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
