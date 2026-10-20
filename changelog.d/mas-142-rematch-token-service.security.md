- **Only the file converter's token can start a sample rematch.**
  `POST /api/match/rematch/sample/{id}` deletes a sample's matches (all of
  them with `full_remove`) and recomputes them. It answered every API token,
  so anyone holding an SDK or agent token could do that to any sample their
  account may edit. It now answers the `file-converter` token alone, which the
  converter uses to rematch after a peak recompute; an SDK or agent token gets
  401, and the web app is unaffected. Each token-driven rematch is logged with
  the service and the user. A route can now be opened to some services' tokens
  only, with `api_route(token_access=("file-converter",))`. (#2354)
