- **The Thermo RawFileReader backend names targeted MS2 streams as OpenTFRaw
  does.** The two backends could write an MS2 scan range's upper bound with
  a different fourth decimal (`181.6370` against `181.6369`), so a stream
  key from one did not select under the other. The Thermo backend now
  writes each bound from the same single-precision value OpenTFRaw reads.
  OpenTFRaw, the default reader, keys every stream as before, so no stored
  key changes.
