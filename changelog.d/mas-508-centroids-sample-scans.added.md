- **The centroid route serves a sample's own scans on request.**
  `GET /api/samples/centroids?scans=sample`, and
  `mascope.samples.get_centroids(..., scans="sample")` in the SDK, return
  only the scans the sample's peak list is read from: the MS1 scans of its
  own scan stream, or of each stream that owns some m/z of a stitched
  sample, each stream's outlier first scan left out. They are the scans the
  sample's axis fingerprint lists in `item.scans`, so a client no longer
  joins the two by scan number. Each sample's entry then says
  `scan_selection: "sample"`, and the SDK raises when a server older than
  the parameter answers without it. The default, `scans=all`, still serves
  every scan of the sample's window, MS2 scans and every stream's included.
