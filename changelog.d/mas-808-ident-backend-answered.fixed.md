- **The pen-test's client-address controls no longer fail on a backend that is down.**
  `IDENT-01` and `IDENT-02` counted every login an edge answered, such as
  nginx's 502 page or a gateway's 403, as one the backend let through
  unthrottled, and reported a high-severity spoofable rate limit against a
  healthy one. They now count only the backend's own refusals of those logins,
  and a burst the backend did not answer is reported as not assessed.
