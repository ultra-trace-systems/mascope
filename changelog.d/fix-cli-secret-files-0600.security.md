- **The secrets `mascope init` and `mascope prod up` generate are readable by
  their owner alone.** `postgres_password.txt`, `jwt_secret_key.txt`,
  `server_owner_secret_key.txt` and `mfa_encryption_key.txt` under
  `.runtime/secrets/` are now created mode 0600. Before, they took the umask
  and usually came out 0644. Compose mounts a secret file into the containers
  as it is on the host, mode included, so every user on the server and every
  uid in the backend container could read the app's database password.
  Existing files are left as they are. To close them on a deployment, run
  `chmod 600 "$MASCOPE_PATH"/.runtime/secrets/*.txt` as the user that owns
  them. That user has to be the uid the backend image runs as: 1000 for the
  published image, or the `MASCOPE_UID` the image was built with. The
  filestore already needs the same. Otherwise the backend can no longer read
  its secrets.
