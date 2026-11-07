- **The sample and batch isotope aggregates now carry each line's stored
  signal-to-noise.** The query read it, but the frame dropped the column
  before it was returned, so the isotope aggregate routes had no
  `signal_to_noise` and the v2 match score
  (`MASCOPE_MATCH_SCORE_VERSION=2`) scored every ion read from the database
  in its no-SNR mode. With the default v1 score no score changes.
