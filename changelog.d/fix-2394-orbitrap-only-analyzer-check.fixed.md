- **An Orbitrap file whose scan events the reader misreads is refused
  instead of processed on noise.** OpenTFRaw derives each scan's analyzer,
  polarity, MS order, filter and profile calibration from its scan event,
  and a file whose events it reads from the wrong bytes gets noise in all
  of them: a public Q Exactive HF-X PRM run read as both polarities and
  none, MS orders 1 to 8 and five analyzers. On an Exploris or a Q Exactive,
  a scan read as an analyzer no Orbitrap instrument has, or as none, now
  refuses the file, whatever made it misread, with the reason and the scans
  read as each analyzer in its status. An ion-trap scan does not: the model
  is read from the file's free text, which can name a Q Exactive in a
  hybrid's file. Opening a file costs what it did, and every scan of the
  demo bundle's files and of two public Q Exactive Plus runs reads as
  before. A file converted on misread events before this release is
  refused by every raw read of it since (GitHub #2394).
