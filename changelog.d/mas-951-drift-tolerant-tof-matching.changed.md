- **TOF calibration finds its calibrants however far the acquisition axis has
  drifted.** It no longer looks for each calibrant within 100 ppm of where the
  file's own m/z axis puts it. It searches 500 ppm either side and takes the
  axis on which the most calibrant ions agree within 15 ppm. A file whose axis
  had drifted past 100 ppm used to fit on too few lines, or on neighbouring
  peaks; it now fits on its calibrants. The record still measures the drift
  against the file's own axis, and says how the calibrants were found under
  `search`. The calibration dialog's refine window no longer limits a TOF
  search. TOF fits are made by version 3 of the calibration step.
