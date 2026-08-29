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
#
# **What it does NOT run**, so nobody reads more into a green line than it
# says: MinIO is not started, so the MinIO-gated compliance tests skip here and
# run only in CI; `npm ci` is not run, so lockfile integrity is CI's job and
# `node_modules` is symlinked from the working tree.
set -uo pipefail

BASE="${1:-origin/main}"

# A database of its own, unique per run.
#
# pytest-django derives `test_<name>` from the URL, so two runs sharing a name
# fight over creating and dropping it -- which reported two commits as failing
# `pytest` for reasons that had nothing to do with them. A verifier that emits
# false failures is worse than none: it teaches you to disbelieve it.
#
# The first fix used a fixed literal, which stops it colliding with a
# developer's suite and does nothing about two verifier runs -- a rerun started
# over one still going, or two branches at once. Both are ordinary. The name
# carries the PID so overlapping runs cannot meet, and cleanup drops it on
# every exit path rather than only on success.
VERIFY_DB="verify_$$"
if [ -n "${DATABASE_URL:-}" ]; then
  ADMIN_URL="${DATABASE_URL%/*}/postgres"
  export DATABASE_URL="${DATABASE_URL%/*}/${VERIFY_DB}"
fi
ROOT="$(git rev-parse --show-toplevel)"
WORKTREE="$(mktemp -d)/verify"
FAILED=0

cleanup() {
  git -C "$ROOT" worktree remove --force "$WORKTREE" 2>/dev/null || true
  # Both databases: the one named here and the `test_` one pytest-django
  # derives from it. Dropped on every exit -- success, failure, and Ctrl-C --
  # because a run that dies half way is exactly the run that leaves a database
  # behind, and the next person meets it as a mysterious failure rather than
  # as litter.
  if [ -n "${ADMIN_URL:-}" ]; then
    psql "$ADMIN_URL" -qc "DROP DATABASE IF EXISTS test_${VERIFY_DB} WITH (FORCE)" >/dev/null 2>&1 || true
    psql "$ADMIN_URL" -qc "DROP DATABASE IF EXISTS ${VERIFY_DB} WITH (FORCE)" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

if [ -n "${ADMIN_URL:-}" ]; then
  psql "$ADMIN_URL" -qc "CREATE DATABASE ${VERIFY_DB}" >/dev/null 2>&1 || true
fi

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
    # CI runs these three and the verifier did not, so "green on its own" was
    # a claim about a smaller set than it sounded like.
    "$ROOT/.venv/bin/python" tools/check_field_shadowing.py >/dev/null 2>&1 \
      || problems+=("field-shadowing")
    "$ROOT/.venv/bin/python" manage.py check >/dev/null 2>&1 || problems+=("django-check")
    "$ROOT/.venv/bin/python" manage.py makemigrations --check --dry-run >/dev/null 2>&1 \
      || problems+=("missing-migration")
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
    # Generated TypeScript, not only the YAML the backend half checks. The
    # two can disagree: a regenerated schema.yaml that was never turned back
    # into schema.d.ts is a compile-time contract one commit behind.
    npx openapi-typescript src/api/schema.yaml -o /tmp/verify-schema.d.ts >/dev/null 2>&1
    diff -q /tmp/verify-schema.d.ts src/api/schema.d.ts >/dev/null 2>&1 \
      || problems+=("types-drift")
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
