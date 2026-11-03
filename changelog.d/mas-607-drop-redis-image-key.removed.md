- **`[backend.redis] image` is gone from `base.mascope.toml`.** Nothing read
  it: the compose files pin the Redis image themselves. `dev config` and
  `prod config` no longer list it as an unknown key on every deployment.
