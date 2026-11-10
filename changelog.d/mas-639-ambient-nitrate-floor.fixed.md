- **The chemistry detection audit no longer calls nitrate on a TOF's
  ambient-ion spectra.** The air's own nitrate can dominate a thin ambient
  spectrum, so it passed every guard and was stored as a nitrate call. A
  nitrate call on a TOF now needs its reagent ions at an area of 100 or more,
  far above the air's and far below any nitrate source's: rules version 3,
  stored with every call. Orbitrap files and other reagents read as before,
  and the audit still binds nothing.
