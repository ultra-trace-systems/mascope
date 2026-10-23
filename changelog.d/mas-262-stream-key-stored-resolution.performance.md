- **Under the Thermo library, a stream's scans are found without reading
  each scan's whole trailer as text.** Selecting a stream and taking a
  file's census read every scan's trailer as text for its FT resolution
  alone. They now read the one number the file stores, and the text only
  where that number is missing or not whole, so every stream key stays as
  it was. The OpenTFRaw reader, the default, is unchanged.
