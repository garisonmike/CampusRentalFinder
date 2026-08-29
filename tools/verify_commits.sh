#!/usr/bin/env bash
#
# Verify each commit in a range on its own, the way CI would.
#
# GitHub builds only the head of a push, so "every commit is green" is an
# unverified claim about everything underneath it. This checks it instead of
# asserting it -- the same instinct that caught a fabricated finding by reading
# a diff rather than a summary of one (docs/OPERATIONS.md, instance 5).
#
# Usage:
#   tools/verify_commits.sh [base]        default base: origin/main
#   tools/verify_commits.sh --self-test   prove it can still detect a red suite
#
# Each commit is checked out into a throwaway worktree, so the working tree is
# never touched. Slow by design: it runs the real suites, because a faster
# check that skipped them would be a check whose scope is narrower than the
# belief attached to it.
#
# **This script is the only tool in the repository whose failure mode is
# total.** Everything else fails loudly or fails alone; a verifier that cannot
# see red reports green for the whole range and is believed. Hence, below: no
# unchecked exit status anywhere, per-commit temporary files, an assertion
# about which toolchain is about to run, an explicit account of what was NOT
# run, and `--self-test`.
set -euo pipefail

SELF_TEST=0
if [ "${1:-}" = "--self-test" ]; then
  SELF_TEST=1
  shift
fi

BASE="${1:-origin/main}"
ROOT="$(git rev-parse --show-toplevel)"

# --------------------------------------------------------------------------
# The toolchain, asserted rather than assumed
# --------------------------------------------------------------------------
#
# The system Python moved 3.13 -> 3.14 under this venv once already. Its
# `bin/python3` is a symlink to `/usr/bin/python3`, so it followed, lost
# `lib/python3.13/site-packages`, and took pytest, ruff and mypy with it.
#
# Every one of those would then have failed with "no module named X" and been
# recorded as a real failure -- so the old script would have gone red, not
# green. But red for the wrong reason is its own problem: a range of commits
# reported as broken when the toolchain is what broke sends somebody looking
# at the commits. Naming the versions up front turns twenty minutes of
# bisecting into one line.
EXPECTED_PYTHON="${EXPECTED_PYTHON:-3.13}"

require() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$actual" != *"$expected"* ]]; then
    echo "toolchain: $label is '$actual', expected to contain '$expected'" >&2
    echo "Refusing to run: a verifier that does not know what it ran proves nothing." >&2
    exit 2
  fi
  printf '  %-8s %s\n' "$label" "$actual"
}

echo "Toolchain:"
require python  "$EXPECTED_PYTHON" "$("$ROOT/.venv/bin/python" -V 2>&1)"
require pytest  "pytest"           "$("$ROOT/.venv/bin/python" -m pytest --version 2>&1 | head -1)"
require ruff    "ruff"             "$("$ROOT/.venv/bin/ruff" --version 2>&1)"
require mypy    "mypy"             "$("$ROOT/.venv/bin/mypy" --version 2>&1)"
require node    "v"                "$(node --version 2>&1)"
require npm     "."                "$(npm --version 2>&1)"
echo

# --------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------
#
# A database of its own, unique per run. pytest-django derives `test_<name>`
# from the URL, so two runs sharing a name fight over creating and dropping it
# -- which once reported two commits as failing `pytest` for reasons that had
# nothing to do with them. The name carries the PID so overlapping runs cannot
# meet, and cleanup drops it on every exit path rather than only on success.
VERIFY_DB="verify_$$"
ADMIN_URL=""
if [ -n "${DATABASE_URL:-}" ]; then
  ADMIN_URL="${DATABASE_URL%/*}/postgres"
  export DATABASE_URL="${DATABASE_URL%/*}/${VERIFY_DB}"
fi

WORKTREE="$(mktemp -d)/verify"
SCRATCH="$(mktemp -d)"
FAILED=0

cleanup() {
  git -C "$ROOT" worktree remove --force "$WORKTREE" 2>/dev/null || true
  rm -rf "$SCRATCH" 2>/dev/null || true
  if [ -n "$ADMIN_URL" ]; then
    psql "$ADMIN_URL" -qc "DROP DATABASE IF EXISTS test_${VERIFY_DB} WITH (FORCE)" >/dev/null 2>&1 || true
    psql "$ADMIN_URL" -qc "DROP DATABASE IF EXISTS ${VERIFY_DB} WITH (FORCE)" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

if [ -n "$ADMIN_URL" ]; then
  psql "$ADMIN_URL" -qc "CREATE DATABASE ${VERIFY_DB}" >/dev/null 2>&1 || true
fi

git -C "$ROOT" worktree add -f --detach "$WORKTREE" HEAD >/dev/null 2>&1

# --------------------------------------------------------------------------
# What this does NOT run
# --------------------------------------------------------------------------
#
# Printed every time, before any result, so a green line is never read as
# covering more than it does. `docs/OPERATIONS.md` records a compliance test
# that skipped in CI and looked exactly like a passing one; the same shape
# applies to a verifier that quietly runs a smaller suite than the reader
# assumes.
MINIO_UP=0
if curl -fsS --max-time 2 "${S3_ENDPOINT_URL:-http://127.0.0.1:9000}/minio/health/live" >/dev/null 2>&1; then
  MINIO_UP=1
fi

echo "Not run by this script:"
if [ "$MINIO_UP" = "1" ]; then
  echo "  (MinIO is reachable, so the MinIO-gated compliance tests DO run here)"
else
  echo "  MinIO is unreachable, so every @pytest.mark.minio test SKIPS in this run."
  echo "  Those cover verified deletion against a real object store. CI runs them."
fi
echo "  npm ci -- node_modules is symlinked, so lockfile integrity is CI's job."
echo

check() {
  # Run one command, record a named problem if it fails. Never let a status
  # reach the floor unexamined.
  local label="$1"
  shift
  if ! "$@" >/dev/null 2>&1; then
    problems+=("$label")
  fi
}

verify_one() {
  local sha="$1"
  local subject
  subject="$(git -C "$ROOT" log -1 --format=%s "$sha" | cut -c1-52)"
  git -C "$WORKTREE" checkout -q --detach "$sha"

  problems=()

  if [ -d "$WORKTREE/backend" ]; then
    pushd "$WORKTREE/backend" >/dev/null

    check ruff             "$ROOT/.venv/bin/ruff" check .
    check format           "$ROOT/.venv/bin/ruff" format --check .
    check mypy             "$ROOT/.venv/bin/mypy" .
    check field-shadowing  "$ROOT/.venv/bin/python" tools/check_field_shadowing.py
    check django-check     "$ROOT/.venv/bin/python" manage.py check
    check missing-migration "$ROOT/.venv/bin/python" manage.py makemigrations --check --dry-run
    check pytest           env DJANGO_SETTINGS_MODULE=config.settings.test \
                             "$ROOT/.venv/bin/python" -m pytest -q

    # Schema drift, in two steps, and **both** are checked.
    #
    # The generator's status used to be unchecked and its output written to a
    # fixed path in /tmp -- so a failed generation left the previous commit's
    # file in place and the diff compared the wrong two things, silently. That
    # is the catalogue shape inside the tool that exists to catch it. Unique
    # path per commit, and a failure to generate is its own named problem.
    local schema="$SCRATCH/${sha}-schema.yaml"
    if "$ROOT/.venv/bin/python" manage.py spectacular --file "$schema" >/dev/null 2>&1; then
      check schema-drift diff -q "$schema" "$WORKTREE/frontend/src/api/schema.yaml"
    else
      problems+=("schema-generate")
    fi

    popd >/dev/null
  fi

  if [ -d "$WORKTREE/frontend" ]; then
    pushd "$WORKTREE/frontend" >/dev/null
    [ -e node_modules ] || ln -s "$ROOT/frontend/node_modules" node_modules

    local types="$SCRATCH/${sha}-schema.d.ts"
    if npx openapi-typescript src/api/schema.yaml -o "$types" >/dev/null 2>&1; then
      check types-drift diff -q "$types" src/api/schema.d.ts
    else
      problems+=("types-generate")
    fi

    check tsc     npx tsc --noEmit -p tsconfig.app.json
    check eslint  npm run lint --silent
    check vitest  npx vitest --run
    check build   npm run build --silent
    check budget  npm run check:budget --silent

    popd >/dev/null
  fi

  if [ ${#problems[@]} -eq 0 ]; then
    printf '  ok    %s  %s\n' "${sha:0:7}" "$subject"
    return 0
  fi

  printf '  FAIL  %s  %s  [%s]\n' "${sha:0:7}" "$subject" "${problems[*]}"
  FAILED=1
  return 1
}

# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------
#
# A verifier that cannot detect a red suite reports green for an entire range
# and is believed. Nothing else in the repository fails that way, so nothing
# else needs this.
#
# It plants a test that must fail, runs the backend half against it, and
# demands a non-zero result. Wired to run rather than to exist: CI invokes it,
# and it uses the same `check` path the real run does.
if [ "$SELF_TEST" = "1" ]; then
  echo "Self-test: planting a failing test and requiring the verifier to see it."
  git -C "$WORKTREE" checkout -q --detach HEAD

  cat > "$WORKTREE/backend/tests/test_verifier_self_test.py" <<'PLANTED'
def test_this_must_fail():
    """Planted by tools/verify_commits.sh --self-test.

    If the verifier reports green with this file present, it cannot see a red
    suite, and every green line it has ever printed means nothing.
    """
    assert False, "planted failure"
PLANTED

  problems=()
  pushd "$WORKTREE/backend" >/dev/null
  check pytest env DJANGO_SETTINGS_MODULE=config.settings.test \
    "$ROOT/.venv/bin/python" -m pytest -q
  popd >/dev/null

  rm -f "$WORKTREE/backend/tests/test_verifier_self_test.py"

  if [ ${#problems[@]} -eq 0 ]; then
    echo "SELF-TEST FAILED: a deliberately failing suite was reported as passing." >&2
    exit 3
  fi

  echo "  ok -- the planted failure was detected."
  exit 0
fi

for sha in $(git -C "$ROOT" rev-list --reverse "$BASE"..HEAD); do
  verify_one "$sha" || true
done

if [ "$FAILED" -eq 0 ]; then
  echo "Every commit passes on its own, for the checks listed above."
else
  echo "At least one commit is not green by itself; a bisect would land on it."
fi

exit "$FAILED"
