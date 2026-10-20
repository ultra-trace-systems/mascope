- **Pairing logs say where a pairing was asked for, and no longer log its
  code.** The server log line for an agent starting a pairing now carries
  the client address it came from, and the line for an approval names the
  approving user's id, the device and machine account it created and that
  address, so a paired credential can be traced back to its request. The
  approval line used to carry the pairing code; it no longer does. Starting a
  pairing also claims its code atomically: in the rare case that no free code
  is drawn it answers 503 instead of writing over another pairing's code.
