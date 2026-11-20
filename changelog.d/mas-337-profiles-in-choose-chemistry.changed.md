- **`GET /api/ionization/modes` lists every profile Mascope ships.** A
  profile is listed whether or not anything was processed under it, and every
  row carries `routed_files`, how many files the server filed under the mode
  and its earlier versions, and `last_file_utc`, when the newest of them was
  acquired. The `include_system` parameter is gone; a client that still sends
  it, such as the SDK's `ionization.modes(include_system=True)`, gets the same
  listing.
