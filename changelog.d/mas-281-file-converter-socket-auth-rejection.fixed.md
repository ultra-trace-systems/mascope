- **A rejected file-converter event is no longer reported as an error.** An
  event the file converter sent without a valid token, or from an account
  below editor, was logged as a routine rejection and then raised again. The
  exception reached no one and showed up in error monitoring as "Task
  exception was never retrieved". The event is now dropped after the log
  line, as the browser's socket events already were.
