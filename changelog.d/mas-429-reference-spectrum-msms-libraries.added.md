- **Open MS/MS spectral libraries can be mirrored as reference spectra.**
  `mascope reference sync` and the backend's `reference_sync` script load
  MassBank (a MassBank-data release archive), MoNA (an MSP export) and GNPS
  (a library MGF) into a new `reference_spectrum` table, as versioned loads
  like the compound sources. Each spectrum keeps its precursor m/z, adduct,
  polarity, collision energy and its kind, centroids, the compound's InChIKey
  where the library gives one, and the licence its record states; MS1, MS3+,
  in-silico and deprecated records are left out and counted. `mascope
  reference status` shows a library's loads as spectra. The library reads them
  back `by_inchikey` and `by_precursor`, licence-filtered. Nothing scores
  against them yet.
