#!/bin/bash
# E13: read-only разведка реального корпуса Knowledge перед решением, нужен
# ли слой inferred-relations и достижим ли честный multi-hop benchmark
# (устав §5.1 — сначала факт, потом решение). Ничего не пишет и не меняет.
set -euo pipefail

echo "=== knowledge_sources: сколько и что именно ==="
sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc \
  "select id, knowledge_user_id, original_filename, domain, parser, status, created_at from knowledge_sources order by created_at"

echo "=== knowledge_notes: сколько и какие slug ==="
sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc \
  "select count(*) from knowledge_notes" || echo "таблицы нет"

echo "=== knowledge_relations: сколько строк уже есть ==="
sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc \
  "select count(*) from knowledge_relations" || echo "таблицы нет"

echo "=== knowledge_chunks: есть ли текст с [[wikilink]] прямо в базе ==="
sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc \
  "select count(*) from knowledge_chunks where text like '%[[%]]%'" || echo "таблицы нет/колонки нет"

echo "=== реальных владельцев (knowledge_users), не считая тестовых ==="
sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc \
  "select id, role, status, created_at from knowledge_users order by created_at"
