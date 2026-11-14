- **A log line no longer leaves its colour reset on the next line.** The
  console log wrote each line's newline before its reset code, so the next
  line on stderr began with `ESC[0m`. A reader matching a whole line, such as
  a marker or a JSON object printed beside the log, missed it. The reset now
  ends its own line.
