- **Sample lists no longer carry each TOF fit's calibration points.** A
  batch's sample list, which is not paginated, put every calibrant line of
  every TOF sample's fit in its rows. It now leaves `quality.points` out of
  each sample's `mz_calibration`, about 0.9 KB of a TOF row's 1.6 KB; the
  badge's counts, errors and span stay. `GET /api/calibration/mz_calibration`
  still answers the whole record, which the calibration dialog reads.
