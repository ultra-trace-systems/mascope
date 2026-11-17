- **The assignment bench builds sets from files kept beside a private
  manifest.** A bench manifest can now name a file by its path in the
  manifest's own folder and its SHA-256 (`{"path": P, "sha256": H}`), and
  seed a set's ionization mode from a seed file in that folder
  (`{"file": "seed.json"}`). `bench init` hashes each such file and refuses
  one whose content is not the one the manifest names. It is meant for
  research data that is not the fleet's, which stays out of the fleet
  regression corpus (`tooling/assignment_bench/README.md`, "The manifest").
