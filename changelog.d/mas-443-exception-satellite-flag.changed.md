- **Orbitrap peak lists leave out the side lobes the instrument marked.** An
  Orbitrap marks each centroid it reads as a weak side lobe of a brighter
  peak (Exception). Peak detection of a raw Orbitrap file now counts, for
  every peak, how many of the per-scan centroids under it carry the mark, and
  flags a peak where more than half do as a satellite, which a peak list
  leaves out as before. A marked peak that sits on a 13C, 2H, 15N, 17O, 18O,
  34S, 37Cl or 81Br line of an unmarked peak is kept, as the instrument also
  marks real isotope lines where it expects a lobe. On the published demo
  raws about 1.5 % of the peaks above the noise leave the list, nearly all of
  them lobes the earlier satellite test missed. The summed spectrum and its
  centroids are unchanged. Files are re-read this way when their peaks are
  detected again.
