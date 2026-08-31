# graph/INDEX.md — указатель по документам compas-ops

Сгенерировано `tools/graph_index.py` из `graph/ops/graph.json` — не редактировать руками. Пересборка:
```
python3 tools/graphify.py build
python3 tools/graph_index.py graph/ops/graph.json graph/INDEX.md
```

Тем в индексе: 203. Полные связи и провенанс — `graphify explain "<id>" --graph graph/ops/graph.json`.

## ADR

- `ADR-009` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/docs/adr/ADR-009-action-registry.md`, `helm/tree/docs/adr/ADR-025-hybrid-retrieval.md`, `helm/tree/docs/adr/README.md`
- `ADR-012` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/docs/adr/ADR-012-durable-execution.md`, `helm/tree/docs/adr/README.md`
- `ADR-013` — `helm/PLAN_HELM_v3.3.md`
- `ADR-014` — `helm/tree/control-plane/tests/test_max_channel.py`, `helm/tree/docs/adr/ADR-014-max-responses-api.md`, `helm/tree/docs/adr/README.md`, `helm/tree/implementation-state/WORKPLAN.md`, `helm/tree/scripts/BATCH-01.md`, `helm/tree/scripts/hermes-enable-runbook.md` (+1)
- `ADR-015` — `helm/tree/docs/KNOWLEDGE_MODELS.md`
- `ADR-017` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/README.md`, `helm/tree/docs/adr/ADR-012-durable-execution.md`, `helm/tree/docs/adr/ADR-017-offline-build.md`, `helm/tree/docs/adr/README.md`, `helm/tree/implementation-state/WORKPLAN.md`
- `ADR-018` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/docs/KNOWLEDGE_INGEST.md`, `helm/tree/docs/adr/ADR-018-single-agent.md`, `helm/tree/docs/adr/ADR-102-knowledge-attachment-transport.md`, `helm/tree/docs/adr/README.md`, `helm/tree/hermes/plugins/helm-control/__init__.py` (+3)
- `ADR-019` — `helm/tree/docs/KNOWLEDGE_MODELS.md`, `helm/tree/docs/adr/README.md`
- `ADR-020` — `helm/tree/docs/adr/README.md`, `helm/tree/scripts/hermes-recon-2.sh`, `helm/tree/scripts/hermes-recon-3.sh`, `helm/tree/scripts/hermes-recon-4.sh`, `helm/tree/scripts/hermes-recon-5.sh`, `helm/tree/scripts/hermes-recon.sh` (+4)
- `ADR-021` — `helm/tree/compose/docker-compose.yml`, `helm/tree/control-plane/helm_core/api/internal.py`, `helm/tree/control-plane/helm_core/knowledge/audio.py`, `helm/tree/control-plane/helm_core/knowledge/chat_intake.py`, `helm/tree/control-plane/helm_core/knowledge/parsers.py`, `helm/tree/control-plane/helm_core/knowledge/rephrase.py` (+17)
- `ADR-024` — `helm/tree/control-plane/helm_core/knowledge/chat_intake.py`, `helm/tree/control-plane/helm_core/models/tables.py`, `helm/tree/docs/KNOWLEDGE_MODELS.md`, `helm/tree/docs/adr/ADR-024-dynamic-domain-registry.md`, `helm/tree/docs/adr/README.md`, `helm/tree/implementation-state/ROADMAP-TO-DONE.md`
- `ADR-025` — `helm/tree/compose/docker-compose.yml`, `helm/tree/control-plane/helm_core/knowledge/audio.py`, `helm/tree/control-plane/helm_core/knowledge/embed_service.py`, `helm/tree/control-plane/helm_core/knowledge/embeddings.py`, `helm/tree/control-plane/helm_core/knowledge/ingest.py`, `helm/tree/control-plane/helm_core/knowledge/probe.py` (+9)
- `ADR-026` — `helm/tree/docs/KNOWLEDGE.md`, `helm/tree/docs/KNOWLEDGE_MODELS.md`, `helm/tree/docs/adr/ADR-026-zip-batch-ingest.md`, `helm/tree/docs/adr/ADR-029-dedicated-knowledge-telegram-bot.md`, `helm/tree/docs/adr/README.md`, `helm/tree/implementation-state/ROADMAP-TO-DONE.md` (+1)
- `ADR-029` — `helm/tree/docs/KNOWLEDGE.md`, `helm/tree/docs/adr/ADR-029-dedicated-knowledge-telegram-bot.md`, `helm/tree/docs/adr/README.md`, `helm/tree/implementation-state/ROADMAP-TO-DONE.md`, `helm/tree/implementation-state/V3.8-ACCEPTANCE.md`, `helm/tree/implementation-state/V3.8-DELTA.md` (+1)
- `ADR-101` — `helm/tree/control-plane/helm_core/models/base.py`, `helm/tree/docs/adr/ADR-101-timezone-moscow.md`, `helm/tree/docs/adr/README.md`
- `ADR-102` — `helm/tree/docs/KNOWLEDGE.md`, `helm/tree/docs/KNOWLEDGE_INGEST.md`, `helm/tree/docs/adr/ADR-029-dedicated-knowledge-telegram-bot.md`, `helm/tree/docs/adr/ADR-102-knowledge-attachment-transport.md`, `helm/tree/docs/adr/README.md`, `helm/tree/hermes/plugins/helm-control/__init__.py` (+2)
- `ADR-103` — `helm/tree/control-plane/tests/test_api.py`, `helm/tree/docs/adr/ADR-103-no-knowledge-cache.md`, `helm/tree/implementation-state/ROADMAP-TO-DONE.md`

## §-раздел спеки

- `§1.1` — `state/15_ORDERS.md`
- `§1.2` — `charter/13_TRACKING_PLAN.md`, `log/20_DAILY_LOG.md`, `product/practice/CJM_booking_v1.md`, `product/practice/CJM_booking_v2.md`, `state/15_ORDERS.md`
- `§1.3` — `board/board_2026-08-17_N1.md`, `legal/CLIENT_CONSENT_BASIS.md`, `log/21_BOARD_LOG.md`, `product/practice/CJM_booking_v1.md`, `state/10_BACKLOG.md`, `state/11_HUMAN_QUEUE.md` (+2)
- `§1.4` — `charter/07_REPOS.md`, `legal/CLIENT_CONSENT_BASIS.md`, `state/10_BACKLOG.md`, `state/15_ORDERS.md`
- `§1.5` — `log/20_DAILY_LOG.md`, `product/moments/D0_TRACKING_PLAN.md`, `state/10_BACKLOG.md`, `state/12_DECISIONS.md`
- `§10.1` — `helm/tree/compose/docker-compose.yml`, `helm/tree/control-plane/helm_core/api/hooks.py`, `helm/tree/control-plane/helm_core/channels/max.py`, `helm/tree/control-plane/helm_core/config.py`, `helm/tree/control-plane/helm_core/hermes_bridge.py`, `helm/tree/control-plane/tests/test_max_channel.py` (+3)
- `§10.2` — `charter/03_SOURCES.md`, `charter/05_METRICS.md`, `helm/tree/compose/docker-compose.yml`, `helm/tree/control-plane/helm_core/api/hooks.py`, `helm/tree/control-plane/helm_core/dispatch.py`, `helm/tree/control-plane/helm_core/hermes_bridge.py` (+12)
- `§10.3` — `helm/tree/compose/docker-compose.yml`, `helm/tree/control-plane/helm_core/api/hooks.py`, `helm/tree/control-plane/helm_core/api/internal.py`, `helm/tree/control-plane/helm_core/app.py`, `helm/tree/control-plane/helm_core/channels/max.py`, `helm/tree/control-plane/helm_core/channels/telegram.py` (+8)
- `§10.4` — `helm/tree/control-plane/helm_core/api/hooks.py`, `helm/tree/control-plane/helm_core/config.py`, `helm/tree/control-plane/helm_core/ingest.py`, `helm/tree/control-plane/tests/test_max_channel.py`, `helm/tree/docs/adr/ADR-014-max-responses-api.md`, `helm/tree/implementation-state/WORKPLAN.md`
- `§10.5.1` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/panel/README.md`
- `§10.5.11` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/panel/README.md`
- `§10.5.2` — `helm/tree/panel/README.md`
- `§10.5.5` — `helm/tree/control-plane/helm_core/api/panel.py`
- `§10.5.6` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/control-plane/helm_core/api/auth.py`, `helm/tree/control-plane/helm_core/api/deps.py`, `helm/tree/control-plane/tests/test_panel_auth.py`, `helm/tree/implementation-state/WORKPLAN.md`
- `§10.5.7` — `helm/tree/control-plane/helm_core/api/auth.py`, `helm/tree/control-plane/helm_core/config.py`, `helm/tree/control-plane/helm_core/models/tables.py`, `helm/tree/control-plane/tests/test_panel_auth.py`, `helm/tree/implementation-state/WORKPLAN.md`
- `§10.5.8` — `helm/tree/control-plane/helm_core/api/auth.py`, `helm/tree/control-plane/helm_core/api/deps.py`, `helm/tree/control-plane/helm_core/api/panel.py`, `helm/tree/control-plane/tests/test_panel_auth.py`
- `§10.5.8.1` — `helm/tree/control-plane/helm_core/api/auth.py`, `helm/tree/control-plane/helm_core/api/deps.py`, `helm/tree/control-plane/helm_core/api/panel.py`, `helm/tree/control-plane/helm_core/config.py`, `helm/tree/control-plane/helm_core/models/tables.py`, `helm/tree/control-plane/tests/test_panel_auth.py` (+4)
- `§10.5.8.2` — `helm/tree/control-plane/helm_core/api/auth.py`, `helm/tree/implementation-state/WORKPLAN.md`, `helm/tree/scripts/panel-passkey-recover.py`
- `§10.5.9` — `helm/tree/guardian/guardian.py`
- `§11.2` — `helm/tree/implementation-state/WORKPLAN.md`, `helm/tree/scripts/provision_hermes_profiles.sh`
- `§11.4` — `helm/tree/implementation-state/WORKPLAN.md`
- `§14.0` — `helm/tree/implementation-state/V3.4-DELTA.md`
- `§14.1` — `helm/tree/control-plane/helm_core/knowledge/documents.py`, `helm/tree/control-plane/helm_core/knowledge/worker.py`, `helm/tree/control-plane/helm_core/models/tables.py`, `helm/tree/control-plane/tests/test_api.py`, `helm/tree/control-plane/tests/test_knowledge_documents.py`, `helm/tree/control-plane/tests/test_knowledge_worker.py` (+2)
- `§14.10` — `helm/tree/control-plane/helm_core/knowledge/memory.py`, `helm/tree/control-plane/helm_core/knowledge/onboarding.py`, `helm/tree/control-plane/helm_core/knowledge/probe.py`, `helm/tree/control-plane/helm_core/knowledge/recall.py`, `helm/tree/control-plane/helm_core/knowledge/worker.py`, `helm/tree/control-plane/helm_core/models/base.py` (+8)
- `§14.11` — `helm/tree/control-plane/helm_core/api/hooks.py`, `helm/tree/control-plane/helm_core/api/hooks_knowledge_telegram.py`, `helm/tree/control-plane/helm_core/api/internal.py`, `helm/tree/control-plane/helm_core/knowledge/admin.py`, `helm/tree/control-plane/helm_core/knowledge/memory.py`, `helm/tree/control-plane/helm_core/knowledge/probe.py` (+11)
- `§14.12` — `.github/workflows/deploy.yml`, `helm/tree/compose/docker-compose.yml`, `helm/tree/control-plane/helm_core/knowledge/embed_benchmark.py`, `helm/tree/control-plane/helm_core/knowledge/embeddings.py`, `helm/tree/control-plane/helm_core/knowledge/probe.py`, `helm/tree/control-plane/helm_core/knowledge/recall.py` (+15)
- `§14.13` — `helm/tree/control-plane/helm_core/knowledge/probe.py`, `helm/tree/control-plane/helm_core/knowledge/recall.py`, `helm/tree/control-plane/tests/test_knowledge_probe.py`, `helm/tree/control-plane/tests/test_knowledge_recall.py`, `helm/tree/docs/KNOWLEDGE_RETRIEVAL.md`, `helm/tree/implementation-state/V3.8-DELTA.md`
- `§14.14` — `helm/tree/control-plane/helm_core/api/hooks.py`, `helm/tree/control-plane/helm_core/api/internal.py`, `helm/tree/control-plane/helm_core/knowledge/probe.py`, `helm/tree/control-plane/helm_core/knowledge/recall.py`, `helm/tree/control-plane/helm_core/knowledge/style.py`, `helm/tree/control-plane/helm_core/models/tables.py` (+10)
- `§14.15` — `helm/tree/control-plane/helm_core/api/panel.py`, `helm/tree/control-plane/helm_core/knowledge/batch_intake.py`, `helm/tree/control-plane/helm_core/knowledge/chat_intake.py`, `helm/tree/control-plane/helm_core/knowledge/documents.py`, `helm/tree/control-plane/helm_core/knowledge/probe.py`, `helm/tree/control-plane/helm_core/knowledge/recall.py` (+15)
- `§14.16` — `helm/tree/control-plane/helm_core/api/hooks.py`, `helm/tree/control-plane/helm_core/api/hooks_knowledge_telegram.py`, `helm/tree/control-plane/helm_core/api/internal.py`, `helm/tree/control-plane/helm_core/knowledge/admin.py`, `helm/tree/control-plane/helm_core/knowledge/recall.py`, `helm/tree/control-plane/tests/test_api.py` (+8)
- `§14.17` — `helm/tree/control-plane/tests/test_api.py`, `helm/tree/docs/adr/ADR-103-no-knowledge-cache.md`, `helm/tree/implementation-state/ROADMAP-TO-DONE.md`
- `§14.18` — `helm/tree/control-plane/helm_core/api/hooks_knowledge_telegram.py`, `helm/tree/control-plane/helm_core/app.py`, `helm/tree/implementation-state/V3.4-DELTA.md`
- `§14.2` — `helm/tree/control-plane/helm_core/knowledge/ingest.py`, `helm/tree/control-plane/helm_core/knowledge/tenancy.py`, `helm/tree/control-plane/helm_core/knowledge/worker.py`, `helm/tree/control-plane/helm_core/models/base.py`, `helm/tree/control-plane/helm_core/models/tables.py`, `helm/tree/control-plane/migrations/versions/ef1ba5467e14_knowledge_tenancy_v3_8.py` (+5)
- `§14.3` — `helm/tree/config/policies/actions.yaml`, `helm/tree/control-plane/helm_core/api/auth.py`, `helm/tree/control-plane/helm_core/api/deps.py`, `helm/tree/control-plane/helm_core/api/hooks_knowledge_telegram.py`, `helm/tree/control-plane/helm_core/api/internal.py`, `helm/tree/control-plane/helm_core/api/panel.py` (+17)
- `§14.4` — `helm/tree/control-plane/helm_core/knowledge/batch_intake.py`, `helm/tree/control-plane/helm_core/knowledge/chat_intake.py`, `helm/tree/control-plane/helm_core/knowledge/ingest.py`, `helm/tree/control-plane/helm_core/knowledge/probe.py`, `helm/tree/control-plane/helm_core/knowledge/quotas.py`, `helm/tree/control-plane/helm_core/knowledge/recall.py` (+13)
- `§14.4.0` — `helm/tree/control-plane/helm_core/api/hooks.py`, `helm/tree/control-plane/helm_core/api/internal.py`, `helm/tree/control-plane/helm_core/knowledge/batch_intake.py`, `helm/tree/control-plane/helm_core/knowledge/zip_safety.py`, `helm/tree/control-plane/helm_core/models/base.py`, `helm/tree/control-plane/helm_core/models/tables.py` (+4)
- `§14.5` — `helm/tree/control-plane/helm_core/knowledge/chat_intake.py`, `helm/tree/control-plane/helm_core/knowledge/ingest.py`, `helm/tree/control-plane/helm_core/models/tables.py`, `helm/tree/control-plane/tests/test_knowledge_chat_intake.py`, `helm/tree/control-plane/tests/test_knowledge_probe.py`, `helm/tree/docs/KNOWLEDGE_INGEST.md` (+1)
- `§14.5.1` — `helm/tree/compose/docker-compose.yml`, `helm/tree/control-plane/helm_core/api/internal.py`, `helm/tree/control-plane/helm_core/knowledge/batch_intake.py`, `helm/tree/control-plane/helm_core/knowledge/chat_intake.py`, `helm/tree/control-plane/helm_core/knowledge/ingest.py`, `helm/tree/control-plane/helm_core/knowledge/worker.py` (+12)
- `§14.5.2` — `helm/tree/control-plane/helm_core/knowledge/batch_intake.py`, `helm/tree/control-plane/helm_core/knowledge/worker.py`, `helm/tree/control-plane/helm_core/models/tables.py`, `helm/tree/docs/adr/ADR-026-zip-batch-ingest.md`, `helm/tree/implementation-state/V3.7-DELTA.md`, `helm/tree/implementation-state/WORKPLAN.md`
- `§14.6` — `helm/tree/control-plane/helm_core/knowledge/batch_intake.py`, `helm/tree/control-plane/helm_core/knowledge/parsers.py`, `helm/tree/control-plane/helm_core/knowledge/worker.py`, `helm/tree/control-plane/helm_core/models/base.py`, `helm/tree/control-plane/helm_core/models/tables.py`, `helm/tree/control-plane/tests/test_knowledge_parsers.py` (+4)
- `§14.7` — `helm/tree/control-plane/helm_core/knowledge/audio.py`, `helm/tree/control-plane/helm_core/knowledge/gigaam_benchmark.py`, `helm/tree/control-plane/helm_core/knowledge/parsers.py`, `helm/tree/control-plane/helm_core/models/tables.py`, `helm/tree/control-plane/tests/test_knowledge_parsers.py`, `helm/tree/docs/KNOWLEDGE_INGEST.md` (+3)
- `§14.7.6` — `helm/tree/control-plane/helm_core/knowledge/batch_intake.py`, `helm/tree/control-plane/helm_core/knowledge/zip_safety.py`, `helm/tree/control-plane/helm_core/models/tables.py`, `helm/tree/control-plane/tests/test_knowledge_zip_safety.py`, `helm/tree/docs/adr/ADR-026-zip-batch-ingest.md`, `helm/tree/implementation-state/V3.7-DELTA.md`
- `§14.7.7` — `helm/tree/implementation-state/V3.7-DELTA.md`
- `§14.9` — `helm/tree/control-plane/helm_core/knowledge/ingest.py`, `helm/tree/control-plane/helm_core/models/tables.py`, `helm/tree/docs/KNOWLEDGE_INGEST.md`, `helm/tree/docs/KNOWLEDGE_MODELS.md`, `helm/tree/docs/KNOWLEDGE_RETRIEVAL.md`, `helm/tree/implementation-state/V3.4-DELTA.md`
- `§15.3` — `helm/tree/config/models/litellm.yaml`, `helm/tree/implementation-state/WORKPLAN.md`, `helm/tree/scripts/provision_hermes_profiles.sh`
- `§15.4` — `helm/tree/config/models/litellm.yaml`, `helm/tree/implementation-state/WORKPLAN.md`, `helm/tree/scripts/provision_hermes_profiles.sh`
- `§15.6` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/config/models/litellm.yaml`, `helm/tree/implementation-state/WORKPLAN.md`
- `§15.7` — `helm/tree/implementation-state/WORKPLAN.md`
- `§15.7.1` — `helm/tree/implementation-state/WORKPLAN.md`
- `§17.1` — `helm/tree/compose/docker-compose.yml`
- `§17.2` — `helm/tree/implementation-state/WORKPLAN.md`
- `§17.4` — `helm/tree/compose/docker-compose.yml`, `helm/tree/implementation-state/WORKPLAN.md`
- `§17.5` — `helm/tree/scripts/backup.sh`, `helm/tree/scripts/n8n-export-runbook.md`, `helm/tree/scripts/n8n-workflows.py`
- `§17.6` — `helm/tree/scripts/n8n-workflows.py`
- `§18.1` — `helm/tree/compose/docker-compose.yml`
- `§18.2` — `helm/tree/scripts/forgejo-migrate-runbook.md`, `helm/tree/scripts/forgejo-migrate.py`
- `§18.3` — `helm/tree/implementation-state/MIGRATION-LOG.md`, `helm/tree/implementation-state/WORKPLAN.md`, `helm/tree/scripts/BATCH-01.md`, `helm/tree/scripts/forgejo-migrate-runbook.md`, `helm/tree/scripts/forgejo-migrate.py`, `helm/tree/scripts/restore_test.sh`
- `§18.4` — `helm/tree/scripts/backup.sh`, `helm/tree/scripts/forgejo-migrate-runbook.md`, `helm/tree/scripts/forgejo-migrate.py`
- `§18.5` — `helm/tree/implementation-state/MIGRATION-LOG.md`, `helm/tree/scripts/forgejo-migrate-runbook.md`
- `§18.6` — `helm/tree/implementation-state/MIGRATION-LOG.md`
- `§18.7` — `helm/tree/scripts/backup.sh`, `helm/tree/scripts/forgejo-migrate-runbook.md`, `helm/tree/scripts/n8n-export-runbook.md`, `helm/tree/scripts/restore_test.sh`
- `§2.1` — `charter/03_SOURCES.md`, `charter/05_METRICS.md`, `charter/09_COWORK.md`, `product/practice/CJM_booking_v2.md`, `state/11_HUMAN_QUEUE.md`, `state/13_DATA_INBOX.md`
- `§2.2` — `charter/05_METRICS.md`, `charter/09_COWORK.md`, `state/13_DATA_INBOX.md`
- `§2.3` — `charter/03_SOURCES.md`, `charter/09_COWORK.md`, `daily/daily_2026-08-25.md`, `log/20_DAILY_LOG.md`, `product/moments/D0_TRACKING_PLAN.md`, `state/10_BACKLOG.md` (+2)
- `§2.4` — `helm/PLAN_HELM_v3.3.md`, `state/14_SIGNALS_INBOX.md`
- `§2.5` — `state/14_SIGNALS_INBOX.md`
- `§25.2` — `helm/tree/guardian/guardian.py`, `helm/tree/scripts/restore_test.sh`
- `§25.3` — `helm/tree/guardian/guardian.py`, `helm/tree/guardian/tests/test_guardian.py`
- `§25.5` — `helm/tree/control-plane/tests/test_perimeter.py`, `helm/tree/guardian/guardian.py`, `helm/tree/implementation-state/WORKPLAN.md`
- `§25.6` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/compose/docker-compose.yml`, `helm/tree/control-plane/tests/test_perimeter.py`, `helm/tree/guardian/cleanup.sh`, `helm/tree/guardian/tests/test_guardian.py`, `helm/tree/implementation-state/WORKPLAN.md` (+1)
- `§26.1` — `helm/tree/implementation-state/V3.4-DELTA.md`, `helm/tree/implementation-state/WORKPLAN.md`, `helm/tree/scripts/backup.sh`
- `§26.4` — `helm/tree/guardian/guardian.py`, `helm/tree/scripts/restore_test.sh`
- `§3.1` — `charter/05_METRICS.md`, `helm/tree/control-plane/helm_core/api/panel.py`, `legal/CLIENT_CONSENT_BASIS.md`
- `§3.10` — `charter/05_METRICS.md`
- `§3.11` — `charter/05_METRICS.md`, `charter/13_TRACKING_PLAN.md`
- `§3.12` — `charter/05_METRICS.md`
- `§3.13` — `charter/05_METRICS.md`
- `§3.14` — `charter/05_METRICS.md`
- `§3.15` — `charter/05_METRICS.md`
- `§3.16` — `charter/05_METRICS.md`
- `§3.17` — `charter/05_METRICS.md`
- `§3.18` — `charter/05_METRICS.md`
- `§3.19` — `charter/05_METRICS.md`, `log/20_DAILY_LOG.md`, `state/10_BACKLOG.md`
- `§3.2` — `charter/05_METRICS.md`, `helm/tree/config/policies/actions.yaml`, `helm/tree/control-plane/helm_core/api/panel.py`, `legal/CLIENT_CONSENT_BASIS.md`, `state/15_ORDERS.md`
- `§3.3` — `charter/05_METRICS.md`, `helm/tree/control-plane/helm_core/api/panel.py`, `legal/CLIENT_CONSENT_BASIS.md`, `product/moments/D0_TRACKING_PLAN.md`
- `§3.4` — `charter/05_METRICS.md`, `helm/tree/control-plane/helm_core/api/panel.py`, `product/moments/D0_TRACKING_PLAN.md`, `state/10_BACKLOG.md`, `state/12_DECISIONS.md`
- `§3.5` — `charter/05_METRICS.md`, `helm/tree/control-plane/helm_core/api/panel.py`
- `§3.6` — `charter/05_METRICS.md`
- `§3.7` — `charter/05_METRICS.md`, `product/moments/D0_TRACKING_PLAN.md`
- `§3.9` — `charter/05_METRICS.md`
- `§30.1` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/control-plane/helm_core/models/base.py`
- `§30.10` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/README.md`, `helm/tree/guardian/guardian.py`, `helm/tree/guardian/tests/test_guardian.py`
- `§30.11` — `helm/PLAN_HELM_v3.3.md`
- `§30.12` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/control-plane/helm_core/approvals/service.py`, `helm/tree/control-plane/tests/README.md`, `helm/tree/control-plane/tests/test_red_gate.py`, `helm/tree/docs/adr/ADR-009-action-registry.md`
- `§30.2` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/README.md`, `helm/tree/control-plane/helm_core/api/internal.py`, `helm/tree/control-plane/helm_core/approvals/service.py`, `helm/tree/control-plane/helm_core/ingest.py`, `helm/tree/control-plane/helm_core/models/tables.py` (+5)
- `§30.3` — `helm/PLAN_HELM_v3.3.md`
- `§30.4` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/implementation-state/WORKPLAN.md`
- `§30.5` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/docs/adr/ADR-014-max-responses-api.md`, `helm/tree/scripts/max-bringup-runbook.md`
- `§30.6` — `helm/PLAN_HELM_v3.3.md`
- `§30.7` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/control-plane/helm_core/api/panel.py`, `helm/tree/control-plane/helm_core/api/security.py`, `helm/tree/control-plane/tests/test_api.py`, `helm/tree/guardian/guardian.py`, `helm/tree/guardian/tests/test_guardian.py`
- `§30.8` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/implementation-state/V3.4-DELTA.md`, `helm/tree/implementation-state/WORKPLAN.md`
- `§30.8.5` — `helm/tree/control-plane/helm_core/knowledge/probe.py`, `helm/tree/control-plane/helm_core/knowledge/recall.py`, `helm/tree/control-plane/tests/test_knowledge_probe.py`, `helm/tree/docs/KNOWLEDGE_INGEST.md`, `helm/tree/docs/KNOWLEDGE_RETRIEVAL.md`, `helm/tree/docs/adr/ADR-025-hybrid-retrieval.md` (+3)
- `§30.9` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/control-plane/helm_core/api/security.py`, `helm/tree/control-plane/tests/test_perimeter.py`
- `§31.0` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/README.md`, `helm/tree/docs/adr/ADR-017-offline-build.md`, `helm/tree/docs/adr/ADR-018-single-agent.md`, `helm/tree/implementation-state/WORKPLAN.md`, `helm/tree/scripts/checkpoint.sh`
- `§31.0.1` — `helm/tree/implementation-state/V3.4-DELTA.md`, `helm/tree/implementation-state/V3.7-DELTA.md`, `helm/tree/implementation-state/V3.8-DELTA.md`
- `§4.1` — `charter/03_SOURCES.md`, `helm/PLAN_HELM_v3.3.md`, `helm/tree/compose/docker-compose.yml`, `helm/tree/docs/adr/ADR-025-hybrid-retrieval.md`, `helm/tree/docs/adr/ADR-101-timezone-moscow.md`, `legal/CLIENT_CONSENT_BASIS.md`
- `§4.2` — `helm/tree/compose/docker-compose.yml`
- `§4.3` — `charter/05_METRICS.md`
- `§4.3.1` — `helm/tree/control-plane/helm_core/knowledge/embed_service.py`
- `§4.4` — `charter/03_SOURCES.md`
- `§4.5` — `charter/05_METRICS.md`, `charter/13_TRACKING_PLAN.md`, `daily/daily_2026-08-15.md`, `daily/daily_2026-08-16.md`, `daily/daily_2026-08-17.md`, `daily/daily_2026-08-18.md` (+11)
- `§4.6` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/README.md`, `helm/tree/compose/docker-compose.yml`, `helm/tree/control-plane/helm_core/app.py`, `helm/tree/control-plane/tests/test_api.py`, `helm/tree/control-plane/tests/test_perimeter.py` (+1)
- `§5.1` — `helm/tree/control-plane/helm_core/knowledge/chat_intake.py`, `helm/tree/scripts/knowledge-probe-smoke-test.sh`, `state/15_ORDERS.md`
- `§5.2` — `charter/00_ORG_CHARTER.md`, `charter/05_METRICS.md`, `helm/tree/control-plane/helm_core/api/panel.py`, `helm/tree/control-plane/helm_core/knowledge/chat_intake.py`, `helm/tree/docs/adr/ADR-014-max-responses-api.md`, `helm/tree/docs/adr/ADR-021-gigaam-voice-pipeline.md` (+2)
- `§5.3` — `daily/daily_2026-08-15.md`, `legal/CLIENT_CONSENT_BASIS.md`, `log/20_DAILY_LOG.md`, `state/11_HUMAN_QUEUE.md`
- `§5.4` — `.github/workflows/deploy.yml`, `HANDOFF.md`, `charter/11_SKILLS.md`, `daily/daily_2026-08-18.md`, `helm/PLAN_HELM_v3.3.md`, `helm/tree/implementation-state/ROADMAP-TO-DONE.md` (+6)
- `§5.5` — `helm/tree/scripts/disk-cleanup.sh`
- `§5.6` — `helm/tree/control-plane/helm_core/actions/fixtures.py`, `helm/tree/control-plane/tests/test_red_gate.py`
- `§5.7` — `helm/PLAN_HELM_v3.3.md`, `log/20_DAILY_LOG.md`, `tools/graph_index.py`, `tools/graphify.py`
- `§6.1` — `HANDOFF.md`, `charter/02_ROLES_CLEVEL.md`, `charter/03_SOURCES.md`, `charter/04_TEMPLATES.md`, `charter/05_METRICS.md`, `charter/07_REPOS.md` (+15)
- `§6.2` — `charter/05_METRICS.md`, `charter/06_DELIVERY.md`, `helm/PLAN_HELM_v3.3.md`, `legal/CLIENT_CONSENT_BASIS.md`, `log/21_BOARD_LOG.md`, `state/11_HUMAN_QUEUE.md` (+1)
- `§6.3` — `charter/03_SOURCES.md`, `charter/05_METRICS.md`, `helm/tree/compose/docker-compose.yml`, `helm/tree/control-plane/helm_core/config.py`, `state/11_HUMAN_QUEUE.md`
- `§6.4` — `charter/02_ROLES_CLEVEL.md`, `charter/03_SOURCES.md`, `charter/04_TEMPLATES.md`, `charter/06_DELIVERY.md`, `charter/08_SCHEDULES.md`, `charter/09_COWORK.md` (+2)
- `§6.6` — `helm/PLAN_HELM_v3.3.md`
- `§7.2` — `charter/05_METRICS.md`, `helm/PLAN_HELM_v3.3.md`, `helm/tree/control-plane/helm_core/approvals/service.py`, `helm/tree/control-plane/helm_core/models/base.py`, `helm/tree/control-plane/helm_core/models/tables.py`, `helm/tree/control-plane/helm_core/outbox.py`
- `§7.3` — `helm/tree/control-plane/helm_core/api/internal.py`, `helm/tree/control-plane/helm_core/api/panel.py`, `helm/tree/control-plane/helm_core/api/security.py`
- `§7.4` — `helm/PLAN_HELM_v3.3.md`, `helm/tree/docs/adr/ADR-012-durable-execution.md`
- `§7.5` — `helm/tree/control-plane/helm_core/models/base.py`
- `§8.1` — `helm/tree/config/policies/actions.yaml`, `helm/tree/control-plane/helm_core/actions/policy.py`, `helm/tree/control-plane/helm_core/approvals/service.py`, `helm/tree/control-plane/tests/test_30_2_control_plane.py`, `helm/tree/control-plane/tests/test_red_gate.py`
- `§8.2` — `helm/tree/config/policies/actions.yaml`, `helm/tree/control-plane/helm_core/actions/policy.py`, `helm/tree/control-plane/helm_core/approvals/service.py`
- `§8.3` — `helm/tree/config/policies/actions.yaml`, `helm/tree/control-plane/helm_core/actions/canonical.py`, `helm/tree/control-plane/helm_core/actions/registry.py`, `helm/tree/control-plane/helm_core/approvals/service.py`, `helm/tree/control-plane/tests/README.md`, `helm/tree/control-plane/tests/test_canonical.py`
- `§8.4` — `helm/tree/config/policies/actions.yaml`, `helm/tree/control-plane/helm_core/actions/canonical.py`, `helm/tree/control-plane/helm_core/actions/policy.py`, `helm/tree/control-plane/helm_core/actions/registry.py`, `helm/tree/control-plane/helm_core/api/internal.py`, `helm/tree/control-plane/helm_core/approvals/service.py` (+4)
- `§8.5` — `helm/tree/control-plane/helm_core/api/internal.py`, `helm/tree/control-plane/helm_core/approvals/service.py`, `helm/tree/control-plane/helm_core/models/tables.py`
- `§8.7` — `helm/tree/config/policies/actions.yaml`, `helm/tree/control-plane/helm_core/actions/policy.py`, `helm/tree/control-plane/helm_core/approvals/service.py`, `helm/tree/control-plane/helm_core/models/tables.py`, `helm/tree/control-plane/tests/test_30_2_control_plane.py`
- `§9.0` — `helm/tree/control-plane/helm_core/api/hooks_knowledge_telegram.py`, `helm/tree/control-plane/helm_core/api/internal.py`, `helm/tree/control-plane/helm_core/app.py`, `helm/tree/control-plane/helm_core/channels/telegram_knowledge.py`, `helm/tree/control-plane/helm_core/config.py`, `helm/tree/control-plane/helm_core/knowledge/onboarding.py` (+7)
- `§9.1` — `charter/03_SOURCES.md`, `helm/tree/implementation-state/WORKPLAN.md`, `helm/tree/scripts/provision_hermes_profiles.sh`
- `§9.3` — `charter/03_SOURCES.md`, `charter/05_METRICS.md`, `helm/tree/control-plane/helm_core/api/internal.py`, `helm/tree/control-plane/helm_core/ingest.py`, `helm/tree/control-plane/tests/test_api.py`, `helm/tree/hermes/plugins/helm-control/__init__.py` (+3)
- `§9.6` — `state/15_ORDERS.md`

## пункт роадмапа (P8.x)

- `P8.5` — `helm/tree/control-plane/helm_core/knowledge/embed_benchmark.py`, `helm/tree/docs/KNOWLEDGE.md`, `helm/tree/docs/KNOWLEDGE_MODELS.md`, `helm/tree/docs/adr/ADR-025-hybrid-retrieval.md`, `helm/tree/implementation-state/V3.4-DELTA.md`, `helm/tree/implementation-state/V3.7-DELTA.md` (+4)
- `P8.5.0` — `helm/tree/control-plane/helm_core/models/tables.py`, `helm/tree/implementation-state/ROADMAP-TO-DONE.md`
- `P8.5.1` — `helm/tree/control-plane/tests/test_knowledge_probe.py`, `helm/tree/docs/KNOWLEDGE.md`, `helm/tree/docs/KNOWLEDGE_INGEST.md`, `helm/tree/implementation-state/V3.4-DELTA.md`, `helm/tree/implementation-state/V3.7-DELTA.md`, `helm/tree/implementation-state/WORKPLAN.md` (+1)
- `P8.5.11` — `helm/tree/implementation-state/V3.7-DELTA.md`
- `P8.5.12` — `helm/tree/control-plane/helm_core/api/hooks.py`, `helm/tree/control-plane/helm_core/api/internal.py`, `helm/tree/control-plane/helm_core/knowledge/memory.py`, `helm/tree/control-plane/tests/test_api.py`, `helm/tree/control-plane/tests/test_knowledge_memory.py`, `helm/tree/control-plane/tests/test_knowledge_recall.py` (+6)
- `P8.5.2` — `helm/tree/compose/docker-compose.yml`, `helm/tree/control-plane/helm_core/api/hooks.py`, `helm/tree/control-plane/helm_core/api/internal.py`, `helm/tree/control-plane/helm_core/channels/max.py`, `helm/tree/control-plane/helm_core/knowledge/ingest.py`, `helm/tree/control-plane/helm_core/knowledge/parsers.py` (+24)
- `P8.5.3` — `helm/tree/control-plane/helm_core/knowledge/gigaam_benchmark.py`, `helm/tree/docs/KNOWLEDGE.md`, `helm/tree/docs/KNOWLEDGE_INGEST.md`, `helm/tree/implementation-state/ROADMAP-TO-DONE.md`, `helm/tree/implementation-state/V3.4-DELTA.md`, `helm/tree/implementation-state/V3.7-DELTA.md` (+1)
- `P8.5.4` — `helm/tree/control-plane/helm_core/knowledge/embed_benchmark.py`, `helm/tree/control-plane/helm_core/models/tables.py`, `helm/tree/docs/KNOWLEDGE.md`, `helm/tree/docs/KNOWLEDGE_RETRIEVAL.md`, `helm/tree/docs/adr/ADR-024-dynamic-domain-registry.md`, `helm/tree/docs/adr/ADR-103-no-knowledge-cache.md` (+6)
- `P8.5.5` — `helm/tree/docs/KNOWLEDGE.md`, `helm/tree/docs/adr/ADR-103-no-knowledge-cache.md`, `helm/tree/implementation-state/ROADMAP-TO-DONE.md`, `helm/tree/implementation-state/V3.7-DELTA.md`
- `P8.5.6` — `helm/tree/control-plane/helm_core/knowledge/batch_intake.py`, `helm/tree/control-plane/helm_core/knowledge/memory.py`, `helm/tree/control-plane/helm_core/knowledge/worker.py`, `helm/tree/control-plane/helm_core/models/tables.py`, `helm/tree/docs/KNOWLEDGE.md`, `helm/tree/docs/KNOWLEDGE_MODELS.md` (+8)
- `P8.5.7` — `helm/tree/compose/docker-compose.yml`, `helm/tree/control-plane/helm_core/api/hooks.py`, `helm/tree/control-plane/helm_core/api/internal.py`, `helm/tree/control-plane/helm_core/channels/max.py`, `helm/tree/control-plane/helm_core/channels/telegram.py`, `helm/tree/control-plane/helm_core/ingest.py` (+26)
- `P8.5.8` — `helm/tree/control-plane/helm_core/api/panel.py`, `helm/tree/docs/KNOWLEDGE.md`, `helm/tree/implementation-state/V3.4-DELTA.md`, `helm/tree/implementation-state/V3.7-DELTA.md`, `helm/tree/implementation-state/V3.8-DELTA.md`, `helm/tree/implementation-state/WORKPLAN.md`
- `P8.5.9` — `helm/tree/implementation-state/V3.7-DELTA.md`
- `P8.6` — `helm/tree/scripts/restore_test.sh`
- `P8.6.0` — `helm/tree/implementation-state/V3.8-DELTA.md`
- `P8.6.1` — `helm/tree/control-plane/helm_core/knowledge/chat_intake.py`, `helm/tree/docs/KNOWLEDGE.md`, `helm/tree/implementation-state/V3.8-ACCEPTANCE.md`, `helm/tree/implementation-state/V3.8-DELTA.md`, `helm/tree/implementation-state/WORKPLAN.md`, `helm/tree/scripts/v37-v38-deploy-runbook.md`
- `P8.6.2` — `helm/tree/compose/docker-compose.yml`, `helm/tree/control-plane/helm_core/api/hooks_knowledge_telegram.py`, `helm/tree/control-plane/helm_core/api/internal.py`, `helm/tree/control-plane/helm_core/app.py`, `helm/tree/control-plane/helm_core/channels/telegram_knowledge.py`, `helm/tree/control-plane/helm_core/config.py` (+13)
- `P8.6.3` — `helm/tree/docs/KNOWLEDGE.md`, `helm/tree/implementation-state/V3.8-DELTA.md`, `helm/tree/implementation-state/WORKPLAN.md`
- `P8.6.4` — `helm/tree/control-plane/helm_core/knowledge/quotas.py`, `helm/tree/control-plane/helm_core/knowledge/worker.py`, `helm/tree/control-plane/tests/test_knowledge_quotas.py`, `helm/tree/docs/KNOWLEDGE.md`, `helm/tree/implementation-state/V3.8-DELTA.md`, `helm/tree/implementation-state/WORKPLAN.md` (+1)
- `P8.6.5` — `helm/tree/control-plane/helm_core/api/auth.py`, `helm/tree/control-plane/helm_core/api/deps.py`, `helm/tree/control-plane/helm_core/api/internal.py`, `helm/tree/control-plane/helm_core/api/panel.py`, `helm/tree/control-plane/helm_core/knowledge/batch_intake.py`, `helm/tree/control-plane/helm_core/knowledge/onboarding.py` (+9)
- `P8.6.6` — `helm/tree/control-plane/helm_core/knowledge/rephrase.py`, `helm/tree/control-plane/helm_core/knowledge/style.py`, `helm/tree/control-plane/migrations/versions/e4a7c9f2b6d1_owner_style_profile_version.py`, `helm/tree/control-plane/tests/test_api.py`, `helm/tree/docs/KNOWLEDGE.md`, `helm/tree/implementation-state/ROADMAP-TO-DONE.md` (+3)
- `P8.6.7` — `helm/tree/docs/KNOWLEDGE.md`, `helm/tree/implementation-state/ROADMAP-TO-DONE.md`, `helm/tree/implementation-state/V3.8-DELTA.md`, `helm/tree/implementation-state/WORKPLAN.md`, `helm/tree/scripts/v37-v38-deploy-runbook.md`

## находка (F-YYMMDD-NN)

- `F-260827-01` — `helm/tree/scripts/BATCH-01.md`
- `F-260828-01` — `helm/tree/scripts/BATCH-01.md`, `helm/tree/scripts/hermes-enable-runbook.md`, `helm/tree/scripts/hermes-set-api-key.sh`, `helm/tree/scripts/knowledge-telegram-register-webhook.sh`, `helm/tree/scripts/max-register-webhook.sh`
- `F-260828-02` — `helm/tree/scripts/BATCH-01.md`, `helm/tree/scripts/knowledge-telegram-register-webhook.sh`, `helm/tree/scripts/max-bringup-runbook.md`, `helm/tree/scripts/max-register-webhook.sh`
- `F-260829-01` — `helm/tree/scripts/n8n-export-runbook.md`
- `F-260829-02` — `helm/tree/implementation-state/WORKPLAN.md`
- `F-260829-04` — `helm/tree/implementation-state/WORKPLAN.md`
- `F-260829-05` — `helm/tree/control-plane/helm_core/channels/max.py`, `helm/tree/control-plane/tests/test_max_channel.py`
- `F-260829-09` — `helm/tree/compose/docker-compose.yml`, `helm/tree/scripts/BATCH-01.md`, `helm/tree/scripts/forgejo-migrate.py`, `helm/tree/scripts/hermes-enable-runbook.md`, `helm/tree/scripts/knowledge-bootstrap.sh`, `helm/tree/scripts/max-bringup-runbook.md` (+2)
- `F-260829-10` — `.github/workflows/deploy.yml`, `helm/tree/scripts/v37-v38-deploy-runbook.md`
- `F-260829-17` — `helm/tree/implementation-state/WORKPLAN.md`
- `F-260829-18` — `helm/tree/implementation-state/WORKPLAN.md`
- `F-260829-19` — `helm/tree/control-plane/helm_core/channels/max.py`, `helm/tree/implementation-state/WORKPLAN.md`
- `F-260829-20` — `helm/tree/implementation-state/WORKPLAN.md`, `helm/tree/scripts/max-diagnose-send.sh`, `helm/tree/scripts/v37-v38-deploy-runbook.md`
- `F-260829-21` — `helm/tree/docs/adr/ADR-014-max-responses-api.md`, `helm/tree/implementation-state/WORKPLAN.md`, `helm/tree/scripts/hermes-responses-diagnose.sh`
- `F-260829-24` — `helm/tree/implementation-state/WORKPLAN.md`, `helm/tree/scripts/knowledge-probe-live-diagnose.sh`
- `F-260829-25` — `helm/tree/control-plane/helm_core/api/internal.py`, `helm/tree/control-plane/tests/test_api.py`, `helm/tree/docs/KNOWLEDGE_RETRIEVAL.md`, `helm/tree/hermes/plugins/helm-control/__init__.py`, `helm/tree/implementation-state/ROADMAP-TO-DONE.md`, `helm/tree/implementation-state/WORKPLAN.md` (+3)
- `F-260829-27` — `helm/tree/scripts/knowledge-telegram-attachment-recon.sh`
- `F-260829-33` — `helm/tree/implementation-state/WORKPLAN.md`
- `F-260830-01` — `helm/tree/implementation-state/WORKPLAN.md`
- `F-260830-02` — `helm/tree/implementation-state/WORKPLAN.md`
- `F-260830-03` — `helm/tree/docs/adr/ADR-025-hybrid-retrieval.md`, `helm/tree/implementation-state/WORKPLAN.md`, `helm/tree/scripts/knowledge-telegram-register-webhook.sh`, `helm/tree/scripts/v37-v38-deploy-runbook.md`
- `F-260831-02` — `helm/tree/docs/adr/ADR-025-hybrid-retrieval.md`, `helm/tree/implementation-state/ROADMAP-TO-DONE.md`
- `F-260831-03` — `helm/tree/docs/adr/ADR-021-gigaam-voice-pipeline.md`, `helm/tree/implementation-state/ROADMAP-TO-DONE.md`
- `F-260831-04` — `helm/tree/docs/adr/ADR-021-gigaam-voice-pipeline.md`, `helm/tree/implementation-state/ROADMAP-TO-DONE.md`
- `F-260831-05` — `helm/tree/docs/KNOWLEDGE_MODELS.md`, `helm/tree/implementation-state/ROADMAP-TO-DONE.md`
- `F-260831-07` — `.github/workflows/deploy.yml`, `helm/tree/implementation-state/ROADMAP-TO-DONE.md`

## Индексы продуктов

- `graph/helm/INDEX.md`
