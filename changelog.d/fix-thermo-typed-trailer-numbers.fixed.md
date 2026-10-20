- **The Thermo reader summarises the MS2 scans of a file that names the
  collision energy otherwise.** For servers that read raw files with the
  Thermo RawFileReader library (`MASCOPE_THERMO_DLL_DIR`). Some instruments
  label the energy in a scan's trailer `HCD Energy:` or `HCD Energy eV:`,
  with no `HCD Energy V:`, as a Q Exactive Plus DDA run does. The MS2
  summary of such a file (`GET /api/samples/{id}/ms2/summary`) failed with
  `'HCD Energy V:' is not in list`. It now gives its groups no energy, as
  the default OpenTFRaw reader already did.
