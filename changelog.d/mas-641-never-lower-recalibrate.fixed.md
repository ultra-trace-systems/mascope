- **No edit lowers a batch's pending recalibration to a rematch.** Editing
  a target ion's match parameters, changing or deleting a target compound,
  copying samples into a batch and changing a batch's target collections
  flagged the batch `rematch` even when it waited for an m/z recalibration,
  so its status icon offered a rematch only. They now leave a batch at
  `recalibrate`, as the ionization-mode and target collection edits already
  did. A rematch forced on such a batch that fails puts it back at
  `recalibrate` rather than `rematch`.
