- **The own-backup-server recipe gives each backed-up server an account of its
  own.** The developer guide's "Using your own backup server" described one
  shared account with an ordinary shell, which handed every server a key that
  could delete the other servers' backups, open a shell on the backup server
  and tunnel through it. It now sets up one SFTP-only account per server,
  chrooted to its own directory by an sshd `Match Group` drop-in, its key
  usable only from that server's VPN address, and checks the drop-in with
  `sshd -t` and `sshd -T` under an automatic revert before reloading. A server
  reaches its repository through an ssh alias as `sftp:<backup-host>:/repo`,
  as `tooling/backup.env.example` now shows, and the guide says how to move an
  existing shared account over one server at a time. It also says what this
  does not cover: restic over SFTP cannot be append-only, so a compromised
  server can still delete its own history. The monitoring box's "Receiving
  the fleet's backups" points to the same recipe.
