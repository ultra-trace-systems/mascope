- **The weekly weakened-target guard covers 13 controls, up from 8.** It now
  also serves the API docs and the static OpenAPI document at the paths
  `RECON-01` and `RECON-02` probe, drops HttpOnly and SameSite from the auth
  cookie (`JWT-04`), lets a client's own `X-Real-IP` reach the backend's
  limiter (`IDENT-01`), and raises `[meta] tus_max_upload_gb` above what
  `UPLOAD-02` declares. The last is made in the demo env's config layer and
  restarts the backend, so a run takes about a minute longer. The env's layer
  is put back when the healthy stack is restored, and on the backend's next
  start if a run was cut short. No configuration edit is left unwritten.
