- **The SDK keeps the application's own loguru handlers.** The first
  `MascopeClient` in a process removed every loguru handler to drop
  loguru's default one, so a file or error-reporting sink the application
  had added before it silently stopped receiving lines. It now removes only
  loguru's default stderr handler (#2566).
