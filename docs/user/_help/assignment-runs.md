Each **Assign peaks** launch creates a run &mdash; an immutable snapshot of the
sample's assignments, numbered in launch order and listed newest first. The
ledger shows the selected run; pick an older one to revisit it. A run marked
with &hellip; is still in progress.

A run also carries **who produced it**, shown as a chip beside its number:

- **Mascope** &mdash; computed by this deployment's own engine, which declines
  to assign a sample whose m/z calibration is present but unverified.
- **any other name** &mdash; a run computed elsewhere and published into this
  sample's history through the API, with the engine's version beside it. These
  are first-class: the ledger, the inspector and the batch overview read them
  exactly like an in-app run, and the newest completed run wins by default
  whichever engine produced it.

A published run calibrates on its own side rather than passing the m/z
verification this deployment applies, so it must declare what it calibrated
against &mdash; that declaration is the **calibration** chip, and hovering it
shows the disclosure. Such a run also shows no calibrated P(correct): that
number is this server's own judgement and an import cannot write it.

Tiers are only comparable between engines when the fit-score thresholds behind
them match, so each run records the bands it tiered with; hover the engine chip
to see them.
