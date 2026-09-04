"""v4.0 R3 — атомизатор v2 на полный источник и публикация ревизии.

Проверяется §30.8.5 D построчно:

    каждое окно источника терминально
    содержимое после 4000-го символа действительно разобрано
    больше 20 атомов в длинном документе не обрезаются молча
    упёрлись в потолок окна → деление и перезапуск, не отбрасывание
    неустранимый сбой окна → semantic DEGRADED
    L1 остаётся доступен поиску при деградировавшем L2

и первое требование владельца к R3: писатель публикует граф ТОЛЬКО
через `semantic run → проверка → READY → атомарное переключение`.

Извлекатель здесь поддельный. Это не упрощение ради скорости: R3 — про
покрытие, ограничения и публикацию, а КАЧЕСТВО извлечения меряет R4
отдельным бенчмарком (§14.18). Тест с живой моделью проверял бы обе
вещи разом и не доказал бы ни одной: §14.23 прямо называет нарушением
«treating 500+ unit tests with mocked atomizer as proof of semantic
extraction quality». Здесь доказывается не качество, а механика.
"""

import uuid

import pytest
from sqlalchemy import select

from helm_core.knowledge.ingest import ingest_text
from helm_core.knowledge.probe import probe
from helm_core.knowledge.semantic_extract import (
    ExtractedAtom, ExtractedEdge, ExtractedEntity, ExtractionFailed, MAX_ATOMS_PER_WINDOW,
    WindowExtraction, WindowTruncated,
)
from helm_core.knowledge.semantic_publish import publish_semantic_run
from helm_core.knowledge.semantic_windows import build_windows
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import (
    KnowledgeEdge, KnowledgeNode, KnowledgeNodeMention, KnowledgeSemanticRun,
    KnowledgeSemanticWindow, KnowledgeSource, SemanticRunStatus, SemanticWindowStatus,
    TERMINAL_WINDOW_STATUSES,
)

from conftest import SYSTEM_OWNER_ID

#: Маркер, по которому видно, что разобран КОНЕЦ длинного документа, а
#: не только его начало. Ставится за 4000-м символом намеренно: ровно
#: там semantic-v1 переставал смотреть.
TAIL_MARKER = "Иванов Пётр Сергеевич, эндокринолог"


def long_source_text() -> str:
    """Длинный документ с заголовками и опознаваемым хвостом."""
    filler = ("Общий осмотр без особенностей. Жалоб нет. Рекомендован повторный "
              "визит через полгода. ")
    return (
        "# Приём\n\n"
        + filler * 60                      # заведомо больше 4000 символов
        + "\n\n## Заключение\n\n"
        + f"Консультацию провёл {TAIL_MARKER}. Назначен контроль через полгода.\n"
    )


def _quote(window_text: str, ordinal: int) -> str:
    """Цитата из ЭТОГО окна, разная для разных узлов.

    Поддельный извлекатель обязан быть grounded, как настоящий: с R4.5.3
    `validate()` выбрасывает любой элемент без дословной цитаты из окна,
    так что ответ без `evidence_quote` не смог бы дойти до публикации
    вообще. С R5 от цитаты зависит ещё и точный диапазон упоминания, и
    цитаты нарочно берутся разные — иначе тест не отличил бы «диапазон
    узла» от «диапазона окна».

    Слова склеиваются одним пробелом, тогда как в источнике между ними
    может стоять перенос строки: это заодно проверяет, что поиск
    диапазона терпим к пробелам ровно так же, как grounding в
    `semantic_extract`.
    """
    words = window_text.split()
    start = min(ordinal * 3, max(len(words) - 3, 0))
    return " ".join(words[start:start + 3]) or window_text[:20]


def _extraction(prefix: str, *, window_text: str, atoms: int = 1,
                label: str | None = None) -> WindowExtraction:
    entity = ExtractedEntity(local_id=f"{prefix}e", entity_type="PERSON",
                             label=label or f"Сущность {prefix}", aliases=(),
                             evidence_quote=label if label and label in window_text
                             else _quote(window_text, 0))
    made = [ExtractedAtom(local_id=f"{prefix}a{i}", kind="event",
                          title=f"Событие {prefix}-{i}", text=f"Текст {prefix}-{i}",
                          evidence_quote=_quote(window_text, i + 1))
            for i in range(atoms)]
    edges = [ExtractedEdge(from_local_id=a.local_id, relation_type="involves",
                           to_local_id=entity.local_id, role="doctor") for a in made]
    return WindowExtraction(entities=[entity], atoms=made, edges=edges)


def marker_aware_extractor(window_text, *, domain, heading_path=(), model=""):
    """Достаёт из окна ровно то, что в нём есть: если маркер попал в это
    окно — сущность называется им. Так тест отличает «разобрали хвост» от
    «разобрали что-нибудь»."""
    prefix = f"w{abs(hash(window_text)) % 10000}"
    if TAIL_MARKER in window_text:
        return _extraction(prefix, window_text=window_text, label=TAIL_MARKER)
    return _extraction(prefix, window_text=window_text)


@pytest.fixture
def source(session):
    bind_knowledge_user(session, SYSTEM_OWNER_ID)
    src = ingest_text(session, domain="personal", text=long_source_text())
    session.flush()
    return src


def _windows(session, run_id):
    return session.scalars(select(KnowledgeSemanticWindow).where(
        KnowledgeSemanticWindow.semantic_run_id == run_id).order_by(
        KnowledgeSemanticWindow.ordinal)).all()


# ── §30.8.5 D ────────────────────────────────────────────────────────────

def test_every_window_of_the_source_is_terminal(session, source):
    result = publish_semantic_run(session, source=source, text=long_source_text(),
                                  extract=marker_aware_extractor)

    rows = _windows(session, result.run_id)
    assert rows, "окна не заведены — проверять нечего"
    stuck = [w for w in rows if w.status not in {s.value for s in TERMINAL_WINDOW_STATUSES}]
    assert not stuck, f"{len(stuck)} окон осталось в промежуточном состоянии"
    assert result.windows_total == len(rows)


def test_content_after_char_4000_is_actually_atomized(session, source):
    """Главный дефект semantic-v1: `text[:4000]`. Документ длиннее, и
    маркер стоит за отсечкой — узел с ним доказывает, что хвост дошёл до
    модели, а не был молча выброшен."""
    text = long_source_text()
    assert text.index(TAIL_MARKER) > 4000, "фикстура перестала быть длинной"

    result = publish_semantic_run(session, source=source, text=text,
                                  extract=marker_aware_extractor)

    labels = session.scalars(select(KnowledgeNode.canonical_label).where(
        KnowledgeNode.semantic_run_id == result.run_id)).all()
    assert TAIL_MARKER in labels


def test_long_source_keeps_more_than_twenty_atoms(session, source):
    """«>20 meaningful atoms in long fixture are not silently truncated».
    Потолок стоит на ОКНО, а источник состоит из многих окон — общее
    число атомов не ограничено ничем, кроме самого текста."""
    result = publish_semantic_run(
        session, source=source, text=long_source_text(),
        extract=lambda t, *, domain, heading_path=(), model="": _extraction(
            f"w{abs(hash(t)) % 10000}", window_text=t, atoms=8))

    atoms = session.scalars(select(KnowledgeNode).where(
        KnowledgeNode.semantic_run_id == result.run_id,
        KnowledgeNode.kind == "event")).all()
    assert len(atoms) > 20, f"атомов всего {len(atoms)}"
    assert result.status == SemanticRunStatus.READY


def test_window_at_the_cap_is_split_not_truncated(session, source):
    """Упёрлись в потолок — окно делится и перезапускается (§14.4.1).

    Поддельный извлекатель переполняется на длинном тексте и умещается
    на коротком: ровно то поведение, ради которого деление и заведено.
    Проверяются обе стороны — родитель помечен SPLIT, и у него есть
    дети, чей разбор дошёл до конца.
    """
    def cap_on_long(window_text, *, domain, heading_path=(), model=""):
        if len(window_text) > 900:
            raise WindowTruncated("окно переполнено")
        return _extraction(f"w{abs(hash(window_text)) % 10000}", window_text=window_text)

    result = publish_semantic_run(session, source=source, text=long_source_text(),
                                  extract=cap_on_long)

    rows = _windows(session, result.run_id)
    split = [w for w in rows if w.status == SemanticWindowStatus.SPLIT]
    children = [w for w in rows if w.parent_window_id is not None]
    assert split, "ни одно окно не было разделено"
    assert children, "деление не породило дочерних окон"
    assert all(c.char_end > c.char_start for c in children)
    # Ничего не потеряно: разобранные дети покрывают текст родителя.
    for parent in split:
        mine = [c for c in children if c.parent_window_id == parent.id]
        assert mine, f"окно {parent.ordinal} помечено SPLIT, но детей нет"
        assert min(c.char_start for c in mine) == parent.char_start
        assert max(c.char_end for c in mine) == parent.char_end


def test_unrecoverable_window_makes_the_run_degraded(session, source):
    """§14.19: неустранимый сбой одного участка — DEGRADED, а не
    «документ готов» и не «всё пропало»."""
    calls = {"n": 0}

    def fails_once(window_text, *, domain, heading_path=(), model=""):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ExtractionFailed("модель не отвечает")
        return _extraction(f"w{calls['n']}", window_text=window_text)

    result = publish_semantic_run(session, source=source, text=long_source_text(),
                                  extract=fails_once)

    assert result.status == SemanticRunStatus.DEGRADED
    assert result.windows_failed == 1
    assert 0 < result.coverage_ratio < 1
    failed = [w for w in _windows(session, result.run_id)
              if w.status == SemanticWindowStatus.FAILED]
    assert [w.error_code for w in failed] == ["EXTRACTION_FAILED"]


def test_l1_stays_searchable_when_l2_degraded(session, source):
    """§14.25: «A source may remain L1_READY + SEMANTIC_DEGRADED; it is
    searchable by FTS/pgvector». Деградировавшая семантика не должна
    ничего отнимать у обычного поиска."""
    def always_fails(window_text, *, domain, heading_path=(), model=""):
        raise ExtractionFailed("модель недоступна")

    result = publish_semantic_run(session, source=source, text=long_source_text(),
                                  extract=always_fails)
    assert result.status == SemanticRunStatus.FAILED

    found = probe(session, query="консультацию провёл эндокринолог",
                  knowledge_user_id=SYSTEM_OWNER_ID)
    assert found.evidence, "L1 перестал искаться из-за провала L2"


# ── инвариант владельца: публикация только через ревизию ─────────────────

def test_ready_run_becomes_current_atomically(session, source):
    result = publish_semantic_run(session, source=source, text=long_source_text(),
                                  extract=marker_aware_extractor)

    assert result.status == SemanticRunStatus.READY
    assert result.switched is True
    assert session.scalar(select(KnowledgeSource.current_semantic_run_id).where(
        KnowledgeSource.id == source.id)) == result.run_id


@pytest.mark.parametrize("extractor,expected", [
    (lambda t, *, domain, heading_path=(), model="": (_ for _ in ()).throw(
        ExtractionFailed("нет модели")), SemanticRunStatus.FAILED),
])
def test_failed_run_never_becomes_current(session, source, extractor, expected):
    """Неудачный разбор не трогает указатель. Прежняя ревизия — если она
    есть — остаётся текущей (§14.20)."""
    result = publish_semantic_run(session, source=source, text=long_source_text(),
                                  extract=extractor)

    assert result.status == expected
    assert result.switched is False
    assert session.scalar(select(KnowledgeSource.current_semantic_run_id).where(
        KnowledgeSource.id == source.id)) is None


def test_degraded_run_does_not_replace_a_good_one(session, source):
    """Самое важное следствие инварианта: неудачная пересборка НЕ рушит
    рабочий граф (§14.20 «Never destroy last known-good semantic graph
    before replacement passes»)."""
    good = publish_semantic_run(session, source=source, text=long_source_text(),
                                extract=marker_aware_extractor)
    assert good.switched is True

    calls = {"n": 0}

    def fails_once(window_text, *, domain, heading_path=(), model=""):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ExtractionFailed("модель не отвечает")
        return _extraction(f"r{calls['n']}", window_text=window_text)

    worse = publish_semantic_run(session, source=source, text=long_source_text(),
                                 extract=fails_once, semantic_version=3)

    assert worse.status == SemanticRunStatus.DEGRADED
    assert worse.switched is False
    assert session.scalar(select(KnowledgeSource.current_semantic_run_id).where(
        KnowledgeSource.id == source.id)) == good.run_id


def test_every_node_of_a_run_has_provenance(session, source):
    """§30.8.5 F: у каждого узла есть упоминание с местом в источнике.
    Узел без происхождения — утверждение без доказательства."""
    result = publish_semantic_run(session, source=source, text=long_source_text(),
                                  extract=marker_aware_extractor)

    nodes = session.scalars(select(KnowledgeNode).where(
        KnowledgeNode.semantic_run_id == result.run_id)).all()
    mentions = session.scalars(select(KnowledgeNodeMention).where(
        KnowledgeNodeMention.semantic_run_id == result.run_id)).all()

    assert {n.id for n in nodes} == {m.node_id for m in mentions}
    for mention in mentions:
        assert mention.source_id == source.id
        assert mention.char_end > mention.char_start
        assert mention.evidence_text_hash
        assert mention.evidence_type == "extracted"


def test_provenance_span_points_at_the_node_quote_not_the_whole_window(session, source):
    """R5 (§30.8.5 F «exact span»): диапазон упоминания — место КОНКРЕТНОЙ
    цитаты узла в источнике, а не границы всего окна, одинаковые у всех
    его узлов. До R5 второе выдавалось за первое.

    Доказывается двумя способами сразу: диапазон уже окна, из которого
    узел пришёл, и у узлов ОДНОГО окна диапазоны разные.
    """
    text = long_source_text()
    result = publish_semantic_run(session, source=source, text=text,
                                  extract=lambda t, *, domain, heading_path=(), model="":
                                  _extraction(f"w{abs(hash(t)) % 10000}", window_text=t, atoms=3))

    windows = {w.ordinal: w for w in _windows(session, result.run_id)}
    mentions = session.scalars(select(KnowledgeNodeMention).where(
        KnowledgeNodeMention.semantic_run_id == result.run_id)).all()
    assert mentions

    by_window: dict[int, set[tuple[int, int]]] = {}
    for mention in mentions:
        window = windows[mention.window_id]
        assert mention.char_start is not None, "точный диапазон обязан быть найден"
        # Строго внутри окна и строго уже него — иначе это снова «границы окна».
        assert window.char_start <= mention.char_start < mention.char_end <= window.char_end
        assert (mention.char_end - mention.char_start) < (window.char_end - window.char_start)
        # И указывает на реальный текст источника, а не на абстрактное смещение.
        assert text[mention.char_start:mention.char_end].split()
        by_window.setdefault(mention.window_id, set()).add(
            (mention.char_start, mention.char_end))

    assert any(len(spans) > 1 for spans in by_window.values()), (
        "у всех узлов окна один диапазон — происхождение снова пооконное")


def test_edges_are_typed_and_bound_to_the_run(session, source):
    result = publish_semantic_run(session, source=source, text=long_source_text(),
                                  extract=marker_aware_extractor)

    edges = session.scalars(select(KnowledgeEdge).where(
        KnowledgeEdge.semantic_run_id == result.run_id)).all()
    assert edges
    assert {e.relation_type for e in edges} == {"involves"}
    assert all(e.role == "doctor" for e in edges)
    assert all(e.evidence_type == "extracted" for e in edges)


def test_no_knowledge_window_is_distinguishable_from_a_silent_failure(session, source):
    """§14.4.1: PROCESSED-окно хранит хэш результата даже при нуле узлов,
    чтобы «нечего извлекать» отличалось от «модель вернула неполный
    объект». Пустой разбор даёт NO_KNOWLEDGE — С хэшем."""
    result = publish_semantic_run(
        session, source=source, text=long_source_text(),
        extract=lambda t, *, domain, heading_path=(), model="": WindowExtraction())

    rows = _windows(session, result.run_id)
    assert rows
    assert all(w.status == SemanticWindowStatus.NO_KNOWLEDGE for w in rows)
    assert all(w.result_hash for w in rows), "нет отпечатка результата — аудит невозможен"
    assert result.status == SemanticRunStatus.READY
    assert result.nodes_created == 0
