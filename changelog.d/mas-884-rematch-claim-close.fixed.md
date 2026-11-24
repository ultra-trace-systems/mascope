- **A cancelled rematch returns its database connection.** A batch rematch
  stopped part way unlocked its batch but left its connection checked out of
  the pool until garbage collection. It now closes it as it releases the claim.
