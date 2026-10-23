- **A file-converter event rejected for its token now says so.** When the
  backend drops an event from the file converter because its access token is
  missing, invalid or expired, or its user is below editor, the event's
  acknowledgement now carries `{"error": "<code>"}`, such as
  `SOCKET_UNAUTHENTICATED`. Before, a rejected event was acknowledged exactly
  like an accepted one. The codes are listed in the developer guide.
