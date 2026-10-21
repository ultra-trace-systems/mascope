- **Updating a sample checks a new batch and a new file.** `PATCH
  /api/sample/items/{id}` moved a sample into whatever batch its body named,
  one in a workspace the caller has no role in included, and linked it to any
  file. A new batch now needs the editor role and must not be locked, as for
  `/api/sample/items/move`, and a locked sample cannot change batch. A new
  file must be one the caller can read.
