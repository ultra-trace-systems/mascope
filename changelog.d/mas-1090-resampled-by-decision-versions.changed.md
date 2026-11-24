- **A re-run sample is behind its batch only when it read older decisions.** The
  batch's assignment status counted every sample re-run since the last pass as
  re-run, even one whose run read the pass's own decisions. It now compares the
  batch stage versions each sample's newest run recorded with the stages' versions
  now, so a sample re-run after the pass that read the current decisions no longer
  makes the batch look behind. A run from before runs recorded what they read
  still counts by its time.
