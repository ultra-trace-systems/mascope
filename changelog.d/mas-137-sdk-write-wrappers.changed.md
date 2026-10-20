- **The SDK raises `PermissionDeniedError` on a 403.** It was an
  `AuthenticationError` telling you to check your API token, which a 403
  never needs: the token was accepted, and its user lacks the role. The new
  error is a subclass of `AuthenticationError`, so code catching that still
  catches it, and a write method's error names what the call acted on and
  the role it needs (`object_type`, `object_id`, `required_role`), which the
  server's answer does not.
