- **Every loaded table says what its columns mean.** The AI toolkit's
  Frames carry a one-line meaning for each column the toolkit knows
  (`frame.column_meanings()`), and the MCP server puts them on every Frame's
  handle as `meanings`: what the number is, its unit, and what it is not.
  The meaning follows the load, so `height` reads as the averaged peak's
  apex intensity in a peak list and as one scan's value in a time series,
  and a spectrum's `intensity` says it is a profile point in the averaged
  spectrum's unit, counts on an Orbitrap and ions/s on a TOF. A column a
  `derive` computed says so and points to the code; one its inputs give
  different meanings has none. The user
  docs gain a "Tables and columns" page under Concepts that lays the four
  tables and their columns out for SDK users and agents alike.
