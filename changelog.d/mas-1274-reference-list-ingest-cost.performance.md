- **Peak assignment no longer resolves the reference lists' lines where it
  only asks what they list.** With reference lists loaded, the batch stages
  and the batch search read each sample's lists for the compounds they name
  and the ions they hold, and every read resolved all of the lists' lines at
  the file's resolving power: about a second a read for the shipped lists,
  several times per sample in each pass after an arrival. They now read the
  ions alone, so a pass costs what it costs without the lists. Stage A, which
  matches the lines, reads them as before, and the assignments are the same.
