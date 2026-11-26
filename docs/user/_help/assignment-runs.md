Each **Assign sample** launch creates a run &mdash; an immutable snapshot of the
sample's assignments, numbered in launch order and listed newest first. The
ledger shows the selected run; pick an older one to revisit it. A run marked
with &hellip; is still in progress. While it is, **Assign sample** waits with
a spinner: a sample runs one assignment at a time. It is offered again once
the run completes. A run you launched from this page, or one in a batch pass
you ran, also lets go when it fails or is cancelled. Another person's run that
fails or is cancelled lets go when you move to another sample and back.

A run also carries **who produced it**, shown as a chip beside its number:

- **Mascope** &mdash; computed by this deployment's own engine, which declines
  to assign a sample whose m/z calibration is present but unverified.
- **Copy** &mdash; a run an earlier version of Mascope copied from another
  sample of the batch: the formulas are that sample's, and the fit, the m/z
  error and the tier were measured again against this one. Hovering the chip
  names the sample it was copied from. No new copies are made.
- **Batch ledger** &mdash; the sample has no run of its own: its peaks were
  folded into the batch peaks and are read back from there. Assign the sample
  for a ledger of its own.
- **any other name** &mdash; a run computed elsewhere and published into this
  sample's history through the API, with the engine's version beside it. These
  are first-class: the ledger, the inspector and the batch overview read them
  exactly like an in-app run, and the newest completed run wins by default
  whichever engine produced it.

An in-app run also names the **chemistry profile** it searched under, such as
*Bromide CIMS*. Hovering the name shows the context, the element grid and the m/z window
the profile set, and whether the profile was read off the sample's ionization mechanisms
or named for the run.

A published run calibrates on its own side rather than passing the m/z
verification this deployment applies, so it must declare what it calibrated
against &mdash; that declaration is the **calibration** chip, and hovering it
shows the disclosure.

Tiers are only comparable between engines when the evidence thresholds behind
them match, so each run records the bands it tiered with; hover the engine chip
to see them.
