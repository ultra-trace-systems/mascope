- **A sample file's source data can be downloaded with an API token.**
  `GET /api/sample/files/{id}/raw` streams the stored Orbitrap `.raw` or TOF
  `.h5` straight from the filestore, byte for byte, with its length and the
  name it was uploaded under, to a guest of the file's workspace signed in
  either way; a file that keeps no source data answers 404. No temporary
  copy is made, and each account may start 60 downloads in ten minutes. The
  SDK's `mascope.sample_files.download_raw(sample_file_id, path)` saves it
  to disk. The web app's `POST /api/file/download` is unchanged.
