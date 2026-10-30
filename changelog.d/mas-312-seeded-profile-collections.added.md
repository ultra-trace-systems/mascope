- **The shipped chemistries bring their own calibrants.** Every start now
  seeds a calibrant and a diagnostic collection for each chemistry Mascope
  ships that has a reagent of its own - nitrate, 15N-nitrate, bromide,
  iodide, uronium and the two ambient ones - read off the reagent-ion
  library: the reagent ion and its own clusters, which give a TOF the three
  anchors its fit needs and an Orbitrap the one it needs, and every ion the
  reagent makes on its own. A file bound to such a chemistry is calibrated
  on them with no collection built by hand. They are global collections
  named after their chemistry (*Bromide, negative calibrants*, *Bromide,
  negative reagent ions*), so they appear in every collection list. A mode a
  site pointed at its own collection keeps it, a shipped collection a site
  edits is left as edited, and an unedited one follows later releases. A
  shipped chemistry files were already processed under takes its
  collections as a new version, so those files keep their results until
  they are processed again.
  Protonation, deprotonation and the ammonium chemistries have no reagent
  in the library and get none.
