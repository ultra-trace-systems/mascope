- **Signing out also signs the tab's live connection out.** Before, a
  signed-out tab kept receiving the updates of the workspaces, batches and
  datasets it had open until it was reloaded. Now the server ends that tab's
  realtime session at sign-out, and the app opens a new connection that
  carries no session. The account's sessions in other browsers carry on, and
  signing in again in the same tab picks the open views' updates back up.
