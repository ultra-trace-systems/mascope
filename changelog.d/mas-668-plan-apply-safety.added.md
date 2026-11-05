- **The AI toolkit's plan/apply is tested against the failures it was built
  to block.** `libraries/ai/tests/ai_safety.py` holds ten safety scenarios, each
  a scripted agent that tries a harmful write through the plan builders and
  `apply`: a compound added to a collection must keep every compound it had,
  no builder renames a batch or rewrites a list in place, an object of another
  workspace is refused at planning whatever ID names it, look-alike names
  resolve to neither, a hidden object is never named, text stored in a
  description stays data, a denied approval writes nothing, nothing on the
  surface deletes, a write that fails part-way is reported as such, and a
  legitimate move is not refused. A collateral snapshot of the workspace,
  read through the tool surface before and after each scenario, fails the test
  on any change outside the scenario's allowed set. They run hermetically and,
  eight of them, against the demo stack with the contract tests' switch. The
  shapes are those of the in-house evaluation of open models
  (`docs/dev/ai_analyst_shell.md`, section 3.4).
