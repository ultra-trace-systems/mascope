- **The frontend image no longer fails to build now and then with "Cannot
  find native binding".** npm sometimes skipped a native binding the build
  needs, of the bundler or of the CSS minifier, both optional packages,
  without a warning, and the build step after it failed. The image now
  installs exactly what the lockfile names, checks both bindings are there,
  and installs once more if one is not. A build that still lacks one fails at
  the install step and says which.
