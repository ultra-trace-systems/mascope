/**
 * Environment configuration for the hermetic e2e suite.
 *
 * Defaults target the demo stack (docker-compose.demo.yaml), where the
 * frontend is served behind nginx on :8080 and proxies /api/ same-origin.
 * Every value can be overridden to point at another running stack.
 */
export const env = {
  /** Frontend origin the tests drive. 127.0.0.1 rather than localhost:
   *  Docker publishes on IPv4, while localhost can resolve to ::1. */
  baseURL: process.env.MASCOPE_E2E_BASE_URL ?? 'http://127.0.0.1:8080',
  /** API origin (same as baseURL on the demo/prod stacks; :8090 on the dev server). */
  apiURL:
    process.env.MASCOPE_E2E_API_URL ?? process.env.MASCOPE_E2E_BASE_URL ?? 'http://127.0.0.1:8080',
  /** Login credentials; defaults are the seeded demo user. */
  email: process.env.MASCOPE_E2E_EMAIL ?? 'demo@mascope.app',
  password: process.env.MASCOPE_E2E_PASSWORD ?? 'mascope-demo',
  /** Where the auth setup project persists the signed-in browser state. */
  storageStatePath: 'playwright/.auth/user.json',
  /** MASCOPE_E2E_REQUIRE_ASSIGNMENT: the specs of the assignment views
   *  (inspector-card, batch-assign) fail where they would skip for want of
   *  them. CI's second run sets it, on a frontend recreated with the views
   *  on (.github/docker-compose.demo.assignment.yaml), so a stack that came
   *  up without them fails there rather than passing with nothing run. Unset
   *  (or `0`), they skip, as on the plain demo stack. */
  requireAssignment: !['', '0'].includes(process.env.MASCOPE_E2E_REQUIRE_ASSIGNMENT ?? '')
}
