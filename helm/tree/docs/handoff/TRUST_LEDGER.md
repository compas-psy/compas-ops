# TRUST_LEDGER — где хранится история доверия к действиям

Устройство решения — ADR-010 (graduated trust). Этот документ — как
ЧИТАТЬ накопленную историю, не повторяет само решение.

## Таблица-реестр

`ActionTrust` (Postgres) — по одной строке на `action_type`:
`current_level`, `supervised_success` (счётчик успешных supervised-
запусков, CHECK `>= 0`), `last_incident_at`, `promoted_at`,
`promoted_by`.

CHECK-ограничение `promotion_requires_owner`: `(promoted_at IS NULL) OR
(promoted_by IS NOT NULL)` — в БД физически невозможна строка с датой
повышения без имени того, кто его дал. Это и есть «ledger» в буквальном
смысле — не просто текущее состояние, а гарантия, что каждое повышение
прослеживается к конкретному человеку.

## Как посмотреть

```sql
SELECT action_type, current_level, supervised_success,
       last_incident_at, promoted_at, promoted_by
FROM action_trust
ORDER BY action_type;
```

Panel показывает то же самое на эндпоинте деталей approval
(`api/panel.py`) — `supervised_success`, зашитый порог `10` (совпадает
со спекой D6), `last_incident_at`. Panel сегодня ТОЛЬКО читает эту
таблицу.

## Закрытый список, который никогда не появится с повышенным уровнем

`helm_core/actions/policy.py::NEVER_GRADUATE` (в коде, не в БД и не в
policy-YAML — «policy-файл редактируется агентом по одобрению, а этот
инвариант — нет»): `spend_money`, `legal_submission`,
`delete_business_data`, `change_price`, `signalai_live_execution`,
`secret_rotation`, `change_policy`, `change_trust_level`. Строка
`ActionTrust` для любого из них может существовать, но `never_
graduates()` всегда `True` — записанный `supervised_success` для этих
действий не имеет эффекта на минимально допустимый уровень.

## Важная оговорка — реестр сегодня инертен

Ни ЧТЕНИЕ, ни ЗАПИСЬ этой таблицы не подключены к боевому пути
диспетчеризации (`ApprovalService.propose()` всегда берёт `spec.
initial_level`, никогда не смотрит в `ActionTrust`; ничто не
инкрементирует `supervised_success` автоматически). Строки, которые
сегодня существуют в этой таблице — только те, что создали тесты или
ручная вставка; в проде реальных строк `ActionTrust`, скорее всего,
ещё нет вовсе. См. ADR-010 «Не в объёме этого захода» — это открытый,
названный пробел, не скрытая недоделка.
