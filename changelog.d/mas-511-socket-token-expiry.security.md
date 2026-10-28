- **A tab's live updates stop when its session expires.** A web session
  lasts seven days, after which the tab is signed out on its next request.
  Its realtime connection stayed signed in, though: a tab left open past
  the seven days kept receiving the updates of what it had open, and could
  open more. Signing out in another tab, after signing in again there, did
  not reach it either. Now the server signs the connection out when its
  session expires.
