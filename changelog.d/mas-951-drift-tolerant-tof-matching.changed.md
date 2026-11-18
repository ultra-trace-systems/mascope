- **TOF calibration finds its calibrants when the acquisition axis has drifted
  past 100 ppm.** It no longer looks for each calibrant within 100 ppm of where
  the file's own m/z axis puts it. It takes the axis on which the most
  calibrant ions agree within 15 ppm, looking 250 ppm either side of each
  calibrant, and 500 ppm when fewer than three ions agree within 250 ppm. A
  file whose axis had drifted past 100 ppm used to fit on too few lines, or on
  neighbouring peaks; it now fits on its calibrants. The record still measures
  the drift against the file's own axis, and says how the calibrants were found
  under `search`. The calibration dialog's refine window no longer limits a TOF
  search. TOF fits are made by version 3 of the calibration step.
