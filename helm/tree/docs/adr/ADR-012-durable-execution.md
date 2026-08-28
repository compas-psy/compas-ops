# ADR-012. Durable execution: Postgres-механизм без живого спайка DBOS

**Дата:** 27–28.08.2026 · **Статус:** принято, с честной оговоркой

## Контекст

§7.4 требует конкретную последовательность перед выбором durable-движка:

> В P2 выполнить spike: durable workflow start → crash процесса → restart →
> resume → wait approval → approval → exact-once fixture → retry transient
> failure → backup/restore. Если PASS — использовать DBOS/Postgres. Если
> FAIL — ADR-012 и простой Postgres mechanism.

Этот спайк **не был выполнен буквально**. DBOS Transact не устанавливался и
не тестировался на crash/resume в реальных условиях.

## Почему так вышло

Основная офлайн-сборка велась в среде без доступа к целевому серверу
(см. ADR-017) и без возможности поднять полноценный процесс с реальным
crash/restart циклом, который спайк требует по своей природе. К моменту
появления доступа к серверу (P1 этого цикла) Control Plane уже был построен
поверх явной Postgres state machine — атомарный `UPDATE ... WHERE status =
'APPROVED'` для claim перед исполнением (`helm_core/approvals/service.py`,
`execute_approved`), TTL как bounded retry/expiry, `idempotency_key` с
UNIQUE-ограничением в БД. Это ровно тот fallback-механизм, который §7.4
разрешает при FAIL спайка, но получен не через сам спайк, а напрямую.

## Решение

Зафиксировать это как отклонение, а не скрыть. Проверить построенный
механизм по требованиям §7.4 к результату (не к процессу):

| Требование §7.4 | Есть? | Где |
|---|---|---|
| explicit state machine | да | `models/base.py::ApprovalStatus`, ровно 5 состояний |
| transactional claims | да | атомарный `UPDATE` в `execute_approved`, тест `test_approved_red_action_executes_exactly_once` |
| idempotency keys | да | `Approval.idempotency_key` UNIQUE, `_idempotency_key()` |
| bounded retry/backoff | частично | TTL ограничивает окно (2ч/24ч), явного backoff для transient failures нет — нужен в P4 при интеграции с Hermes |
| leases/heartbeat | нет | не нужно при текущей модели: одна попытка исполнения на approval, не долгоживущий worker pool |
| systemd/CP scheduler | нет | появится в P2 при routines (§27), не относится к approval execution |
| recovery tests | частично | тесты покрывают атомарность на уровне БД; сценарий «процесс упал между claim и executor» не воспроизведён живым тестом |
| backup/restore | нет | P5, ещё не пройден |

## Последствия

Не притворяюсь, что DBOS был оценён и отвергнут по существу — он не
оценивался вовсе. Если впоследствии появится основание считать, что
durable-гарантии текущего механизма недостаточны (например: обнаружится
реальный сценарий «упал между claim и запуском executor, approval завис в
EXECUTING навсегда»), пересмотр этого ADR обязателен, и тогда DBOS стоит
оценить по-настоящему, а не описательно.

Открытый пробел на сейчас: нет автоматического recovery для approval,
застрявшего в `EXECUTING` (например, если процесс убит между claim и
исполнением). Это не покрыто ни текущим кодом, ни тестами. Кандидат для
P2 либо для отдельного finding перед P14.
