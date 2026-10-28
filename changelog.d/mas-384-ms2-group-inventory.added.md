- **Which samples of a batch have MS2 is one request.** Each file's MS2
  groups (precursor, activation, energies, isolation widths, scans, time
  span, scan range) are listed in a new `ms2_group` table when its MS2 store
  is written. `GET /api/sample/batches/{id}/ms2/groups` (token access) gives
  the groups of every sample of a batch in one query, keyed as
  `ms2/scans?group=` takes them (`137.0960@hcd25.00`). The samples listing
  gives each sample's `ms2_group_count`, and a sample's record its
  `ms2_groups`. A sample's MS2 summary lists the same groups, read from the
  table (`source: "inventory"`) where its window holds each whole, and says
  why it lists none in `reason`: `none` for a raw Orbitrap file, and
  `unsupported` for any other, such as a TOF sample, whose summary used to
  fail with a server error.
