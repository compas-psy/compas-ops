#!/usr/bin/env bash
# HELM · права на каталогах, созданных В РАНТАЙМЕ. Чтение.
set -uo pipefail
sudo find /opt/helm-knowledge/users -maxdepth 2 -type d -printf '%M %u:%g %f\n' | head -6
