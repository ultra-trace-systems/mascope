- **A peak's matches come back in the same order on every read.** Matches of
  one peak with equal scores, and the collections listed for one match, came
  back in whatever order the database returned them, which could change
  between two reads with nothing changed in between. Ties are now broken by
  the target isotope and collection ids, so two loads of the same sample or
  batch compare equal row by row.
