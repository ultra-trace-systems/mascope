- **The batch ledger export names each row's instrument type and intensity unit.**
  On a batch of Orbitrap and TOF files, *Export ledger (CSV)* and the SDK's
  `batch_peaks.members` wrote both instrument types' batch peaks with nothing to
  tell an Orbitrap row's peak heights from a TOF row's peak areas. Each row now
  carries `instrument_type` (`orbi` or `tof`) and `intensity_variable`
  (`sum_peak_heights` or `sum_peak_areas`), as the ledger route already did.
