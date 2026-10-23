#!/usr/bin/env bash
# Opens, or comments on, the issue that tracks a scheduled workflow going red.
#
# One open issue per workflow, labelled `ci` and titled
# "Nightly CI: <workflow> is red". The first failure opens it with the run
# link and the jobs that failed; every later failure adds a comment while it
# is open. Nothing here closes it: the fixing pull request's `Fixes #N` does,
# or a person once the cause is understood, so a green night cannot hide a
# real break. From there the issue is triaged like any other.
#
# Environment, set by action.yml: GH_TOKEN (actions: read, issues: write),
# GH_REPO, WORKFLOW, RUN_ID, RUN_URL. With DRY_RUN=1 the script reads as
# usual and prints the write it would make instead of making it, which is
# how it is tried from a workstation against the live repository.
set -euo pipefail

: "${GH_REPO:?}" "${WORKFLOW:?}" "${RUN_ID:?}" "${RUN_URL:?}"
LABEL="ci"
TITLE="Nightly CI: ${WORKFLOW} is red"
export TITLE
DAY=$(date -u +%Y-%m-%d)

# The jobs that did not pass, by name. The job running this script is still
# in progress and has no conclusion yet, so it leaves itself out. A job that
# hit its timeout is reported as cancelled, not failed, so both are named:
# a nightly that hung is one to look at too. Read through gh's own --jq, so
# the script needs no jq where it is tried by hand.
if FAILED=$(gh run view "$RUN_ID" --json jobs     --jq '[.jobs[] | select(.conclusion == "failure") | .name] | join(", ")' 2>/dev/null); then
  UNFINISHED=$(gh run view "$RUN_ID" --json jobs     --jq '[.jobs[] | select(.conclusion == "cancelled" or .conclusion == "timed_out") | .name] | join(", ")' 2>/dev/null || true)
  STATUS=""
  [ -n "$FAILED" ] && STATUS="Failed jobs: ${FAILED}"
  [ -n "$UNFINISHED" ] && STATUS="${STATUS:+$STATUS; }Cancelled or timed out: ${UNFINISHED}"
  STATUS=${STATUS:-"Jobs: none failed; see the run"}
else
  STATUS="Jobs: could not be read (the token may lack actions: read); see the run"
fi

# The open issue for this workflow: the exact title among the open `ci`
# issues. A word search would also match a workflow whose name contains
# another's, and the oldest open one wins should two ever exist.
NUMBER=$(gh issue list --state open --label "$LABEL" --limit 100 --json number,title \
  --jq '[.[] | select(.title == $ENV.TITLE)] | sort_by(.number) | .[0].number // empty')

write() {
  if [ "${DRY_RUN:-0}" = "1" ]; then
    printf 'DRY RUN: gh'
    printf ' %q' "$@"
    printf '\n'
  else
    gh "$@"
  fi
}

if [ -n "$NUMBER" ]; then
  echo "Open issue #${NUMBER} tracks this workflow; adding the run."
  write issue comment "$NUMBER" --body "$(printf 'Failed again on %s: %s\n\n%s' "$DAY" "$RUN_URL" "$STATUS")"
else
  echo "No open issue tracks this workflow; opening one."
  BODY=$(cat <<BODY_EOF
The scheduled run of the **${WORKFLOW}** workflow did not pass on ${DAY}.

- Run: ${RUN_URL}
- ${STATUS}

Filed by the workflow itself (\`.github/actions/nightly-failure-issue\`), so a red nightly reaches triage instead of only the failure e-mail. Later failures of this workflow are added below while this issue is open; a green night does not close it. Close it with the fix (\`Fixes #N\` in the pull request), or by hand once the cause is understood.
BODY_EOF
)
  write issue create --title "$TITLE" --label "$LABEL" --body "$BODY"
fi
