- **No toast for a result the screen already shows.** The composition search
  pane and the calibration dialog's preview fit render their outcome where it
  was asked for, so that outcome is no longer toasted on top; it is still kept
  in the notification log, and a failure is toasted as before. Opening the
  calibration dialog no longer toasts the preview fit, while applying the
  calibration still does. A composition search that finds nothing is logged
  as an info rather than a green success.
