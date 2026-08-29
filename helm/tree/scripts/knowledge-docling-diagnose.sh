#!/bin/bash
# Полный traceback ошибки Docling на smoke-test-broken.pdf — job.error
# хранит только str(exc), не стектрейс. Разведка, ничего не меняет.
set -euo pipefail

cd /opt/helm/compose
sudo docker compose exec -T helm-knowledge-worker python3 - <<'PYEOF'
import traceback
from docling.document_converter import DocumentConverter

path = "/opt/helm-knowledge/raw/engineering/smoke-test-broken.pdf"
try:
    converter = DocumentConverter()
    result = converter.convert(path)
    print("convert() succeeded, trying export_to_markdown()")
    md = result.document.export_to_markdown()
    print("export_to_markdown() succeeded:")
    print(repr(md[:500]))
except Exception:
    print("=== ПОЛНЫЙ TRACEBACK ===")
    traceback.print_exc()
PYEOF
