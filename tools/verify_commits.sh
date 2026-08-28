#!/usr/bin/env bash
#
# Verify each commit in a range on its own, the way CI would.
#
# GitHub builds only the head of a push, so "every commit is green" is an
# unverified claim about everything underneath it. This checks it instead of
# asserting it -- the same instinct that caught a fabricated finding by reading
# a diff rather than a summary of one (docs/OPERATIONS.md, instance 5).
#
# Usage:  tools/verify_commits.sh [base]      (default: origin/main)
#
# Each commit is checked out into a throwaway worktree, so the working tree is
# never touched and an interrupted run leaves nothing behind. Slow by design:
# it runs the real suites, because a faster check that skipped them would be a
# check whose scope is narrower than the belief attached to it.
set -uo pipefail

BASE="${1:-origin/main}"
ROOT="$(git rev-parse --show-toplevel)"
WORKTREE="$(mktemp -d)/verify"
FAILED=0

cleanup() { git -C "$ROOT" worktree remove --force "$WORKTREE" 2>/dev/null || true; }
trap cleanup EXIT

git -C "$ROOT" worktree add -f --detach "$WORKTREE" HEAD >/dev/null 2>&1

for sha in $(git -C "$ROOT" rev-list --reverse "$BASE"..HEAD); do
  subject="$(git -C "$ROOT" log -1 --format=%s "$sha" | cut -c1-52)"
  git -C "$WORKTREE" checkout -q --detach "$sha"
  problems=()

  if [ -d "$WORKTREE/backend" ]; then
    pushd "$WORKTREE/backend" >/dev/null
    "$ROOT/.venv/bin/ruff" check . >/dev/null 2>&1 || problems+=("ruff")
    "$ROOT/.venv/bin/ruff" format --check . >/dev/null 2>&1 || problems+=("format")
    "$ROOT/.venv/bin/mypy" . >/dev/null 2>&1 || problems+=("mypy")
    DJANGO_SETTINGS_MODULE=config.settings.test \
      "$ROOT/.venv/bin/python" -m pytest -q >/dev/null 2>&1 || problems+=("pytest")

    # Schema drift: what the code generates must match what was committed.
    "$ROOT/.venv/bin/python" manage.py spectacular --file /tmp/verify-schema.yaml >/dev/null 2>&1
    diff -q /tmp/verify-schema.yaml "$WORKTREE/frontend/src/api/schema.yaml" >/dev/null 2>&1 \
      || problems+=("schema-drift")
    popd >/dev/null
  fi

  if [ -d "$WORKTREE/frontend" ]; then
    pushd "$WORKTREE/frontend" >/dev/null
    # Symlinked rather than installed: `npm ci` per commit would make this
    # take an afternoon, and the lockfile is checked by CI anyway.
    [ -e node_modules ] || ln -s "$ROOT/frontend/node_modules" node_modules
    npx tsc --noEmit -p tsconfig.app.json >/dev/null 2>&1 || problems+=("tsc")
    npm run lint --silent >/dev/null 2>&1 || problems+=("eslint")
    npx vitest --run >/dev/null 2>&1 || problems+=("vitest")
    npm run build --silent >/dev/null 2>&1 || problems+=("build")
    npm run check:budget --silent >/dev/null 2>&1 || problems+=("budget")
    popd >/dev/null
  fi

  if [ ${#problems[@]} -eq 0 ]; then
    printf '  ok    %s  %s\n' "${sha:0:7}" "$subject"
  else
    printf '  FAIL  %s  %s  [%s]\n' "${sha:0:7}" "$subject" "${problems[*]}"
    FAILED=1
  fi
done

if [ "$FAILED" -eq 0 ]; then
  echo "Every commit passes on its own."
else
  echo "At least one commit is not green by itself; a bisect through this range would land on it."
fi

exit "$FAILED"
