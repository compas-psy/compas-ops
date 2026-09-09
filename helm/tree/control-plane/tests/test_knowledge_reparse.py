"""Переразбор уже загруженного файла новым парсером.

ЗАЧЕМ ЭТОТ ФАЙЛ. Распоряжение владельца 07.09.2026: «одинаковые байты
файла после исправления парсера должны получать новую производную
ревизию». Прогон 469 показал, что этого не происходило ВООБЩЕ: правка
парсера доходила только до новых загрузок, а уже лежащий
«Биохимический анализ крови.pdf» оставался разобранным по-старому, и
вопрос про холестерин не отвечался.

Три отсечки, каждая из которых по отдельности хоронила правку:
повторная загрузка тех же байтов отсекается по sha256; семантическое
задание читает уже разобранный Markdown, а не файл; чанки поиска
сделаны из того же старого текста. Здесь проверяется путь, который эти
три отсечки обходит.
"""

import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from helm_core.knowledge import reparse as reparse_module
from helm_core.knowledge.derivation import derivation_fingerprint
from helm_core.knowledge.ingest import register_file_for_ingest
from helm_core.knowledge import worker as worker_module
from helm_core.knowledge.reparse import (
    MISSING, QUALITY, REPARSED, UNCHANGED, reparse_one_stale, reparse_source, stale_source,
)
from helm_core.knowledge.worker import process_job
from helm_core.models import (
    KnowledgeChunk, KnowledgeIngestStatus, KnowledgeSemanticJob, KnowledgeSource,
    KnowledgeStatus,
)


@dataclass
class _Parsed:
    text: str
    parser: str
    quality_ok: bool


#: Лабораторная строка так, как её отдавал ПРЕЖНИЙ парсер: название,
#: значение и единица оторваны друг от друга разрывами строк.
BROKEN = "Холестерин общий\n5.4\nммоль/л\n3.0 - 5.2"
#: Она же после `tables.py` — одной строкой, как её и надо искать.
FIXED = "Холестерин общий: 5.4 ммоль/л (3.0 - 5.2)"


def _ingested(session, tmp_path, name: str, text: str) -> KnowledgeSource:
    """Загрузить файл и разобрать его так, как это сделал бы воркер."""
    raw = tmp_path / name
    raw.write_text("байты, которые парсер и разбирает", encoding="utf-8")
    result = register_file_for_ingest(session, domain="engineering", raw_path=raw,
                                      original_filename=name, vault_root=str(tmp_path))
    session.flush()
    process_job(session, result.job)
    session.flush()
    return session.get(KnowledgeSource, result.source.id)


def _with_parser(monkeypatch, module, text: str, *, quality_ok: bool = True) -> None:
    monkeypatch.setattr(module, "parse_file",
                        lambda path: _Parsed(text=text, parser="markitdown",
                                             quality_ok=quality_ok))


# ── УСТАРЕВШИЙ ИСТОЧНИК ВИДЕН ────────────────────────────────────────

def test_a_freshly_parsed_source_is_not_stale(session, tmp_path, monkeypatch):
    """Отметка ставится там, где разбор и произошёл.

    Без неё только что загруженный документ считался бы устаревшим и
    уходил бы в переразбор первым же циклом воркера — бесконечно.
    """
    _with_parser(monkeypatch, worker_module, BROKEN)
    source = _ingested(session, tmp_path, "анализ.pdf", BROKEN)

    assert source.derivation_fingerprint == derivation_fingerprint()
    assert stale_source(session) is None


def test_a_source_parsed_by_other_code_is_stale(session, tmp_path, monkeypatch):
    """Не совпал отпечаток — источник разобран прежним способом."""
    _with_parser(monkeypatch, worker_module, BROKEN)
    source = _ingested(session, tmp_path, "анализ.pdf", BROKEN)
    source.derivation_fingerprint = "старый разбор"
    session.flush()

    assert stale_source(session) is source


def test_a_source_from_before_the_field_is_stale(session, tmp_path, monkeypatch):
    """NULL — это «разобран неизвестно чем», то есть не нынешним.

    Весь корпус на момент правки именно такой, и бэкафиллить его
    нынешним значением было бы неправдой ровно того рода, против
    которой поле и заведено.
    """
    _with_parser(monkeypatch, worker_module, BROKEN)
    source = _ingested(session, tmp_path, "анализ.pdf", BROKEN)
    source.derivation_fingerprint = None
    session.flush()

    assert stale_source(session) is source


# ── ПЕРЕРАЗБОР ДОХОДИТ ДО СОХРАНЁННОГО ТЕКСТА ────────────────────────

def test_fixed_parser_reaches_an_already_ingested_document(session, tmp_path, monkeypatch):
    """ИЗМЕРЕННЫЙ ДЕФЕКТ ПРОГОНА 469, ЦЕЛИКОМ.

    Документ загружен сломанным парсером; парсер починен; те же байты
    обязаны дать новый текст, новые чанки и новое семантическое задание
    — без повторной загрузки и без правки статусов руками.
    """
    _with_parser(monkeypatch, worker_module, BROKEN)
    source = _ingested(session, tmp_path, "анализ.pdf", BROKEN)
    source.derivation_fingerprint = "разбор со сломанной таблицей"
    session.flush()
    assert BROKEN in Path(source.source_path).read_text(encoding="utf-8")

    _with_parser(monkeypatch, reparse_module, FIXED)
    outcome = reparse_source(session, source)
    session.flush()

    assert outcome == REPARSED
    stored = Path(source.source_path).read_text(encoding="utf-8")
    assert FIXED in stored, "сохранённый текст остался прежним"
    assert BROKEN not in stored, "старый разбор не убран"
    assert source.derivation_fingerprint == derivation_fingerprint()

    chunks = session.scalars(
        select(KnowledgeChunk).where(KnowledgeChunk.source_id == source.id)).all()
    assert chunks, "поисковый слой не пересобран"
    assert any(FIXED in chunk.text for chunk in chunks), (
        "чанки поиска сделаны из старого текста — значение так и осталось "
        "оторванным от названия")


def test_reparse_enqueues_semantic_work_for_the_new_text(session, tmp_path, monkeypatch):
    """Новый текст — новый вход для извлечения, значит и новый разбор."""
    _with_parser(monkeypatch, worker_module, BROKEN)
    source = _ingested(session, tmp_path, "анализ.pdf", BROKEN)
    source.derivation_fingerprint = "прежний"
    session.flush()
    job = session.scalars(
        select(KnowledgeSemanticJob).where(
            KnowledgeSemanticJob.source_id == source.id)).one()
    # Задание уже выполнено по СТАРОМУ тексту — так его оставила загрузка.
    job.status = KnowledgeIngestStatus.DONE
    session.flush()

    _with_parser(monkeypatch, reparse_module, FIXED)
    reparse_source(session, source)
    session.flush()

    # Ключ уникальности задания — байты файла и версия; ни то, ни другое
    # не изменилось, поэтому НОВОГО задания быть и не может. Работа
    # обязана стать невыполненной у СУЩЕСТВУЮЩЕГО.
    session.refresh(job)
    assert job.status == KnowledgeIngestStatus.PENDING, (
        "семантика осталась выполненной по старому тексту")
    assert job.semantic_run_id is None
    assert job.attempts == 0


# ── И НЕ СТОИТ ВТОРЫХ ПЯТНАДЦАТИ ЧАСОВ ───────────────────────────────

def test_unchanged_text_costs_nothing_but_the_mark(session, tmp_path, monkeypatch):
    """ГЛАВНОЕ СВОЙСТВО ПО ЦЕНЕ.

    Книга fb2 к правке `tables.py` безразлична: её текст не изменится.
    Пятнадцатичасовая ревизия обязана остаться на месте, а не строиться
    заново из-за правки, которая этого источника не касается.
    """
    _with_parser(monkeypatch, worker_module, "Текст, которого правка не касается.")
    source = _ingested(session, tmp_path, "книга.fb2", "Текст, которого правка не касается.")
    source.derivation_fingerprint = "прежний"
    run_id = uuid.uuid4()
    source.current_semantic_run_id = None
    session.flush()
    jobs_before = len(session.scalars(
        select(KnowledgeSemanticJob).where(
            KnowledgeSemanticJob.source_id == source.id)).all())

    _with_parser(monkeypatch, reparse_module, "Текст, которого правка не касается.")
    outcome = reparse_source(session, source)
    session.flush()

    assert outcome == UNCHANGED
    assert source.derivation_fingerprint == derivation_fingerprint()
    jobs_after = len(session.scalars(
        select(KnowledgeSemanticJob).where(
            KnowledgeSemanticJob.source_id == source.id)).all())
    assert jobs_after == jobs_before, (
        "текст тот же, а семантика поставлена заново — это и есть лишние "
        "пятнадцать часов")


# ── ОТМЕТКА НЕ СТАВИТСЯ ТАМ, ГДЕ РАЗБОРА НЕ БЫЛО ─────────────────────

def test_a_failed_parse_does_not_claim_the_source_is_current(session, tmp_path, monkeypatch):
    """Нынешний код с файлом не справился — следующая правка обязана
    попробовать снова, а не считать его разобранным."""
    _with_parser(monkeypatch, worker_module, BROKEN)
    source = _ingested(session, tmp_path, "анализ.pdf", BROKEN)
    source.derivation_fingerprint = "прежний"
    session.flush()

    _with_parser(monkeypatch, reparse_module, "", quality_ok=False)
    outcome = reparse_source(session, source)
    session.flush()

    assert outcome == QUALITY
    assert source.derivation_fingerprint == "прежний"
    assert source.status == KnowledgeStatus.NEEDS_REVIEW


def test_a_missing_raw_file_does_not_claim_the_source_is_current(session, tmp_path, monkeypatch):
    """Файл может вернуться (том не смонтирован, идёт восстановление)."""
    _with_parser(monkeypatch, worker_module, BROKEN)
    source = _ingested(session, tmp_path, "анализ.pdf", BROKEN)
    source.derivation_fingerprint = "прежний"
    Path(source.raw_path).unlink()
    session.flush()

    assert reparse_source(session, source) == MISSING
    assert source.derivation_fingerprint == "прежний"


def test_reparse_one_stale_returns_none_when_the_corpus_is_current(session):
    assert reparse_one_stale(session) is None


def test_a_source_that_cannot_be_reparsed_is_not_offered_again(session, tmp_path, monkeypatch):
    """ИЗМЕРЕННАЯ АВАРИЯ 09.09.2026, ЦЕЛИКОМ.

    Источник, у которого пропал исходный файл, отметку не получает —
    намеренно, файл может вернуться. Из-за этого выборка возвращала ЕГО
    ЖЕ каждый раз, воркер крутился на нём по сорок раз в секунду, съедал
    процессор и морил голодом локальную модель: в журнале helm-core
    стояло «локальный синтез недоступен: timed out», и ВСЕ ответы
    владельцу деградировали до ближайшей цитаты.

    Дефект не в том, что отметки нет, а в том, что выборка не умела
    пропускать уже испробованное.
    """
    _with_parser(monkeypatch, worker_module, BROKEN)
    lost = _ingested(session, tmp_path, "пропал.pdf", BROKEN)
    lost.derivation_fingerprint = None
    Path(lost.raw_path).unlink()
    session.flush()

    source_id, outcome = reparse_one_stale(session)
    assert (source_id, outcome) == (lost.id, MISSING)
    # Без пропуска — тот же источник снова, и так до бесконечности.
    assert stale_source(session) is lost
    # С пропуском — выборка идёт дальше и на этом источнике не залипает.
    assert stale_source(session, skip={lost.id}) is None
    assert reparse_one_stale(session, skip={lost.id}) is None
