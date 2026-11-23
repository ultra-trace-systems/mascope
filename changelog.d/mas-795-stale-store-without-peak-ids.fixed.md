- **A peak store without peak ids is read as a stale store.** A store
  written before peak ids were kept, and missed by the migration that gave
  stores their ids, made the sample's peak list fail with an unexpected
  error, and every batch refresh failed its samples with a raw message. It
  now answers with the stale-store message, a batch refresh counts its
  samples as stale and queues their file's peak detection, and
  `rebuild_stale_peak_stores` counts and rebuilds the raw Orbitrap ones. A
  TOF or converted file's store is rebuilt by *Detect peaks*, or by the
  refresh that queues it.
