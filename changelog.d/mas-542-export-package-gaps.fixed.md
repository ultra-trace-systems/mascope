- **`mascope export package` warns of every gap Prepare for publication
  flags.** It warned of a missing licence or authors only, so a dataset
  with no description, or no samples, passed it silently. The build now
  reports the app's own gaps, and the command prints each one.
