#!/bin/bash
# HELM v4.0 RESCUE · R0: какому коммиту соответствует код на сервере.
#
# deploy.yml раскладывает helm_core файлами и не оставляет отметки о
# коммите, поэтому «выкачено то же, что в HEAD» — предположение, а не
# факт (v4.0 §14.22 требует именно факт). Здесь считается тот же
# отпечаток, что печатает r0-truth-inventory.sh на сервере, но для
# коммитов ветки — совпадение и есть ответ.
#
# Запускается локально из корня репозитория:
#   bash helm/tree/scripts/r0-match-deployed-sha.sh <сколько коммитов>
set -euo pipefail

depth="${1:-15}"
repo_root="$(git rev-parse --show-toplevel)"

for sha in $(git -C "$repo_root" rev-list -n "$depth" HEAD); do
  tmp="$(mktemp -d)"
  if git -C "$repo_root" archive "$sha" helm/tree/control-plane/helm_core 2>/dev/null \
     | tar -x -C "$tmp" 2>/dev/null; then
    fp="$(cd "$tmp/helm/tree/control-plane" \
          && find helm_core -name '*.py' -type f -print0 | sort -z \
          | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)"
    echo "$fp  $sha  $(git -C "$repo_root" log -1 --format=%s "$sha" | cut -c1-60)"
  fi
  rm -rf "$tmp"
done
