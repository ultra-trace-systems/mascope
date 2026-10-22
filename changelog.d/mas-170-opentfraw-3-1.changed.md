- **The raw-file reader is OpenTFRaw 3.1.0, which reads targeted MS2 files
  with a named inclusion list itself.** Mascope's interim decoder for those
  files (`mascope_thermo/v66_scan_events.py`) is removed, and with it the
  warning such a file logged each time it was read. The reader reads every
  scan-event record at its own size, so these files read as before: on the
  four of the internal regression corpus, every scan's filter, centroids and
  profile are the ones the interim gave them. Three more things the new
  reader brings:
  - `GET /api/sample/files/{id}/scans` now gives a file's
    `calibration_status`, the overdue mass calibrations its first and last
    scan name, under the default reader too. The reader decodes the
    instrument's status log, which it never found before; the status agrees
    with the Thermo library's on all 164 files compared.
  - A method that sets an in-source CID energy now shows it in the scan
    filter (`sid=20.00`) wherever the method's layout hid it before, so the
    scan stream of such a file gets a new name.
  - Fusion Lumos, Eclipse and Orbitrap Elite MS1 profiles come back in m/z,
    where they came back as frequencies.

  Everything else reads as before on 433 files (the demo bundle's 161, the
  two committed sample files and the corpus's 270 Orbitrap files): every
  scan's centroids and profile, MS2 scans included, and the averaged
  centroids and profiles (GitHub #2391).

  A raw Orbitrap file's cached profile is named after the reader that
  averaged it, so each sample is averaged anew on its first view after the
  upgrade, to the profile it had.

  Operators: the caches OpenTFRaw 2.0.0 averaged (`...otf2.0.0-g5.zarr`) are
  never read again. Reclaim the space with
  `python -m mascope_backend.db.admin.filestore delete-stale-sum-signal`.

- **The demo dataset is now bundle v1.3.2**
  ([10.5281/zenodo.23562417](https://doi.org/10.5281/zenodo.23562417)),
  built with OpenTFRaw 3.1.0, the reader the reproducibility check now
  expects. Same 161 acquisitions, the same reference data and the same
  goldens: all 42,521 peaks and 17,254 ions hold the values 1.3.1 gave them.
  `mascope demo` picks it up by default, and 1.3.1 stays registered so a
  pinned run still resolves.
