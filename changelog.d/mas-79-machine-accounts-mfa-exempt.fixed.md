- **Machine accounts are exempt from the two-factor requirement.** On a
  deployment whose `mfa_required_min_role` covers editors, every instrument
  agent's machine account was reported as owing an enrolment it can never
  complete, since it never signs in at a browser. The policy now exempts
  machine accounts by their kind, whatever their role: the users API reports
  them `mfa_exempt: true` and never `mfa_enrollment_required`, and Manage users
  shows them as exempt rather than pending, so "pending" is the set of people
  who still owe an enrolment. A person at the same role is covered as before.
  The second-factor sign-in step now refuses a machine account on its own, as
  the password step already did (`docs/authorization.md`).
