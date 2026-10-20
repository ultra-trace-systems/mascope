- **A migration update installed by hand no longer shows as pending.** When
  the update timer (`mascope prod update --auto`) has recorded a migration
  release as pending, and an operator then deploys that release with
  `mascope prod update`, the record is now cleared. Before, it stayed until the
  next timer run, so `mascope prod doctor` kept saying a migration was waiting
  after it had already run. Deploying a later release, or a candidate for one,
  clears it too. Rolling back to an older release keeps it, because that update
  is still outstanding, and so does deploying `latest`, which the next timer run
  sorts out. The clear is written to the updater's status log.
