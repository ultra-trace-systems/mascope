- **A TOF peak of a few ions over a half-filled baseline no longer reads as
  a strong peak.** The one-ion floor on a TOF peak's noise applied only
  where its baseline was mostly empty. Where the baseline was about half
  filled with stray ions, its spread was a small fraction of one ion, and
  a peak of three or four ions scored from about 15 to over 200: past TOF
  m/z calibration's gate of 10 and the weak cutoff of 3. One ion's height,
  measured over the spectrum's empty stretches, now floors every peak's
  noise, so such a peak scores about the ions it holds and is weak. A
  spectrum with no empty stretch keeps its values. A file's stored peaks
  keep the old values until its peaks are detected again.
