#!/bin/bash
# Диагностика: структура /root/helm-bootstrap/backup_credentials без вывода
# значений — только имена полей/переменных, чтобы спланировать restic.
set -euo pipefail

FILE=/root/helm-bootstrap/backup_credentials

python3 - "$FILE" <<'PYEOF'
import re
import sys

path = sys.argv[1]
with open(path) as f:
    text = f.read()

print("bytes:", len(text))
print("lines:", text.count("\n") + 1)
print("keys found:", re.findall(r'^[A-Za-z_][A-Za-z0-9_]*(?==)', text, re.M))
PYEOF
