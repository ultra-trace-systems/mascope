- **`GET /api/version/public` reports the running version without signing
  in.** A monitoring host with no account on a server can read which release
  it runs, for instance to find the servers of a fleet that are behind. It
  answers the version string alone; the capabilities stay behind
  `GET /api/version`, which still asks for a signed-in user or a device
  token and answers them as before.
