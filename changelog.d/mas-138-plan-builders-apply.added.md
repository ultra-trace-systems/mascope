- **Writes for AI agents, behind an approval.** `mascope_ai` plans a write
  before it makes it: `plan_organize` (dataset and sample moves),
  `plan_collection`, `plan_associate` and `plan_match` describe the change,
  before and after, the role it needs and whether it can be undone, and
  write nothing. `apply` makes a plan's writes only with a confirm token
  from a person's approval of that plan, once, after checking that nothing
  it planned on has changed. Global collections and shared compound
  records are left alone unless asked for.
