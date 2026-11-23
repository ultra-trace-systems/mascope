- **The backend turns unhealthy when it loses Postgres or Redis.** Its compose
  healthcheck only checked that the backend's port was open. A backend whose
  database and Redis were stopped therefore stayed `healthy` for
  `mascope prod doctor`, the monitoring push and the update's health wait
  (#3470). The check now asks a new readiness route inside the container,
  `GET /ready`. It answers 200 only while the backend reaches both stores, so
  five failed probes, about 50 s, mark the backend `unhealthy`. It turns
  healthy again by itself once both answer. The route says ready or not
  ready and nothing else, nginx does not pass it on, and each backend worker
  logs what it could not reach once each time that changes.
