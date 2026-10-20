- **The Thermo reader writes trailer numbers alike on every server.** For
  servers that read raw files with the Thermo RawFileReader library
  (`MASCOPE_THERMO_DLL_DIR`). The library wrote each scan's trailer and
  status-log values in the server's regional format: a decimal comma where
  that format has one (fi-FI, de-DE), so `-71050473,7320` where other
  servers wrote `-71050473.7320`, and where .NET takes its culture data
  from ICU (sv-SE, nb-NO) the minus sign U+2212 as well. It now writes them
  in the invariant culture, with a decimal point and an ASCII minus, on
  every server. On a server whose format writes a decimal comma this
  changes text Mascope serves and stores: the trailer columns of
  `GET /api/sample/files/{id}/scans`, which the SDK's `get_scans` returns,
  read with a point from now on for every file, since they are read from
  the raw file on each request, and so does the `acquisition_params` a file
  records when it is converted. A file converted earlier keeps the text it
  recorded, and Mascope still reads the numbers in it. Servers whose format
  writes a decimal point see no change.
