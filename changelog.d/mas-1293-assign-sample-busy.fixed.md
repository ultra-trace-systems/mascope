- **Assign sample waits while the sample's run is in progress.** The
  button in the assignment bar, and the dialog's own **Assign**, stayed
  clickable while a run of the sample was queued, running or importing,
  and a second click was refused by the server as busy. Both now wait with
  a spinner and say why, and come back once the run completes. A run you
  launched from this page, or one in a batch pass you ran, is also noticed
  when it fails or is cancelled; another person's run that fails is noticed
  only when you come back to the sample. A double click on **Assign** sends
  one request, and a refusal as busy reads the sample's runs again, so the
  run that holds it shows.
