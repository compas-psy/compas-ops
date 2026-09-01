# TEST_REPORT — состояние тестов на 31.08.2026

**565 тестов, 565 зелёных**, прогон против настоящего PostgreSQL (не
SQLite — проверяются JSONB, CHECK-ограничения вроде
`promotion_requires_owner`, поведение RLS и UNIQUE под конкурентной
вставкой, см. `tests/conftest.py`). Запуск:

```bash
cd control-plane && python3 -m pytest tests/ -q
```

Занимает ~100-180 секунд в зависимости от загрузки машины (наблюдалось
и 102с, и 176с на одном и том же наборе тестов — не признак поломки,
Postgres иногда требует времени на восстановление соединений).

## Разбивка по крупнейшим файлам (30 файлов тестов всего)

| Файл | Тестов | Что покрывает |
|---|---|---|
| `test_max_channel.py` | 52 | MAX-канал целиком: webhook, дедуп, /force, cross-channel |
| `test_api.py` | 46 | Control Plane API + AST-тесты zero-paid-AI гарантии (ADR-020) |
| `test_knowledge_chat_intake.py` | 40 | Диалог выбора домена для вложений (Telegram/MAX) |
| `test_panel_knowledge_user.py` | 33 | Panel-оболочка для KNOWLEDGE_USER (P8.6.5) |
| `test_knowledge_worker.py` | 24 | Async ingest worker, `register_file_for_ingest`, fair queue |
| `test_knowledge_probe.py` | 22 | Z0/Z1/Z2 probe, NEEDS_REASONING эскалация |
| `test_knowledge_memory.py` | 22 | Micro-Memory «Запомни» (ADR-027) |
| `test_knowledge_telegram_hook.py` | 20 | Dedicated Knowledge Telegram Bot (ADR-029) |
| `test_knowledge_recall.py` | 20 | Recall — читающая половина Micro-Memory |
| `test_knowledge_relations.py` | 19 | Слой 1 `knowledge_relations` — wikilink/frontmatter (E13) |
| `test_knowledge_admin.py` | 18 | §14.16 команды управления памятью |
| `test_panel_users.py` / `test_panel_auth.py` | 16 + 16 | Panel account-management + Telegram Login/WebAuthn (ADR-016) |
| `test_knowledge_batch_intake.py` | 16 | ZIP batch ingest (ADR-026) |
| `test_knowledge_onboarding.py` | 15 | Инвайты KNOWLEDGE_USER |
| `test_knowledge_zip_safety.py` | 14 | Защита от zip bomb/path traversal |
| `test_perimeter.py` | 12 | Периметр сети (какие порты реально открыты) |
| `test_knowledge_quotas.py` / `test_knowledge_offboarding.py` / `test_30_2_control_plane.py` | 12 каждый | Квоты (ADR-031), выгрузка/удаление, §30.2 approval/trust |
| `test_knowledge_tenancy.py` / `test_knowledge_documents.py` | 11 каждый | RLS/tenancy (ADR-030), выдача оригинала (§14.15) |
| `test_knowledge_parsers.py` | 10 | MarkItDown/Docling на реальных файлах |
| Остальные 12 файлов | ~50 суммарно | canonical actions, RED-gate, rephrase (Z2), config, audio, style, telegram channel |

Сумма top-level `def test_` по файлам — 510; разница до 565 —
параметризованные тесты (`@pytest.mark.parametrize`) и методы тестовых
классов, не считающиеся отдельной top-level функцией при простом grep.

## Что тестами НЕ покрыто (осознанно, не пробел в тестировании)

- **Docling/GigaAM на реальных моделях** — `test_knowledge_parsers.py`
  подменяет тяжёлые модели там, где сценарий не требует реального
  распознавания; настоящая проверка качества — только на живом сервере
  (см. `docs/adr/ADR-021-gigaam-voice-pipeline.md`).
- **Telegram-сторона `helm-control`** (Hermes-плагин) — вне пакета
  `helm_core`, untestable локально штатным pytest-прогоном; проверяется
  только живым сервером (см. записи `resolved_this_cycle` в
  `STATUS.json` про P8.5.7 Telegram-сторону).
- **Реальный Graphify-эксперимент** — синтетика для теста парсера
  (`test_knowledge_relations.py`) разрешена явно, но НЕ как
  доказательство пользы Graphify — сам эксперимент `E13 =
  INSUFFICIENT_REAL_CORPUS`, реальных multi-hop данных пока нет.

## Живые проверки, не покрытые pytest вовсе

Отдельный класс верификации — recon-скрипты в `scripts/` (`verify-
z2-rephrase.sh`, `verify-e13-relations.sh`, `verify-gigaam-audio-
pipeline.sh`, `verify-voice-remember-pipeline.sh` и другие), каждый
запускается через `deploy.yml` action=recon на реальном сервере,
результат — не зелёная галочка pytest, а реальный вывод (`outcome=...`,
`mode=...`) в откатываемой транзакции. Список находок, пойманных именно
такими прогонами (не unit-тестами) — `TROUBLESHOOTING.md`.
