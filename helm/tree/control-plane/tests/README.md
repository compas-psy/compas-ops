# Тесты Control Plane

Прогон против **настоящего PostgreSQL**, не против SQLite: проверяются JSONB,
CHECK-ограничение `promotion_requires_owner` и поведение UNIQUE — то есть
ровно те свойства, которые на боевой БД и должны держать §30.2.

```bash
export HELM_TEST_DB='postgresql+psycopg://helm_rls@/helm_test?host=/tmp&port=55432'
export HELM_POLICY=../config/policies/actions.yaml
pytest tests/ -q
```

## Роль в базе обязана НЕ обходить RLS

С v3.8 изоляция между пользователями держится на двух слоях: явный
фильтр в коде и политики RLS в Postgres (§14.4). Второй слой не
проверяется вовсе, если тесты подключаются суперпользователем или ролью
с `BYPASSRLS`: Postgres пропускает такие роли мимо политик всегда, и
`FORCE ROW LEVEL SECURITY` тут не помогает.

Понизить bootstrap-роль кластера нельзя — Postgres отвечает «the
bootstrap user must have the SUPERUSER attribute». Нужна отдельная роль,
владеющая тестовой схемой:

```sql
create role helm_rls login nosuperuser nobypassrls;
alter database helm_test owner to helm_rls;
-- пересоздать схему, чтобы таблицы принадлежали новой роли:
\c helm_test
alter schema public owner to helm_rls;
drop schema public cascade;
create schema public authorization helm_rls;
```

`conftest.py` проверяет это на старте сессии и отказывается запускаться
под ролью, которая обходит RLS, — иначе шесть tenancy-тестов падают
невнятными диффами, а причина не видна.

## Что покрыто

| Файл | Требование ТЗ |
|---|---|
| `test_30_2_control_plane.py` | §30.2 — все девять обязательных тестов, по одному на строку |
| `test_red_gate.py` | A-DoD п.5–6, цель §30.12 «RED bypass = 0» |
| `test_canonical.py` | §8.3 — канонизация payload и хэш действия |

## Проверка самих тестов

Набор проверен мутациями: отключение RED-гейта, отключение сверки хэша и
схлопывание намеренного повтора в дедупликации — каждая ловится ровно тем
тестом, который за неё отвечает. Тест, который не падает от снятия
проверки, которую он якобы проверяет, — это не тест.
