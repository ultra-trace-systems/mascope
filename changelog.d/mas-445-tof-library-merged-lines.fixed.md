- **A target ion's TOF lines start at the most abundant isotope.** The
  library a TOF sample is matched against grouped its isotopes from the
  lightest one. On a nitrogen ion the window started at the 15N line could
  take the 13C line and leave the 2H line a few mDa over it as a line of its
  own, though one TOF peak holds them all. It now groups them as the
  untargeted envelope match does: from the most abundant isotope, half the
  peak width either side. Ions created earlier keep their lines until they
  are created again.
