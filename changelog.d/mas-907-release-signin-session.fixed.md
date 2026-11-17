- **Launched runs no longer stall the rest of the app.** A request kept its
  database permit until the work it launched had ended, so a few assignment
  runs, batch runs or rematches started at once held every permit of a worker,
  and every signed-in request on it waited for a run to end. The sign-in
  session now closes when the request has its answer (#3240).
