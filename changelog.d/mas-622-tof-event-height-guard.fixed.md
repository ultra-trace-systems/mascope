- **A small TOF cluster read as one ion no longer cuts every peak's
  signal-to-noise.** Since 1.16.0 one ion's height, measured over a TOF
  spectrum's empty stretches, floors the noise of every peak. When it was
  read from only a few runs, and one of them was a small cluster of ions
  too weak to be fitted, the floor was several ions high, and every peak
  over a dense baseline lost signal-to-noise, calibrants included. The
  height now floors every peak only once it is read from at least 30
  single ions; read from fewer, it floors only the peaks over an empty
  baseline, as before 1.16.0. A file's stored peaks keep the old values
  until its peaks are detected again.
