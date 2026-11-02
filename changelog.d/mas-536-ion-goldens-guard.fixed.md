- **`mascope demo snapshot --update` no longer drops a bundle's ion goldens
  without a word.** A refresh whose ion export came back empty, while its
  peaks exported as before, wrote a manifest without the per-ion goldens the
  bundle had. It now refuses before writing anything, as it already does for
  the MS2 goldens.
