- **An Orbitrap file whose scan events the reader misreads is refused
  instead of processed on noise.** OpenTFRaw derives each scan's analyzer,
  polarity, MS order, filter and profile calibration from its scan event,
  and a file whose events it reads from the wrong bytes gets noise in all
  of them: a public Q Exactive HF-X PRM run read as both polarities and
  none, MS orders 1 to 8 and five analyzers. On an Exploris or a Q Exactive,
  whose only analyzer is the Orbitrap, a scan read as any other analyzer is
  now refused, whatever made it misread, with the reason in the file's
  status. Opening a file costs what it did: the check reads the scan table
  every scan selection reads anyway. Every scan of the demo bundle's files
  and of two public Q Exactive Plus runs reads as the Orbitrap, as before
  (GitHub #2394).
