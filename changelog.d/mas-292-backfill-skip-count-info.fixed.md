- **The method-binding backfill no longer files a monitoring event for files it skips.**
  `backfill_method_bindings` counts the files converted before the
  scan-stream census and skips them by design; it now reports that count at
  INFO instead of WARNING, so a routine run raises nothing in error
  monitoring.
