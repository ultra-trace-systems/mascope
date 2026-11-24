- **A mode's diagnostic collection edit no longer flags analysis batches.**
  An analysis batch is matched against the collections a person gave it, so
  changing only a mode's diagnostic collection leaves it as it was and flags
  only the daily acquisition batches for re-matching. A change of the mode's
  mechanisms or calibrant collection still flags every batch with samples of
  the mode, and re-matching or re-calibrating an analysis batch still moves
  its samples onto the mode's new version while its collections stay.
