- **`mascope-ai` no longer adds a stray "the object" to a refusal.**
  A 403 on an operation without an object read "Refused to list the target
  collections the object." or "Refused to apply plan ...: samples.move the
  object.". The message now names the object only when there is one.
