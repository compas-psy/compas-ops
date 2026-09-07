"""Порционный разбор большого источника: прогресс, продолжение, отзывчивость.

Дефект, ради которого это написано (замер 07.09.2026). Книга на 1399
фрагментов занимала воркер целиком: `publish_semantic_run()` разбирал
источник от первого окна до последнего одним вызовом, ничего не
коммитил до конца и не отдавал управление. Пока она разбиралась, живые
вопросы владельца деградировали, а снять её с обработки можно было
только правкой статуса задания руками — то есть человеком.

Здесь проверяется механика порций, а не качество извлечения: извлекатель
поддельный по той же причине, что и в `test_knowledge_semantic_v3_publish`
(§14.23 — мок-извлекатель ничего не доказывает про качество).
"""

from sqlalchemy import select

from helm_core.knowledge.semantic_publish import (
    SEMANTIC_VERSION, publish_semantic_run, resumable_run,
)
from helm_core.knowledge.semantic_windows import build_windows
from helm_core.models import KnowledgeSemanticWindow, SemanticRunStatus

from test_knowledge_semantic_v3_publish import (  # noqa: F401 — фикстура source
    long_source_text, marker_aware_extractor, source,
)


def _many_windows_text() -> str:
    """Текст заведомо из нескольких окон верхнего уровня."""
    return "\n\n".join(f"## Раздел {i}\n\n" + long_source_text() for i in range(4))


def test_budget_stops_after_its_windows_and_says_the_work_is_not_done(session, source):
    text = _many_windows_text()
    total = len(build_windows(text))
    assert total > 2, "текст должен давать больше двух окон, иначе тест ничего не ловит"

    result = publish_semantic_run(session, source=source, text=text,
                                  extract=marker_aware_extractor,
                                  semantic_version=SEMANTIC_VERSION, budget=1)

    assert result.finished is False, "порция обязана сообщать, что работа осталась"
    assert result.switched is False
    run = resumable_run(session, source_id=source.id, semantic_version=SEMANTIC_VERSION)
    assert run is not None and run.status == SemanticRunStatus.RUNNING
    session.refresh(source)
    assert source.current_semantic_run_id != run.id, \
        "половина разобранной книги не может стать текущей ревизией (§14.20)"


def test_progress_of_a_batch_is_written_and_a_resume_continues_from_it(session, source):
    text = _many_windows_text()
    total = len(build_windows(text))

    publish_semantic_run(session, source=source, text=text, extract=marker_aware_extractor,
                         semantic_version=SEMANTIC_VERSION, budget=1)
    run = resumable_run(session, source_id=source.id, semantic_version=SEMANTIC_VERSION)
    after_first = session.scalars(
        select(KnowledgeSemanticWindow).where(
            KnowledgeSemanticWindow.semantic_run_id == run.id,
            KnowledgeSemanticWindow.parent_window_id.is_(None))).all()
    assert len(after_first) == 1, "первая порция обязана записать ровно своё окно"

    result = publish_semantic_run(session, source=source, text=text,
                                  extract=marker_aware_extractor,
                                  semantic_version=SEMANTIC_VERSION, budget=1, resume=True)
    assert result.run_id == run.id, "продолжение обязано идти в ту же ревизию"
    after_second = session.scalars(
        select(KnowledgeSemanticWindow).where(
            KnowledgeSemanticWindow.semantic_run_id == run.id,
            KnowledgeSemanticWindow.parent_window_id.is_(None))).all()
    assert len(after_second) == 2, "продолжение обязано брать следующее окно, а не то же"
    assert result.finished is (total <= 2)


def test_batched_run_finishes_with_the_same_result_as_a_single_pass(session, source):
    """Порции не должны менять итог — только момент, когда он получен."""
    text = _many_windows_text()
    total = len(build_windows(text))

    result = publish_semantic_run(session, source=source, text=text,
                                  extract=marker_aware_extractor,
                                  semantic_version=SEMANTIC_VERSION, budget=1)
    passes = 1
    while not result.finished:
        result = publish_semantic_run(session, source=source, text=text,
                                      extract=marker_aware_extractor,
                                      semantic_version=SEMANTIC_VERSION,
                                      budget=1, resume=True)
        passes += 1
        assert passes <= total + 1, "продолжение не сходится: порции не двигают прогресс"

    assert passes == total, f"{total} окон обязаны занять {total} порций, а не {passes}"
    assert result.status == SemanticRunStatus.READY
    assert result.switched is True, "законченная годная ревизия обязана стать текущей"
    session.refresh(source)
    assert source.current_semantic_run_id == result.run_id

    single = publish_semantic_run(session, source=source, text=text,
                                  extract=marker_aware_extractor,
                                  semantic_version=SEMANTIC_VERSION)
    assert (result.windows_total, result.windows_processed, result.windows_failed) == \
           (single.windows_total, single.windows_processed, single.windows_failed)
    assert result.nodes_created == single.nodes_created
    assert result.coverage_ratio == single.coverage_ratio


def test_a_resume_onto_changed_text_fails_instead_of_gluing_two_documents(session, source):
    """Тихая склейка двух текстов — худший отказ: ревизия выглядит целой."""
    publish_semantic_run(session, source=source, text=_many_windows_text(),
                         extract=marker_aware_extractor,
                         semantic_version=SEMANTIC_VERSION, budget=1)

    other = "## Совсем другой документ\n\n" + long_source_text()
    result = publish_semantic_run(session, source=source, text=other,
                                  extract=marker_aware_extractor,
                                  semantic_version=SEMANTIC_VERSION,
                                  budget=1, resume=True)
    assert result.status == SemanticRunStatus.FAILED
    assert result.finished is True
    assert result.switched is False
    assert resumable_run(session, source_id=source.id,
                         semantic_version=SEMANTIC_VERSION) is None, \
        "провалившаяся ревизия не должна оставаться претендентом на продолжение"


# ── владение заданием и аренда ────────────────────────────────────────
#
# До 07.09.2026 задание разбирало источник целиком за один вызов, и оба
# свойства ниже были не нужны, потому что порций не было. Появились
# порции — появились и два способа сломаться: потерять прогресс при
# возврате брошенного задания и закрыть большое задание как неисполнимое,
# просто потому что оно не влезло в три попытки.

import datetime as dt

import pytest

from helm_core.knowledge import semantic_jobs, semantic_pilot
from helm_core.knowledge.semantic_jobs import (
    MAX_ATTEMPTS, claim_next_semantic_job, enqueue_semantic, process_semantic_job,
)
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeIngestStatus, KnowledgeSemanticRun


@pytest.fixture()
def batched_job(session, source, monkeypatch):
    """Задание на многооконный источник, разбираемое поддельным извлекателем."""
    text = _many_windows_text()
    monkeypatch.setattr(semantic_pilot, "source_text", lambda src: text)

    real = semantic_jobs.publish_semantic_run

    def with_fake_extractor(*args, **kwargs):
        kwargs["extract"] = marker_aware_extractor
        kwargs["budget"] = 1
        return real(*args, **kwargs)

    monkeypatch.setattr(semantic_jobs, "publish_semantic_run", with_fake_extractor)

    bind_knowledge_user(session, source.knowledge_user_id)
    # Загрузка источника уже поставила задание сама (P2: обычная загрузка
    # порождает семантику), поэтому `enqueue_semantic` здесь вернёт None —
    # это и есть защита от дубликата, а не сбой.
    enqueue_semantic(session, source_id=source.id,
                     knowledge_user_id=source.knowledge_user_id,
                     source_sha256=source.sha256 or "0" * 64,
                     semantic_version=SEMANTIC_VERSION)
    session.flush()
    job = claim_next_semantic_job(session)
    assert job is not None and job.source_id == source.id
    return job, len(build_windows(text))


def test_an_unfinished_batch_keeps_the_job_and_extends_its_lease(session, batched_job):
    job, total = batched_job
    assert total > 2
    before = job.lease_expires_at

    finished = process_semantic_job(session, job)

    assert finished is False, "источник не кончился — задание не должно закрываться"
    assert job.status == KnowledgeIngestStatus.RUNNING
    assert job.lease_expires_at > before, "аренда не продлена после реальной работы"


def test_progress_clears_the_attempt_counter(session, batched_job):
    """Счётчик попыток стережёт задание, которое РОНЯЕТ воркер, а не большое.

    Без обнуления книга из сотен окон исчерпала бы три попытки на ровном
    месте и закрылась бы как неисполнимая, разобранная наполовину.
    """
    job, _ = batched_job
    job.attempts = MAX_ATTEMPTS - 1

    process_semantic_job(session, job)

    assert job.attempts == 0


def test_a_reclaimed_job_continues_its_run_instead_of_starting_over(session, batched_job):
    """Возврат брошенного задания обязан продолжать, а не начинать заново.

    Раньше `claim` помечал незаконченную ревизию FAILED как зомби. С
    порциями это стало прямым уничтожением прогресса: книга, дошедшая до
    половины, теряла половину при каждом перезапуске воркера.
    """
    job, _ = batched_job
    process_semantic_job(session, job)
    first_run = job.semantic_run_id
    assert first_run is not None

    # Воркер «умер»: аренда истекла, задание снова претендуемо.
    job.lease_expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
    session.flush()
    again = claim_next_semantic_job(session)
    assert again is not None and again.id == job.id

    assert session.get(KnowledgeSemanticRun, first_run).status == SemanticRunStatus.RUNNING, \
        "ревизия закрыта как зомби — прогресс порций снова теряется"
    process_semantic_job(session, again)
    assert again.semantic_run_id == first_run, "разбор начался заново, а не продолжился"


def test_the_last_batch_closes_the_job_and_releases_the_lease(session, batched_job):
    job, total = batched_job
    for _ in range(total):
        finished = process_semantic_job(session, job)
    assert finished is True
    assert job.status == KnowledgeIngestStatus.DONE
    assert job.lease_expires_at is None


# ── остановка и переразбор без правки статусов руками ─────────────────

def test_pause_stops_exactly_one_job_and_leaves_a_reason(session, batched_job):
    """Останавливается названное задание, а не всё похожее на него.

    `semantic-pause-oversized.sh` закрывал разом все незавершённые
    задания источников с расширением fb2 — чтобы снять одну книгу. Такая
    выборка попадает и в задания, о которых оператор не думал, и в те,
    которых ещё нет.
    """
    job, _ = batched_job
    assert semantic_jobs.pause_semantic_job(
        session, job_id=job.id, reason="SourceTooLargeForLease: проверка") is True

    session.refresh(job)
    assert job.status == KnowledgeIngestStatus.FAILED
    assert "SourceTooLargeForLease" in job.error
    assert job.lease_expires_at is None, "остановленное задание не должно держать аренду"
    assert claim_next_semantic_job(session) is None, "остановленное задание снова взято"


def test_pause_does_not_touch_a_finished_job(session, batched_job):
    job, total = batched_job
    for _ in range(total):
        process_semantic_job(session, job)
    assert job.status == KnowledgeIngestStatus.DONE
    assert semantic_jobs.pause_semantic_job(
        session, job_id=job.id, reason="поздно") is False
    assert job.status == KnowledgeIngestStatus.DONE


def test_rederivation_returns_one_source_to_the_queue_with_a_named_reason(session, batched_job):
    """Переразбор одного источника — вызов с именем, а не UPDATE руками."""
    job, _ = batched_job
    process_semantic_job(session, job)
    stale_run = job.semantic_run_id

    assert semantic_jobs.request_rederivation(
        session, source_id=job.source_id, semantic_version=SEMANTIC_VERSION) is True

    session.refresh(job)
    assert job.status == KnowledgeIngestStatus.PENDING
    assert job.attempts == 0
    assert job.semantic_run_id is None
    closed = session.get(KnowledgeSemanticRun, stale_run)
    assert closed.status == SemanticRunStatus.FAILED
    assert closed.error_code == "REDERIVATION_REQUESTED", \
        "закрытая ревизия обязана называть причину, иначе это молчаливая правка"
    assert claim_next_semantic_job(session) is not None, "источник не вернулся в очередь"


def test_an_unfinished_job_is_recorded_as_running_not_waiting(session, batched_job):
    """Незаконченное задание обязано числиться идущим, а не ждущим.

    Прогон 459 показал `pending` при живой аренде и идущей работе: отчёт
    по очереди говорил «ждёт», пока воркер разбирал порцию за порцией.
    Числа в отчёте, расходящиеся с делом, — тот же класс вранья, что и
    «развёрнут» про никогда не запускавшийся cleanup.sh.
    """
    job, _ = batched_job
    job.status = KnowledgeIngestStatus.PENDING
    session.flush()

    assert process_semantic_job(session, job) is False
    assert job.status == KnowledgeIngestStatus.RUNNING
