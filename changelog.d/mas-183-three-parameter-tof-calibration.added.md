- **TOF files are calibrated with a three-parameter model where it reads
  the axis better.** The square-root model leaves a bow of a few ppm across
  the calibrants on some TOFs. Each TOF fit now also tries Tofwerk's
  three-parameter mode 2 on the same calibrants, and keeps it when it
  predicts each calibrant ion, fitted without it, more than 0.5 ppm better
  on average. It is tried on calibrants of four ions or more. The
  calibration record says which model was kept and each model's held-out
  error, and the TOF calibration audit fits either model or the node's own
  choice (`TOF_AUDIT_MODELS=0,2,node`). Existing records apply as before.
