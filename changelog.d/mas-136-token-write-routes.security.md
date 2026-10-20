- **An API token can organise data, on the routes opened for it one by one.**
  The SDK's coming write methods need nineteen write routes that refused a
  token with 401, and they now accept one: dataset create, update, move and
  delete; batch update, copy and delete; sample update, move and copy; target
  collection create, update (which also associates batches) and delete;
  target compound add, update and remove; and saving or rebuilding a sample's
  or batch's aggregate matches. Each keeps its ACL, which a token passes as
  its user's session does, and each was read before it was opened; the rest
  of the write API still refuses a token. "What a token may change" in the
  SDK docs lists them with the role each needs. The pen-test suite's new
  TOKEN-01 reports any write a deployment opens to tokens beyond the declared
  list in `security/pentest/token_writes.json`, which the backend's tests keep
  equal to the routes.
