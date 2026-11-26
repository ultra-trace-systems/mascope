- **Assign sample waits while the sample's run is in progress.** The
  button in the assignment bar, and the dialog's own **Assign**, stayed
  clickable while a run of the sample was queued, running or importing,
  and a second click was refused by the server as busy. Both now wait with
  a spinner and say why, and come back once the run ends, completed,
  failed or cancelled. A double click on **Assign** sends one request, and
  a run that fails or is cancelled no longer reads as running until the
  page is reloaded.
