- **The frontend image no longer fails to build now and then with "Cannot
  find native binding".** npm sometimes skipped the bundler's native binding,
  an optional package, without a warning, and the build step after it failed.
  The image now installs exactly what the lockfile names, checks the binding
  is there, and installs once more if it is not. A build that still lacks it
  fails at the install step and says why.
