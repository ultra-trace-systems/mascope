- **A download of several raw files no longer takes their size twice on disk.**
  The bundle copied every raw file into a folder in the user's temp directory,
  zipped the folder beside it and left the folder there for a day. The files
  are now written straight into the zip, so the zip is all a bundle leaves.
  A second bundle started within the same second no longer fails.
