- **The SDK makes a client after loguru was reset.** A second
  `MascopeClient` no longer fails with "There is no existing handler" when
  the application removed every loguru handler (`logger.remove()`) after the
  first one was made. The SDK now adds its handler afresh.
