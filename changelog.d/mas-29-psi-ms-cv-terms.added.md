- **The batch spreadsheet names each sample's instrument in PSI-MS terms.**
  The Samples sheet ends with ten new columns: the instrument model the raw
  file names, and the model, ion source, mass analyser, detector and scan
  polarity as PSI-MS terms, each by name and accession (`MS:1003095`,
  `MS:1000484`, ...). Another tool can read them without knowing Mascope's
  own names for things. A value with no term, such as a TOF instrument's
  model, is left empty. The terms come from one table,
  `mascope_backend.vocabulary`, checked against PSI-MS release 4.2.6.
