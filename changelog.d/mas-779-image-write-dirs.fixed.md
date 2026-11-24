- **The uid 1000 warning covers the directories the images write to.**
  `mascope init`, `mascope prod up` and `mascope prod doctor` warned only
  about secrets the published images cannot read. They now also check the
  env's filestore and logs and the pre-migration dumps' directory, which a
  home made by another user leaves unwritable to uid 1000. The warning names
  one command for exactly the paths found, each quoted, so a home path with a
  space no longer gives a command that chowns the wrong paths. The printed
  fix of `mascope env sync` is quoted too.
