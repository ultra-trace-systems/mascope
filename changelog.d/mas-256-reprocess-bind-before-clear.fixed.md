- **A re-processed file that can no longer be bound keeps its samples.**
  Re-processing reset a file's m/z calibration, detected its peaks again and
  deleted its samples before the pipeline bound it. A file whose chemistry
  went while the batch waited for its turn, a chosen mode deleted or a token
  changed, was parked as needing a chemistry with nothing left. The pipeline
  now does all three only once it has bound the file, so such a file keeps
  its samples, its calibration and its peaks, and its status says so (#2347).
