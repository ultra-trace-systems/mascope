# Mascope through natural language: the AI analyst shell

- **Status:** design
- **As of:** 2026-11-04
- **Tracked in:** [AI analyst shell](https://linear.app/ultra-trace/project/ai-analyst-shell-2b0a6a7e8af8)
  (Linear, P-MAS-21, filed 2026-11-04: milestones 0 to 5 follow section 4;
  the five decisions of section 5 are MAS-659 to MAS-662 and MAS-665;
  milestone 0's test issues are MAS-666 to MAS-668, with MAS-669 asking
  for the owner's bench modules; the rest follow the decisions; since
  2026-11-06 also the open items of the AI layer project, P-MAS-12, which
  built phases 0, 1 and 3 of the layer under this and is closed)
- **What:** The shell a user drives Mascope through in natural language: any
  ACP agent runtime over the toolkit's MCP server, Goose by default, recipes
  that compute every number, and an evaluation matrix that admits cheaper
  models per task.

## Purpose

The long-term vision is that a Mascope user works mostly through natural
language. An agent runs Mascope's calculations, produces the charts,
dashboards and reports, and organises the data; the user clicks through the
web app less and writes fewer notebooks. One principle holds the whole
design together: **the agent runs calculations and never generates a
number.** Mascope computes every value, deterministically; the model chooses
what to compute, fills parameters and narrates.

[`ai_layer.md`](ai_layer.md) designed and largely built the layer under this:
`mascope_ai`, its MCP server, plan/apply and the golden fixture. This note is
about the two things that layer does not settle: the **shell** a user meets
the agent through, and the **cost** of the model that drives it. Today the
shell is a coding agent (Claude Code with the peaky skill), and the cost is a
frontier model, because the model does all the orchestration. The vision
needs a shell a scientist can install, and a model a site can afford, or
run on its own hardware with no internet route.

The note records (1) where the layer stands, (2) why the shell is built to
protocols and not to a runtime, (3) the levers that make cheaper models
reliable, (4) the phases, (5) the decisions, (6) the risks. It is a plan, not
a user guide; what a user meets is written in `docs/user` as each phase
lands.

> **Status (2026-11-04): proposal, nothing built.** Written for sign-off.
> The only model-in-the-loop measurement is one run of Sonnet 5.5 over the
> MCP server (section 1.1); no cheaper model has been tried.

---

## 1. Where the layer stands

### 1.1 What ships

The AI layer's phases 0 to 3 are done or nearly so
([AI layer](https://linear.app/ultra-trace/project/ai-layer-d957c301639f),
P-MAS-12). On `develop`:

- **`mascope_ai` is on PyPI** (`pip install mascope-ai`, MAS-611) with the
  MCP server `mascope-ai mcp`, the `Frame` handle type, `find` and
  `describe`, the four loaders with a row cap, the oracle (`query_by_mz`,
  `match_compounds` returning condensed verdicts) and a provenance manifest
  per session ([libraries/ai](../../libraries/ai/README.md)). No tool result
  passes 4 KB; every name Mascope returns comes back as inert JSON.
- **Writes exist behind plan/apply** (`mascope_ai.plans`, `mascope_ai.writes`,
  MAS-138): a write tool returns a `Plan`, and `apply` writes only with a
  confirm token the transport mints. The MCP-side `apply` tool is MAS-544, in
  review.
- **The golden fixture exists**
  ([`libraries/ai/tests/ai_golden.py`](../../libraries/ai/tests/ai_golden.py),
  MAS-131): 15 questions over the demo dataset in three classes (metadata,
  analysis, interpretation), each with a known answer and a solver through
  the tool surface alone. With a model in the loop it has run once:
  Claude Sonnet 5.5 over the MCP server, 2026-10-27, 15 of 15 right, the
  largest tool result 2.2 KB
  ([transcript](../../libraries/ai/tests/golden_transcripts/2026-10-27-sonnet-mcp.md)).
- **Agent traffic is attributable**: the `ai-agent` service name on every
  request a toolkit sends (MAS-127).

### 1.2 Peaky today, and why it needs a frontier model

[Peaky](https://github.com/ultra-trace-systems/peaky) is the working
prototype of the vision: a scientist asks a coding agent to assign a sample
or a batch, and gets a tiered workbook, figures, a PDF report. It runs as a
Claude Code skill over the SDK. Its principle is the one above: a
deterministic enumerator proposes candidates, Mascope scores them, local
arbitration picks, the model narrates (`ai_layer.md` section 1.4).

It needs a frontier model all the same, and the reason is instructive. The
model is the orchestrator: it holds the chemistry context across a long
session, sequences many tool and shell calls, writes ad hoc Python over
parquet, decides when a result is good enough, and interprets. None of
those is a number, so the principle is kept; but each is a step a small
model gets wrong more often, and a session of forty such steps compounds
them. Peaky's cost is not in what it computes. It is in how much deciding
is left to the model.

### 1.3 The agreed roles

Two decisions already taken bound this note:

- **Peaky is a skill on the AI layer, not a second interface**
  ([`peaky_mascope_boundary.md`](peaky_mascope_boundary.md); Linear
  [P-PKY-19](https://linear.app/ultra-trace/project/peaky-thinner-the-research-and-reporting-platform-81db9b25bea2)
  milestone D). Organising data is the toolkit's; peaky stays the
  interactive analysis and reporting tool over either engine's ledgers, and
  its enduring capabilities are catalogued (D2) and kept through the
  thinning. Section 2.5 proposes the next step of that agreement: the
  shell this note designs is Peaky, by name and by repository.
- **The in-app agent is `ai_layer.md` phase 4** (MAS-139): a chat panel in
  the web app over a sandboxed transport, with per-session tokens, approval
  cards and cost attribution. Nothing of it has started.

This note does not move either. It adds the desktop shell between them, and
narrows phase 4 to what a panel does well (section 2.3).

---

## 2. The shell: protocols, not a runtime

### 2.1 Goose, as of spring 2026

[Goose](https://github.com/block/goose) is Block's open-source agent,
Apache-2.0, since 2026 a project of the Agentic AI Foundation under the
Linux Foundation. Its extensions are MCP servers; its recipes are YAML
files bundling instructions, required extensions and parameters, which the
desktop can save and a CI job can run; its providers include
OpenAI-compatible local endpoints, so it runs against a model on a site's
own hardware. All of that fits Mascope: the MCP server is already the
integration point, and the air-gap requirement of the autonomous-operation
discussion is satisfiable.

Its internals are in motion, and the plan has to respect that. Goose's
client/server roadmap of 2026-03-06
([discussion 7697](https://github.com/block/goose/discussions/7697)) and its
blog of 2026-04-08
([goose ACP and new TUI](https://goose-docs.ai/blog/2026/04/08/goose-acp-and-new-tui))
say:

- Goose ships three Rust binaries around one agent core: the CLI
  (in-process), **`goosed`** (a bespoke REST and SSE server of 103 endpoints,
  which the Electron desktop spawns), and **`goose-acp-server`**, which
  wraps the agent in the **Agent Client Protocol** (ACP), the protocol Zed
  and the JetBrains IDEs drive it through.
- The roadmap is **one binary, one protocol**: ACP for every client, a
  `goose serve` exposing it over HTTP and WebSocket, and the removal of
  `goosed` and the old CLI in a "consolidation" phase. Goose-specific
  features (configuration, recipes, scheduling) become ACP custom methods.
- The Electron desktop is being **rewritten in Tauri on ACP**. A TypeScript
  SDK wrapping the ACP server exists in Goose's `ui/acp` and is proposed
  for publication; a Python equivalent is proposed with no date.

Two consequences follow. Anything built on `goosed` is built on a surface
its owners intend to delete. And once a client speaks ACP, Goose is one
runtime among several: Claude Code, Gemini CLI and others are reachable
over ACP natively or through adapters.

### 2.2 Build to ACP and MCP

```
   a scientist
        |
   shell, "Peaky": Goose Desktop + the Peaky bundle (milestone 3),
                   Peaky's ACP client (milestone 4)
        |  ACP: sessions, prompts, tool-call approval, custom methods
   agent runtime: Goose by default; any ACP agent in its place
        |  MCP                          |  model API
   mascope-ai mcp                       Anthropic API / OpenAI-compatible
     + Peaky's skills and recipes         local endpoint (air-gapped)
        |
   mascope_ai  ->  mascope_sdk  ->  Mascope's API (the user's own rights)
```

What Mascope owns and invests in is the bottom half: the toolkit, the MCP
server, every recipe step's implementation and the evaluation matrix. The
top half, the shell with its skills, recipe compositions and bundle, is
Peaky's (2.5). Every ACP runtime consumes both unchanged, so the runtime is
a choice, not a dependency, and the choice can change when Goose's
consolidation lands or another runtime proves better on the matrix. The design's invariants
(`ai_layer.md` section 3) are unchanged: handles not tables, the oracle as
the only source of a mass or a score, approvals minted by the transport.
The shell adds one: **a model writes prose and fills enumerated
parameters; code produces every number and every figure** (section 3).

Goose is the default runtime for three reasons that are all about
constraints, not features: Apache-2.0 beside Mascope's Apache-2.0; local
providers, so a site with no internet route runs it; and its own direction
toward ACP, so the client we might build later is the client it is building
for itself.

### 2.3 Three surfaces, one layer

| Surface | Who | What it is for | State |
|---|---|---|---|
| A coding agent (Claude Code, Goose CLI) with `mascope-ai mcp` and the skills | developers, power users | everything, including research passes peaky does and Mascope does not | ships |
| Peaky, the desktop analyst: Goose Desktop with the Peaky bundle, later Peaky's ACP client | scientists | analysis on request, reports and dashboards from recipes, data organisation behind approval | this note, milestones 3 and 4 |
| The in-app panel | every user | metadata questions, "what is this peak", organising data with plan/apply | `ai_layer.md` phase 4, narrowed: milestone 5 |

The user's own assessment holds: a panel inside the web app covers simple
tasks well and open-ended analysis badly. A panel has no shell to run
Python in, no place to keep a session's parquet, and a per-request cost
model that punishes long sessions. So the panel is scoped to what resolves
in one or two tool calls, and the desktop carries the rest. Both are
transports over the same toolkit, as the layer's design intended.

### 2.4 Air gap

A site that runs Mascope with no internet route runs the model beside it:
an OpenAI-compatible endpoint (vLLM or similar) on a GPU host, weights
pinned by hash, the endpoint named in the provider preset. The desktop
shell talks to Mascope's API and to that endpoint, nothing else. Which
model is good enough for which task is the matrix's answer (section 3.4),
not a guess, and a site that cannot run a model large enough for a task
class loses that class, not the shell. [`isolated_deployment_plan.md`](isolated_deployment_plan.md)
owns the server side of this.

### 2.5 Peaky is the shell

> **Proposed 2026-11-04, pending the peaky owner's agreement** (decision
> 5.5, MAS-665). The split below is this note's position; the two Linear edits it
> names are made at the owners' meeting, not before.

Peaky's owner keeps peaky, and the two owners' idea is that the AI frontend
is *called* Peaky while every computation and every reusable piece moves to
Mascope. That is the boundary agreement of 2026-10-30 taken one step on,
and it changes this note's names, packaging and ownership, not its
architecture. The "Mascope bundle" of milestone 3 is Peaky. The end state
of the thinning
([`peaky_mascope_boundary.md`](peaky_mascope_boundary.md) section 8)
gains a definition: when the register is empty, what remains in the peaky
repository is the shell.

Where each thing lives:

| Thing | Home | Why |
|---|---|---|
| The toolkit, the MCP server, `Frame`, plan/apply | Mascope, `libraries/ai` (`mascope-ai` on PyPI) | computation and the API client, versioned with the server |
| Recipe step implementations: normalisation, background, Van Krevelen, clustering, report rendering | Mascope (`mascope_ai`, `mascope_tools`, `mascope_viz`) | computations; every rule a result rests on is Mascope's |
| Figures: the Figure spec, the chart kinds, the renderers | Mascope (`mascope_viz`) | step implementations; peaky's figure modules move as chart kinds, mapped in [`visual_digest.md`](visual_digest.md) section 3; one spec that the shell, the web app and the notebooks all render |
| Recipe compositions: which steps, which parameters, the narrative template | Peaky, as data files run by the toolkit's `run_recipe` | the product; a composition carries parameters, not rules; guarded by peaky's golden runs (PKY-33) |
| Saved dashboards and report recipes a user authors in conversation | the server, per user or per workspace: `visual_digest.md` decision 8 (MAS-218), drafted, not decided | a saved recipe is data a user owns, as a collection is; the shipped catalogue stays Peaky's |
| The base skill that documents the tool surface (`mascope_ai/SKILL.md`) | Mascope, shipped in `mascope_ai` | it describes the tools and changes with them |
| Domain skills: reagent chemistry, which recipe for which question, report style | Peaky | the science guidance its owner authors |
| The Goose bundle: extension registration, provider presets, the admitted model per class, `peaky setup`, the installer | Peaky | the shell |
| The golden fixture and the model matrix | Mascope, `libraries/ai/tests` | the admission test of the toolkit; a recipe's own questions join peaky's golden runs |
| The ACP client (milestone 4) | the peaky repository | the shell |
| The in-app panel (milestone 5) | Mascope's web app; its branding is the owners' call | a web-app component over the toolkit |
| Chemistry as data; the mass-QC reference ions and rosters | Mascope (PKY-35); peaky (A12) | unchanged from the boundary note |
| What a user meets of the shell | peaky's own docs; Mascope's `docs/user` says what Mascope serves and links to them | P-PKY-19 already says so |

The rule behind the table: **a skill or a program that describes a tool
ships in the package that provides the tool, so with Mascope; one that
composes tools into an analysis, or guides the science, ships with
Peaky.** A tool here is one operation an agent calls by name over MCP,
such as `load` or `chart`, with typed inputs and outputs; it is not a
program a person runs. The package that provides Mascope's tools is the
toolkit, `mascope_ai`, and the set of tools it offers is its tool surface.
The install line becomes `pip install peaky`, `peaky setup` checks Goose,
the toolkit and the AI Agent token, and peaky pins `mascope-ai` the way it
pins the SDK, under PKY-4's parity rule.

Three things in the existing plan need an edit:

1. **Decision 5.4 becomes a joint one.** Step implementations and every
   rule stay in `mascope_ai`; the composition of steps into a recipe is
   Peaky's data, run by the toolkit's runner so the manifest is kept.
2. **PKY-14's wording.** It says peaky is "not a second natural-language
   interface". The guard was against two competing interfaces; under this
   framing there is one, and it is Peaky. `mascope-ai` is a library and a
   server, not an interface.
3. **Milestones 3 and 4 are Peaky-team deliverables.** The Peaky team
   joins the project, as P-PKY-19 lists both teams, and their issues are
   filed on PKY. Milestones 0 to 2 stay Mascope's; 0 is run jointly.

Two risks. Users meet two names: Peaky's docs say in their first line that
it is Mascope's AI analyst and computes nothing itself. And the framing is
honest only under the "thinner is not less capable" rule: the owner keeps
the product and its name, Mascope gets the computations, and no capability
is lost because every one is reachable as a recipe.

---

## 3. Cheaper models: the levers

### 3.1 What the evidence says

Public measurements of small and open-weight models on tool use agree on
one shape: they match proprietary models on single tool calls and fall
behind when calls must be sequenced or run in parallel (the Berkeley
Function Calling Leaderboard's multi-turn and parallel categories), and
they lose track of early tool results after a few steps. Decomposing a task
into smaller steps buys reliability for a weak model at the cost of more
calls, and is overhead for a strong one (a 2026 Politecnico di Milano
benchmark of local models over MCP,
[arXiv 2608.26199](https://academy.dair.ai/papers/benchmarking-ai-agents-for-hardware-design-automation-via-mcp-tool-calling-2608.26199)).
Vendor and blog rankings of which local model is best disagree with each
other; none of them is a number to plan on.

There is one in-house measurement, and it is the prior the matrix starts
from. Peaky's owner evaluated 13 open-weight models on 26 scored tests in
seven families between 30 October and 2 November 2026, all through hosted
OpenAI-compatible providers: database reads with traps, writes through an
approval gate, a twelve-scenario customer-data safety suite over synthetic
customers, long analysis reports with a null control, question answering
with questions that must be declined, figures drawn by code from a
model-written specification, and an assignment review with planted errors.
Every test had a code-computed reference and a deterministic scorer, the
scorers were validated with scripted careful, careless and fabricating
agents before any model ran, the decision rules were pre-registered and
hashed, and every failing group was audited by a second agent asked to
refute the first. Its finalist is a 120B mixture-of-experts model with 12B
active parameters, served in bf16 and then shown to hold at 4-bit with the
provider pinned and reasoning on; the 27B to 30B models were dropped after
the first rounds. Its record is internal; its shapes are what milestone 0
ports onto the demo dataset (section 4), and its finalist is where the
open-weight column starts.

The shape matters more than the ranking. It says the way to a cheaper model
is not a better prompt. It is a surface on which the model makes fewer,
simpler decisions.

### 3.2 The levers

1. **Coarse task tools beside the primitives.** A small model that must
   chain `find`, `load_peaks`, Python and `derive` to normalise a batch
   fails more often than one that calls `normalise(batch, reagent_ion)`
   once. A skill that is a procedure today becomes a tool with enumerated
   parameters, implemented deterministically in `mascope_ai`, returning a
   handle or a few numbers. The primitives stay for the open-ended case.
2. **Recipes as the unit of work.** Peaky's batch report, Van Krevelen,
   clustering and PDF are deterministic programs with parameters. A recipe
   is such a program in `mascope_ai`, with a manifest, run by one tool
   (`run_recipe`); its Goose recipe YAML is a thin wrapper that names the
   program and its parameters. The model fills the slots and writes the
   commentary. The sequence of steps is fixed by code, so a run is
   reproducible whatever the model.
3. **Closed output spaces where the model decides.** Reagent, polarity,
   template, which candidate to narrate: enumerations and structured
   output, never free text parsed afterwards. This is the determinism
   position of the autonomous-operation discussion applied to the analyst
   tier: choices by constrained decoding or option scoring, not sampling.
4. **Context hygiene, already there.** Results under 4 KB, inert names, no
   tables in context. Add compaction between recipe steps, so a long
   session is a series of short ones.
5. **Routing by task class.** `ai_layer.md` section 3.7 dropped model
   routing for v1 because the harness was not ours. With a shell it is: a
   cheap model for metadata, narration and parameter filling; a stronger
   one for open-ended analysis and interpretation; the table filled by the
   matrix.
6. **Numbers from code, prose from the model.** A report template is
   rendered deterministically (the `mascope_viz` artifacts `chart` and
   `table`, MAS-237 and MAS-553) from Frames; the model writes only the
   text between the figures, and the manifest records which values are
   Mascope's, which the code computed and which the model inferred, as the
   toolkit's `answer` already does.

### 3.3 What stays deterministic, and what does not

The numbers, the figures, the recipe's steps and the provenance manifest
are deterministic: the same inputs give the same values and the same files.
The words are not: the same question may be narrated differently by the
same model on two days, and differently again by another model. That is
the stated contract: **same numbers, possibly different words**, and the
manifest is what a reader checks the words against. A claim in the prose
that no manifest value supports is the failure mode to measure (section
3.4), as unverifiable claims per hundred decisions was in the
autonomous-operation discussion.

### 3.4 Admission by evaluation: the model matrix

The golden fixture is the acceptance test of the tool surface; here it is
also the admission test of a model. Run `ai_golden.py prompts` through each
candidate runtime and model, one conversation per question, and record per
question class:

| Measure | Why |
|---|---|
| correct, wrong, missing, sloppy, per question | correct is the only measure that matters to a user; wrong (stated but false) is the fabrication measure, kept apart from missing (abstained); sloppy is an instruction not followed |
| tool calls per question | the sequencing load the model carried |
| largest and total result bytes | whether the surface kept its context promise |
| cost per question | what the site pays, or the GPU-seconds it spends |

A model is admitted to a task class when its correct rate on that class
clears the threshold decision 5.3 sets and its wrong rate is at or under
the one that decision allows; a decline question answered with a value is
wrong. Each question runs at least three times per model, since a single
conversation measures nothing and five repetitions detected only large
differences in the owner's evaluation. A provider error (a 429, a timeout)
is an infrastructure failure, excluded and counted apart. The thresholds
are pre-registered: a pilot on one model settles the harness, then the
thresholds are written into decision 5.3 and hashed before the full matrix
runs, so they are not fitted to its results. The matrix is re-run whenever
the surface changes (milestones 1 and 2) and whenever a model is added,
and its table lives in this note. The first three columns are a frontier
model, a cheap hosted model and an open-weight model a site could run
itself; the fixture's rule stands: only the published demo dataset in a
transcript, never a token.
A row is produced by one command, the driver under
`libraries/ai/tests/golden_transcripts/matrix/`, whose recipe
`golden_transcripts/README.md` records ("Running a row of the matrix"):
the checker's dry run as the gate, Goose as the runtime with the MCP
server as its extension, keys in an env file on the host, one folder per
run with its header, pre-registration hashes, transcripts and the row.

**The first row (2026-11-05).** The open-weight finalist of the owner's
evaluation (3.1), served in bf16 by a hosted provider, through Goose 1.57.0
with the MCP server alone (16 tools, before `reduce`), on demo bundle 1.4.3,
three repetitions of the 22 questions. The record is
`libraries/ai/tests/golden_transcripts/2026-11-05-nemotron-3.5-super-vl-goose.md`;
a provider outage took most of the third repetition, which ran the next day
and is rendered into the same record.

| Class | Episodes | Correct | Wrong | Missing | Infra (excluded) | Median calls | Median s | USD |
|---|---|---|---|---|---|---|---|---|
| metadata | 24 | 24 | 0 | 0 | 0 | 4 | 11 | 0.25 |
| analysis | 17 | 3 | 2 | 12 | 1 | 13 | 1464 | 2.64 |
| interpretation | 9 | 9 | 0 | 0 | 0 | 11 | 48 | 0.40 |
| decline | 15 | 15 | 0 | 0 | 0 | 6 | 31 | 0.60 |

Three classes are clean. The analysis class is not, and its transcripts
say why: through MCP alone a Frame handle has no computation path. The
model declined the top-ions question "because no tool returns aggregate
statistics", read the apex off the first row of a handle's head, named a
background sample as the brightest, and ran the rest into the wall clock
looking for a way to compute (A1 missing, infra, wrong; A2 missing three
times; A3 missing three times; A4 correct, missing, missing; A5 missing,
correct, correct; A6 wrong, missing, missing). The twelve missing are nulls
where values were asked, read as the abstention they are, and declines of
answerable questions; the two wrong are the background sample named as the
brightest and a decline the model wrapped in a JSON string, its own JSON
error. Two side arms on the same model, stack and questions, recorded
beside the row:

| Arm | Questions | Correct | Wrong | Missing | Infra |
|---|---|---|---|---|---|
| MCP with `reduce` (PR #2983), 2 repetitions | A1, A4, A6 | 4 (A1 2/2, A4 2/2) | 0 | 2 (A6: reached the grouped medians, then the provider went silent) | 0 |
| MCP plus a Python shell (Goose's developer extension), 2 repetitions | A1 to A6 | 8 | 3 (A3 twice: the peak reported without its background subtracted; A2 once) | 0 | 1 |

So the class fails for want of a computation path, not of reasoning, which
is what milestone 1's coarse tools are for: `reduce` first (merged), then
what A6 (a median per ion with its mass error), A3 (a value above a
background) and A2 (a ratio of two lines) still lack. Two cautions for the
next row. The recipe of this row told the model to call `reduce`, which its
toolkit did not have; the three clean classes do not depend on it, the
analysis class was told of a tool it could not call. And the provider's
speed decided the wall clock: the same episodes took minutes in the
afternoon and seconds in the evening, and an overload (529) took a
repetition, so a provider error is retried once and excluded, never scored.

**The fixture needs traps before the matrix means anything.** A matrix of
questions whose right answer is always a value cannot tell a model that
fabricates from one that declines. Milestone 0 therefore adds, before any
model runs, a `decline` class whose right answer is "not available" (a
batch that does not exist, a workspace the token cannot see, an m/z
outside the acquired range, a quantity no Frame carries), scorer dry runs
with a fabricating and an empty solver that the checker must fail, and
the plan/apply safety scenarios as contract tests of the toolkit (a
compound list an update would replace, a cross-workspace move, injected
text in a description, a denied approval, a scoped delete, a false success
after a failed write), each with a collateral snapshot of every object so
that any change outside the scenario's allowed set fails the test. The
shapes are the owner's evaluation's (3.1); two of its findings are
Mascope API defects filed on GitHub, the collection update that replaces
the compound list its schema says it adds to, and the batch update that
requires the collection list and detaches on an empty one.

**The open-weight column may be hosted for the trial.** What defines the
column is self-hostability, not where the trial runs: weights published on
Hugging Face, a licence a customer may use commercially, and a size one GPU
host serves, which the owner's evaluation puts at a 120B mixture of
experts at 4-bit, one 80 GB card, rather than the 27B dense models it
dropped. The column starts from that evaluation's finalist. A hosted copy
of such a model through an OpenAI-compatible provider Goose already knows
(OpenRouter, Nebius Token Factory) is a fair proxy for milestone 0;
OpenRouter's catalogue lists over a hundred tool-capable models with a
Hugging Face repository behind them. The serving side differs from what a
site runs, in precision, tool-call parser, chat template and whether
reasoning is on, and those are where models break: the same provider
served one family in bf16 and another in fp4, and a provider default of
reasoning off voided one of the owner's runs. So milestone 3 re-runs the
admitted model on the self-hosted stack before it is written into a
preset, and the transcript's header records the provider, the model id,
the weights it names, the precision served and the reasoning setting; on
OpenRouter the provider is pinned in the request with fallbacks off, since
one model id routes across providers with different quantizations. The
trial runs on the demo dataset, so a hosted provider is fine for it; a
session over a site's own data never goes to a hosted provider without
that site's agreement, which is the air-gap rule of 2.4 restated.

---

## 4. Phases

Each phase lands on its own and leaves the system shippable. Milestone 0
changes nothing in the toolkit: it grows the tests, then tests the premise
that cheaper models work on a well-shaped surface before anything is built
on it.

### Milestone 0 - The runtime trial and the model matrix

- First, the fixture's traps (3.4): the `decline` question class, the
  scorer dry runs, and the plan/apply safety scenarios as contract tests
  with a collateral snapshot. The owner's bench modules are the source
  where they can be read.
- Register `mascope-ai mcp` as a Goose extension; run the golden prompts
  against the demo stack through Goose with a frontier model, a cheap
  hosted model and an open-weight model, the last hosted through a
  provider Goose knows and pinned as 3.4 says, starting from the owner's
  finalist. At least three repetitions per question; the thresholds
  pre-registered after a one-model pilot. Transcripts under
  `libraries/ai/tests/golden_transcripts/`, the matrix of section 3.4 in
  this note.
- Wrap one golden question as a Goose recipe, to learn the format and its
  limits.
- Verify: the fixture's dry runs fail what they should; three transcripts
  and the matrix; a list of what each model missed and why (tool choice,
  sequencing, context loss, prose, fabrication on a decline question),
  which is milestone 1's input.

### Milestone 1 - A surface small models can drive

- Coarse task tools beside the primitives, their descriptions tuned on the
  misses; `chart` and `table` served (MAS-553); a `run_recipe` tool;
  compaction between recipe steps; the fixture extended with the questions
  that failed.
- Verify: the matrix re-run; the cheap and local columns improved on the
  classes the tools target; nothing new passes a table.

### Milestone 2 - Recipes: numbers from code, prose from the model

- The first recipes from peaky's catalogue (P-PKY-19, D2): the batch report
  first, then normalisation to a reagent ion, background subtraction, the
  Van Krevelen, the time-series clustering. Each step a deterministic
  program in `mascope_ai` with a manifest; each composition Peaky's data
  file with golden questions of its own (2.5, decision 5.4); its Goose
  YAML a thin wrapper.
- Verify: each recipe's output is byte-identical across two runs and two
  models, prose excepted; its golden questions pass on the admitted models.

### Milestone 3 - The desktop MVP: Goose Desktop and the Peaky bundle

- No UI code of ours: Goose Desktop plus the Peaky bundle (2.5), which is
  the MCP server, the skills, the recipes, provider presets (Anthropic API;
  a local endpoint) and a `peaky setup` that checks Goose, the toolkit and
  the AI Agent token as `mascope-ai setup` does. The admitted model per
  task class, re-run self-hosted (3.4), is written into the presets.
- Docs: peaky's own docs on what a user meets; Mascope's `docs/user` on
  what it serves, linking to them; `docs/maintaining.md` on the local
  endpoint an operator runs.
- Verify: a scientist installs it from the docs alone and runs the batch
  report on the demo dataset on an admitted cheap model.

### Milestone 4 - Peaky's ACP client

- Only on a UX gap the MVP shows, and never on `goosed`: a Tauri client
  speaking ACP, in the peaky repository, with approval cards that render a
  `Plan` and mint its token, artifact panes for charts and reports, a
  session browser over the manifest. Goose stays the default runtime
  behind it.
- Gated on Goose's consolidated binary (`goose serve`) and a published ACP
  SDK (decision 5.1 says what to do if neither arrives).

### Milestone 5 - The in-app panel

- `ai_layer.md` phase 4, narrowed to metadata questions, the oracle and
  plan/apply with approval cards. Its prerequisites are the layer's:
  per-session tokens, the sandbox decision (MAS-141), cost attribution
  (MAS-140).
- The panel renders `chart` and `digest` artifacts with the web app's
  Figure spec renderer (`visual_digest.md` section 2.8), the component a
  saved dashboard uses, so a figure looks the same in the panel, the
  shell and a report.
- Whether its backend is the same ACP runtime as the desktop, `goose serve`
  behind the backend with one sandboxed session per user, is decided here
  with milestone 3's usage in hand. The security questions are real: an
  agent runtime with a shell is not a web-app component, and a shared
  runtime must never share a session, a token or a scratch directory
  between users.

---

## 5. Decisions

Each is a Linear Decision issue in the project; the leaning is recorded so
the plan is actionable, and the issue is closed by writing the decision into
its description.

1. **ACP-first.** Build every shell to the Agent Client Protocol and every
   integration to MCP; never to `goosed` or to another runtime's private
   API. *Leaning: yes.* The fallback if Goose's consolidation stalls or no
   SDK is published: the MVP (milestone 3) needs neither, and milestone 4
   waits or uses a runtime that does speak ACP.
2. **Goose as the default runtime.** Ship the bundle for Goose Desktop and
   name Goose in the docs, or ship a runtime-agnostic bundle and name none.
   *Leaning: Goose by default, the bundle usable from any ACP or MCP host.*
   The reasons are its licence, its local providers and its ACP direction;
   the cost is tracking a project in mid-migration.
3. **Admission thresholds.** The correct rate a model must clear per task
   class, and the wrong rate it may not exceed. *Leaning: 100 % correct on
   metadata and decline questions, the frontier model's own rate on
   analysis and interpretation, one wrong answer on a decline question
   disqualifies, a missing one does not.* Pre-registered, not fitted: a
   pilot on one model settles the harness, then the thresholds are written
   and hashed before the full matrix runs.
4. **Where recipes live** (a joint decision since 2.5). The step
   implementations, and every rule a result rests on, in `mascope_ai`; the
   composition of steps into a recipe, with its parameters and narrative
   template, as Peaky's data, run by the toolkit's `run_recipe` so the
   manifest is kept; a Goose recipe YAML only a thin wrapper naming the
   composition. *Leaning: that split.* A step in the library is testable in
   pytest, runs from any host and from the golden fixture, and carries the
   manifest; a composition in Peaky is the product its owner shapes, and
   peaky's golden runs guard it; a Goose recipe alone would tie the
   deterministic core to one runtime's instruction text.
5. **Peaky is the shell** (joint, 2.5). The AI frontend is called Peaky
   and lives in the peaky repository and package; every computation and
   reusable piece is Mascope's; milestones 3 and 4 are Peaky-team
   deliverables. *Leaning: yes*, with the rule of 2.5 (what describes a
   tool ships in the package that provides the tool, so with Mascope; what
   composes tools or guides the science ships with Peaky) and the two
   Linear edits 2.5 names.

Open questions the phases answer rather than decide now: which GPU host
runs the self-hosted re-run of milestone 3; whether Goose's ACP custom
methods for recipes are stable enough to depend on; whether the in-app
panel carries the Peaky name; and whether the panel's backend is the
desktop's runtime (milestone 5).

---

## 6. Risks and limits

- **Goose is mid-consolidation.** The facts of section 2.1 are from March
  and April 2026; check Goose's state before each milestone that touches
  it. `goosed` will vanish; the ACP custom-method surface for recipes and
  configuration is not yet documented or versioned; ACP's Streamable HTTP
  transport is a draft.
- **Prompt injection is still live.** Sample, batch and dataset names flow
  into the model's context through `find` and `describe`. The server
  escapes them and the skills say to read them as data; a recipe must not
  interpolate a name into an instruction either.
- **A cheap model is admitted per class, not in general.** A site whose
  local model clears metadata but not interpretation gets a shell that
  answers "which batches" and declines "what is this peak". The decline is
  a feature; silently routing the question to a model that fails the
  matrix is the failure.
- **Words are not reproducible.** Section 3.3 states the contract; the
  docs must state it too, or a user will read two narrations of the same
  numbers as two results.
- **The owner's evaluation is one study type.** Its long tasks are all
  variants of one headspace study on one reagent chemistry, its screens
  were designed on the same data, and five repetitions detect large
  differences only. It is the prior, not the matrix; the matrix is run on
  the demo dataset's own questions.
- **Peaky's capabilities move at peaky's pace.** Milestone 2 depends on
  P-PKY-19's catalogue and on peaky's reports reading either engine's
  ledger (PKY-13); a recipe written before the catalogue risks encoding a
  rule the thinning is about to move.

---

## 7. Related

- [`ai_layer.md`](ai_layer.md): the toolkit, the MCP server, plan/apply, the
  golden fixture, phase 4.
- [`peaky_mascope_boundary.md`](peaky_mascope_boundary.md): peaky's role and
  the move protocol; its D2 catalogue is milestone 2's source.
- [`visual_digest.md`](visual_digest.md): the `mascope_viz` artifacts
  `chart` and `table` wait for; its section 2.5 maps peaky's figures onto
  the catalogue under this note's framing, and its section 2.8 and
  decision 8 carry dashboards, reports and the app.
- [`isolated_deployment_plan.md`](isolated_deployment_plan.md): the server
  with no internet route that a local model endpoint sits beside.
- The autonomous-operation discussion of 2026-10-26 (no note yet): the
  decider tiers and the determinism position this note reuses for the
  analyst tier.
- The peaky owner's open-model evaluation of 30 October to 2 November
  2026 (internal record, not in this repository): the prior of section
  3.1 and the source of milestone 0's traps, dry runs and safety
  scenarios.
