- **A pass given no settings runs on the batch's, not on each stage's last run.** A
  pass of the batch stages started without settings used to run the search and the
  series on whatever their own last run on the batch recorded, so who pressed the
  search last decided the next pass. It now runs on the batch's saved settings.
  Settings sent with one request, the *Search untargeted* dialog's included, still
  override them for that pass, and its run records that they did.
