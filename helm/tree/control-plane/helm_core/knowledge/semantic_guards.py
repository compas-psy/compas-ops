"""v4.0 §14.5 — гейт текущей семантической ревизии на уровне БД.

Правило спеки одно: «Only a revision whose run reached READY may become
`current_semantic_revision` for that source». Из него следуют три
условия, которые обязаны выполняться ОДНОВРЕМЕННО:

    status            = ready
    source_id         = тот самый источник
    knowledge_user_id = тот самый владелец

Почему в базе, а не в коде. Внешний ключ доказывает только, что строка
существует, — он одинаково пропустит ревизию в статусе FAILED, ревизию
чужого документа и ревизию соседа. Проверка в Python защищает ровно
один путь записи; сюда же ходят миграции, backfill, `psql` руками и
любой будущий писатель графа. Последствие ошибки — не «неаккуратные
данные», а показанный владельцу ответ, собранный из полузаписанного
прохода: §14.5 требует, чтобы запрос НИКОГДА не видел узлы идущего
разбора.

Второй триггер закрывает обратную сторону. Пропустить ревизию в
`current` мало — надо ещё не дать ей испортиться после назначения:
`UPDATE knowledge_semantic_runs SET status = 'failed'` на текущей
ревизии оставил бы источник указывающим на провалившийся проход, и
первая проверка об этом никогда бы не узнала.

Оба триггера — SECURITY INVOKER (по умолчанию), и это намеренно: под
RLS чужая ревизия просто не видна, и попытка сослаться на соседа
падает на «ревизия не найдена». Fail closed без единой строки про
тенантность внутри самого триггера.

Как и `rls.py`: единственный источник DDL — этот модуль, его зовут и
миграция, и тестовая фикстура. `Base.metadata.create_all()` триггеров
не создаёт, и без явного вызова pytest проверял бы схему без гейта.
"""

from __future__ import annotations

from sqlalchemy import Connection, text

#: Имена — чтобы миграция могла их снять, а приёмка проверить наличие.
SOURCE_TRIGGER = "knowledge_sources_current_semantic_run_guard"
RUN_TRIGGER = "knowledge_semantic_runs_current_guard"
SOURCE_FUNCTION = "knowledge_current_semantic_run_is_ready"
RUN_FUNCTION = "knowledge_semantic_run_stays_valid_while_current"

#: `IS NOT DISTINCT FROM`, а не `=`: `knowledge_sources.knowledge_user_id`
#: nullable (наследие аддитивной миграции v3.8), и на строке без
#: владельца `=` дал бы NULL, то есть «условие не нарушено». У ревизии
#: поле NOT NULL, поэтому источник без владельца текущей ревизии иметь
#: не может — и это правильный отказ, а не побочный эффект.
_SOURCE_GUARD = f"""
CREATE OR REPLACE FUNCTION {SOURCE_FUNCTION}() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.current_semantic_run_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM knowledge_semantic_runs r
         WHERE r.id = NEW.current_semantic_run_id
           AND r.source_id = NEW.id
           AND r.knowledge_user_id IS NOT DISTINCT FROM NEW.knowledge_user_id
           AND r.status = 'ready'
    ) THEN
        RAISE EXCEPTION
            'current_semantic_run_id % не годится источнику %: нужна ревизия '
            'этого же источника, этого же владельца, в статусе ready (v4.0 §14.5)',
            NEW.current_semantic_run_id, NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
"""

_RUN_GUARD = f"""
CREATE OR REPLACE FUNCTION {RUN_FUNCTION}() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- Ревизию правят на каждом шаге разбора (счётчики окон, покрытие).
    -- Дорогой поиск ссылок делается только когда меняется что-то из
    -- трёх полей, от которых зависит пригодность.
    IF NEW.status IS NOT DISTINCT FROM OLD.status
       AND NEW.source_id IS NOT DISTINCT FROM OLD.source_id
       AND NEW.knowledge_user_id IS NOT DISTINCT FROM OLD.knowledge_user_id THEN
        RETURN NEW;
    END IF;
    IF EXISTS (
        SELECT 1 FROM knowledge_sources s
         WHERE s.current_semantic_run_id = OLD.id
           AND NOT (NEW.status = 'ready'
                    AND NEW.source_id = s.id
                    AND NEW.knowledge_user_id IS NOT DISTINCT FROM s.knowledge_user_id)
    ) THEN
        RAISE EXCEPTION
            'ревизия % назначена текущей: пока это так, её status/source_id/'
            'knowledge_user_id нельзя менять так, чтобы она перестала годиться '
            '(v4.0 §14.5)', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
"""

#: `UPDATE OF` перечисляет и `knowledge_user_id`: сменить владельца
#: источника, оставив указатель на ревизию прежнего, — та же дыра с
#: другой стороны.
#:
#: Список, а не один текст с точками с запятой: разбирать составной SQL
#: обратно на команды пришлось бы самому, и любая точка с запятой внутри
#: тела функции ломала бы разбор.
_STATEMENTS = (
    _SOURCE_GUARD,
    _RUN_GUARD,
    f"DROP TRIGGER IF EXISTS {SOURCE_TRIGGER} ON knowledge_sources",
    f"""CREATE TRIGGER {SOURCE_TRIGGER}
        BEFORE INSERT OR UPDATE OF current_semantic_run_id, knowledge_user_id
        ON knowledge_sources
        FOR EACH ROW EXECUTE FUNCTION {SOURCE_FUNCTION}()""",
    f"DROP TRIGGER IF EXISTS {RUN_TRIGGER} ON knowledge_semantic_runs",
    f"""CREATE TRIGGER {RUN_TRIGGER}
        BEFORE UPDATE ON knowledge_semantic_runs
        FOR EACH ROW EXECUTE FUNCTION {RUN_FUNCTION}()""",
)


def apply_semantic_guards(connection: Connection) -> None:
    """Поставить оба триггера. Идемпотентно."""
    for statement in _STATEMENTS:
        connection.execute(text(statement))


def drop_semantic_guards(connection: Connection) -> None:
    """Снять оба триггера и их функции — для `downgrade`."""
    connection.execute(text(f"DROP TRIGGER IF EXISTS {SOURCE_TRIGGER} ON knowledge_sources"))
    connection.execute(text(f"DROP TRIGGER IF EXISTS {RUN_TRIGGER} ON knowledge_semantic_runs"))
    connection.execute(text(f"DROP FUNCTION IF EXISTS {SOURCE_FUNCTION}()"))
    connection.execute(text(f"DROP FUNCTION IF EXISTS {RUN_FUNCTION}()"))
