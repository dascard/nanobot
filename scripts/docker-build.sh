#!/usr/bin/env bash
set -euo pipefail

export GIT_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
export GIT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
export GIT_FULL_COMMIT="$(git rev-parse HEAD 2>/dev/null || true)"
export GIT_COMMIT_DATE="$(git log -1 --format=%ci --date=iso-strict 2>/dev/null || true)"

if git status --porcelain --untracked-files=no >/tmp/nanobot_git_status 2>/dev/null; then
  if [ -s /tmp/nanobot_git_status ]; then
    export GIT_DIRTY=true
  else
    export GIT_DIRTY=false
  fi
else
  export GIT_DIRTY=null
fi

docker compose build "$@"
