- **Recomputing a sample file's peaks keeps one progress bar to the end.**
  The request no longer reports a success of its own the moment it is handed
  to the file converter. That early success closed the progress bar before
  any detection ran, filed a success in the notification log, and left the
  converter's progress to open a second bar. The bar now stays open until the
  converter reports the outcome, with one toast and one row in the log. A
  request refused before it reaches the converter still reports its error.
