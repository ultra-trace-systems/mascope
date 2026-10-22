- **The MS2 summary answers every isolation width a group used.** A parent
  peak group whose scans isolate at two widths reported no width at all. Each
  group of
  `GET /api/samples/{id}/ms2/summary` (`ms2(...).get_summary()` in the SDK)
  now carries `isolation_widths`, every width its scans used with their scan
  counts, narrowest first. `isolation_width` stays the group's one width when
  its scans share one, and the group keys of the centroid and timeseries
  routes do not move.

- **The Thermo reader gives one record per MS2 scan.**
  `ms2_scan_records()` on both reader backends returns, for every MS2 scan,
  its time, polarity, precursor m/z, activation and its collision energy, the
  HCD volts, the isolation width, the scan range, the injection time and the
  TIC, and says for each field whether it was read from the scan's filter,
  its trailer or the scan index. A field a scan's trailer lacks is `None`
  there, never another scan's value.
