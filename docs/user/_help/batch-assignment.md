**Assign batch** runs the batch's pass: it folds every sample again where the
ledger cannot be a starting point, then runs the batch stages in their fixed
order, in four phases &mdash; *Gather samples*, *Recognise source ions*, *Find
compositions* and *Cross-check across the batch*. Beside it the bar says where
the batch stands: *Known compositions only* (folded at ingest, no pass yet),
up to date, behind and why, running, or stopped at a stage. The button is
filled when the batch needs a pass: **Update assignment** after new samples,
**Re-assign** after anything else, **Retry** from the stage a pass stopped at.
**Details**, beside the status, shows the pass's stages under their phases,
each done, running, failed, out of date or not run, with a one-line result,
the change it made to the tier counts and the settings it ran under. Its menu
edits the batch's own settings (**Assignment settings…**), folds every sample
first (**Re-assign from scratch**), opens the **Run history** and exports the
ledger. Saving the settings starts nothing: the batch turns behind, and the
next **Re-assign** runs on them.
