- **A raw Orbitrap file that switches polarity keeps the real peaks beside the
  other polarity's strong ions.** The satellite flag read both polarities of
  such a file as one spectrum. A weak peak of one polarity near a strong ion
  of the other could be taken as its side lobe and left out, and one
  polarity's strong ions could crowd the other's out of the peaks whose side
  lobes are looked for. Each polarity is now judged on its own, as each scan
  stream already was. A file that records one polarity is unchanged.
