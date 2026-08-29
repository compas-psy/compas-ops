#!/bin/bash
# Диагностика: скрытые пробелы/переносы строк/непечатаемые символы в
# backup_credentials — без вывода самих значений.
set -euo pipefail

FILE=/root/helm-bootstrap/backup_credentials

python3 - "$FILE" <<'PYEOF'
import sys

path = sys.argv[1]
with open(path, "rb") as f:
    raw = f.read()

lines = raw.split(b"\n")
for line in lines:
    if b"=" not in line:
        continue
    key, _, value = line.partition(b"=")
    key = key.decode(errors="replace")
    leading_ws = len(value) - len(value.lstrip())
    trailing_ws = len(value) - len(value.rstrip())
    non_ascii = sum(1 for b in value if b < 32 or b > 126)
    has_cr = b"\r" in value
    print(f"{key}: len={len(value)} leading_ws={leading_ws} "
          f"trailing_ws={trailing_ws} non_ascii_or_control={non_ascii} "
          f"contains_CR={has_cr}")
PYEOF
