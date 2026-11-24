- **A batch pass no longer searches again the batch peaks its last search
  found nothing for.** The untargeted search remembers each batch peak it
  searched in vain and leaves it out while nothing it read for that peak has
  changed: the settings, the library, the ionization chemistry, the engine and
  Mascope versions, the peak it searched on and whether the peak recurs across the
  batch. A sample left with nothing to search is not searched, so a pass after
  a fold-in searches little more than the new sample. Nothing is left out
  under a peak cap (`docs/user/how-it-works/peak-assignment.md`, "Searching
  untargeted").
