- **Optional calibration anchors in the collection editor and the SDK.** A
  CALIBRANTS collection's compound list has an **Optional anchor** checkbox
  per compound, loaded from the collection and saved with it, so a
  calibration collection built in the editor from a shipped one can keep its
  background marks. Saving a change of the marks asks first, since the
  collection's calibrated batches are flagged recalibrate. The SDK's
  `collections.create` and `collections.update` take `optional_anchor_ids`,
  and `collections.get` documents `optional_anchor_compound_ids`.
