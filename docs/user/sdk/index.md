# SDK & API

Load and analyse Mascope data from Python (notebooks or scripts).

```sh
pip install mascope_sdk
```

```python
from mascope_sdk import MascopeClient

mascope = MascopeClient(workspace="My Workspace")
peaks = mascope.load_peaks(dataset="My Dataset", batches="Uronium")
```

!!! tip "New to Python?"
    Follow the step-by-step [getting started guide](getting-started.md) — it
    walks you from installing an editor to running your first tutorial
    notebook in about 15 minutes.

Full reference, configuration, and tutorial notebooks: see the
[SDK readme on GitHub](https://github.com/ultra-trace-systems/mascope/blob/master/libraries/sdk/README.md).

## Built on the SDK: Peaky

Want the analysis without writing the notebook code?
[**Peaky**](https://github.com/ultra-trace-systems/peaky) is an AI-native
analysis toolbox built on the Mascope SDK — untargeted chemical-formula
assignment, time-series clustering, Van Krevelen plots, and PDF reports,
driven in plain language through a coding agent such as
[Claude Code](https://claude.com/claude-code). It is the SDK-powered,
AI-driven power-user path: a complement to the web app, not a replacement —
Mascope stays the system of record, and Mascope's scoring is the only scorer
in the loop, so results are reproducible and auditable.

<!-- TODO Phase 3: publish the SDK README content into this section as the single
source, or keep this page thin and deep-link. See the roadmap. -->
