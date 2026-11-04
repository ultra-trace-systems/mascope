- **Renaming a batch through the API no longer needs, or risks, its target
  collections.** `PATCH /api/sample/batches/{id}` required
  `target_collection_ids` and took it as the batch's whole set of
  collections, so a request that only renamed a batch was refused, and a
  client that answered the refusal with an empty list detached every
  collection and flagged the batch for rematch. The field is optional now:
  left out, or null, the batch keeps its collections and its status. A list
  still replaces them, and an empty list still detaches them all. The web
  app always sends the list and is unaffected.
