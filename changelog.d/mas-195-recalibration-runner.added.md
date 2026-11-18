- **Re-calibrate a TOF instrument's stored files.** The maintenance script
  `recalibrate_tof` refits one instrument's TOF files with the calibration
  node as it is now, applies each fit as ingest would, and rematches what the
  applies cleared, a batch at a time and paced against the live ingest. A fit
  below the quality bar never replaces one that cleared it. A dry run, the
  default, reports how far each file's m/z axis would move per mass band. A
  ledger in the filestore records what every file had before, and a rollback
  puts a file or a batch back from it, every m/z axis bit for bit. A stopped
  run resumes where it stopped. `docs/maintaining.md` has the steps.
