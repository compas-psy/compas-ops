"""Переразбор уже загруженного файла новым парсером.

ЗАЧЕМ ЭТО ЕСТЬ. Распоряжение владельца 07.09.2026: «Одинаковые байты
файла после исправления парсера должны получать новую производную
ревизию». До 08.09.2026 этого не происходило ВООБЩЕ, и подъём
`SEMANTIC_VERSION` этого не давал — он ключует семантическое задание, а
семантическое задание файл не разбирает.

Измерено прогоном 469 на живом корпусе: восстановление строк
лабораторной таблицы (`tables.py`) работало только для новых загрузок,
а уже лежащий «Биохимический анализ крови.pdf» остался с оторванным от
названия значением. Вопрос «какой у меня был холестерин» не отвечался
не потому, что проверки строги, а потому, что в сохранённом тексте
холестерина рядом с его значением нет.

ПОЧЕМУ ЭТО НЕ СТОИТ ВТОРЫХ ПЯТНАДЦАТИ ЧАСОВ. Переразбор сравнивает
ПОЛУЧЕННЫЙ ТЕКСТ с уже сохранённым. Совпал — меняется только отпечаток,
и ни чанки, ни граф, ни семантика не трогаются. Книга fb2 к `tables.py`
безразлична, её текст не изменится, и её пятнадцатичасовая ревизия
останется на месте. Перестраивается ровно то, что действительно стало
другим.

ПОЧЕМУ ПОРЦИЯМИ И ПОСЛЕДНИМ. Тот же довод, что у семантики (§1
распоряжения): переразбор — фоновая работа, и занимать ею модель и
диск, пока владелец ждёт ответа, нельзя. Воркер берёт один устаревший
источник за цикл и только после дел владельца.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Collection
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import KnowledgeSource, KnowledgeStatus
from .chunking import store_chunks
from .temporal import content_date
from .derivation import derivation_fingerprint
from .parsers import parse_file
from .relations import note_id_for, store_relations
from .semantic_jobs import enqueue_semantic, request_rederivation
from .tenancy import bind_knowledge_user
from .vault import frontmatter

logger = logging.getLogger(__name__)

#: Исходы переразбора одного источника.
UNCHANGED = "unchanged"      # текст тот же — отмечен, больше ничего
REPARSED = "reparsed"        # текст другой — слои пересобраны
QUALITY = "quality"          # парсер снова не справился — не трогаем
MISSING = "missing"          # исходного файла нет на диске


def stale_source(session: Session,
                 skip: Collection[uuid.UUID] = ()) -> KnowledgeSource | None:
    """Один живой источник, разобранный не нынешним кодом.

    Порядок — по `created_at`: самые старые документы разобраны самыми
    старыми парсерами, и польза от их переразбора больше.

    `skip` — источники, за которые нынешний код уже брался и не смог
    (файла нет, парсер не справился). Отметку они не получают намеренно,
    поэтому без этого списка выборка возвращала бы ОДИН И ТОТ ЖЕ
    источник бесконечно.
    """
    current = derivation_fingerprint()
    query = (
        select(KnowledgeSource)
        .where(
            KnowledgeSource.status == KnowledgeStatus.ACTIVE,
            KnowledgeSource.source_path.is_not(None),
            or_(KnowledgeSource.derivation_fingerprint.is_(None),
                KnowledgeSource.derivation_fingerprint != current),
        )
        .order_by(KnowledgeSource.created_at)
        .limit(1))
    if skip:
        query = query.where(KnowledgeSource.id.not_in(list(skip)))
    return session.scalars(query).first()


def _stored_text(source: KnowledgeSource) -> str | None:
    """Текст, лежащий сейчас в `source_path`, без нашего frontmatter."""
    path = Path(source.source_path or "")
    if not path.is_file():
        return None
    raw = path.read_text(encoding="utf-8")
    _, separator, body = raw.partition("---\n\n")
    return body if separator else raw


def reparse_source(session: Session, source: KnowledgeSource) -> str:
    """Разобрать файл заново. Возвращает один из исходов выше.

    Ничего не коммитит: транзакцией распоряжается вызывающий, как и в
    `worker.process_job()`.
    """
    tenant_id = bind_knowledge_user(session, source.knowledge_user_id)
    raw = Path(source.raw_path)
    if not raw.is_file():
        # Отпечаток НЕ ставится: файл может вернуться (том не
        # смонтирован, восстановление идёт), и объявлять источник
        # разобранным нынешним кодом было бы неправдой.
        logger.warning("переразбор %s: исходного файла нет", source.id)
        return MISSING

    result = parse_file(raw)
    if not result.quality_ok:
        # Тоже без отпечатка: нынешний код с этим файлом не справился,
        # и следующая правка парсера обязана попробовать снова.
        source.status = KnowledgeStatus.NEEDS_REVIEW
        return QUALITY

    if result.text == _stored_text(source):
        # ГЛАВНАЯ ВЕТКА ПО ЧИСЛУ ИСТОЧНИКОВ. Правка парсера касается не
        # всех форматов; для остальных переразбор обязан быть бесплатным.
        source.parser = result.parser
        source.derivation_fingerprint = derivation_fingerprint()
        return UNCHANGED

    source.parser = result.parser
    source.content_date = content_date(result.text)
    Path(source.source_path).parent.mkdir(parents=True, exist_ok=True)
    Path(source.source_path).write_text(frontmatter(source) + result.text,
                                        encoding="utf-8")
    store_relations(session, domain=source.domain, knowledge_user_id=tenant_id,
                    from_id=note_id_for(original_filename=source.original_filename,
                                        source_id=source.id),
                    source_id=source.id, text=result.text)
    store_chunks(session, source_id=source.id, knowledge_user_id=tenant_id,
                 domain=source.domain, text=result.text)
    # Семантика — заданием, а не здесь: она идёт минутами и зовёт модель,
    # а эта транзакция держит разбор и чанки (тот же довод, что в
    # `worker.process_job()`).
    #
    # ДВА ВЫЗОВА, А НЕ ОДИН, И ЭТО НЕ ПЕРЕСТРАХОВКА. Ключ уникальности
    # задания — «пользователь, источник, содержимое, версия», где
    # содержимое это sha256 ФАЙЛА. Байты не изменились, версия не
    # изменилась — значит `enqueue_semantic` видит работу выполненной и
    # нового задания не создаёт, хотя разобранный текст стал другим.
    # Это ровно тот дефект, который `derivation.py` описывал и не
    # закрывал. `enqueue_semantic` нужен, когда задания ещё нет вовсе;
    # `request_rederivation` — когда оно есть и должно начаться заново.
    enqueue_semantic(session, source_id=source.id, knowledge_user_id=tenant_id,
                     source_sha256=source.sha256)
    request_rederivation(session, source_id=source.id)
    source.derivation_fingerprint = derivation_fingerprint()
    logger.info("переразбор %s: текст изменился, слои пересобраны", source.id)
    return REPARSED


#: Исходы, после которых источник нельзя выбирать снова в том же
#: процессе: отметку он не получил, значит выборка вернёт его опять.
UNFIXABLE = (MISSING, QUALITY)


def reparse_one_stale(session: Session,
                      skip: Collection[uuid.UUID] = ()) -> tuple[uuid.UUID, str] | None:
    """Взять один устаревший источник и переразобрать. `None` — нечего."""
    source = stale_source(session, skip=skip)
    if source is None:
        return None
    return source.id, reparse_source(session, source)
