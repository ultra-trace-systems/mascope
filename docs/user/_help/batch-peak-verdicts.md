A **batch-level verdict** judges a batch peak's consensus formula once for the
whole batch: **confirm**, **reject** or **unsure**, with the evidence behind it,
exactly as a per-sample verification. It covers every sample in the batch whose
peak folded into this batch peak and that has no verdict of its own; a
per-sample verdict always wins, so verifying a sample records an exception. The
verdict is pinned to the formula judged &mdash; if the consensus later moves to
another formula it stays on record as stale until re-judged or retracted
&mdash; and it never enters the labelled record that confidence calibration is
fit on.
