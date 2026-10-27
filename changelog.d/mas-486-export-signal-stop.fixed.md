- **`mascope export package` stops its container build when it is ended
  by SIGHUP or SIGTERM, as on Ctrl-C.** A dropped SSH session, or a
  `timeout` or service manager stopping the command, left the build in the
  prod backend container running, and its archive in the env's temp
  directory until the backend restarted. The command now stops the build
  and removes its directory first, then ends by the signal it was sent. A
  SIGHUP ignored with `nohup` stays ignored.
