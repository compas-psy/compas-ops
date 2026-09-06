"""Local-only контракт памяти и доказательства, доходящие до вызывающего.

Две дыры, найденные аудитом владельца 06.09.2026 и подтверждённые на
исходниках. Обе про то, что видит человек, а не про внутренние структуры.

1. **Сбой probe оплачивался.** `_probe_local_answer()` возвращал `None`
   и при таймауте, и при разрыве, и при битом JSON. Вызывающий не мог
   отличить «локально ответа нет» (запланированная эскалация) от «мой
   собственный код не ответил», и сообщение молча уходило в платную
   модель. Отдельно к этому: потолок ожидания плагина был 5 секунд, а
   рефраз внутри probe имеет свои 20 при холодной задержке 5-8 —
   бесплатный ответ штатно не успевал, и владелец платил за вопрос,
   который система записывала как бесплатный.

2. **Ответ приходил без источников.** `ProbeResult` выносил наружу
   `evidence` только для документного пути, структурный путь S1 терял
   доказательства целиком, а `/internal/knowledge/probe` отдавал три
   поля и ничего из этого не пропускал. Спаны считались и умирали.

База здесь не нужна: обе проверки про решения кода, не про данные.
"""

from __future__ import annotations

import importlib.util
import pathlib
import urllib.error
import uuid

import pytest

from helm_core.knowledge import probe as probe_mod
from helm_core.knowledge import rephrase as rephrase_mod
from helm_core.knowledge.query_router import (
    AnswerPath, DoctorItem, DoctorsAnswer, Proof, QuestionIntent,
)

TENANT = uuid.UUID("00000000-0000-0000-0000-00000000beef")

#: Плагин лежит вне пакета и в каталоге с дефисом — обычным импортом не
#: берётся. На уровне модуля он тянет только stdlib, поэтому грузится
#: файлом без поднятия Hermes.
_PLUGIN_PATH = (pathlib.Path(__file__).resolve().parents[2]
                / "hermes" / "plugins" / "helm-control" / "__init__.py")


def _load_plugin():
    spec = importlib.util.spec_from_file_location("helm_control_under_test", _PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeSession:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)


def _stub_common(monkeypatch):
    monkeypatch.setattr(probe_mod, "bind_knowledge_user", lambda s, u: TENANT)
    monkeypatch.setattr(probe_mod, "is_future_reminder", lambda q: False)
    monkeypatch.setattr(probe_mod, "search_memories", lambda *a, **kw: [])


# ── 1. Сбой локального пути не оплачивается ──────────────────────────

@pytest.mark.parametrize("failure", [
    TimeoutError("timed out"),
    urllib.error.URLError("connection refused"),
    ValueError("Expecting value: line 1 column 1"),
])
def test_probe_failure_reports_itself_instead_of_returning_none(monkeypatch, failure):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_read_secret", lambda: "secret")
    monkeypatch.setattr(plugin.urllib.request, "urlopen",
                        lambda *a, **kw: (_ for _ in ()).throw(failure))

    result = plugin._probe_local_answer("какие анализы я сдавал?")

    assert result is not None, "сбой снова неотличим от «ответа нет» — это и есть оплата"
    assert result["outcome"] == "LOCAL_UNAVAILABLE"
    assert result["error"] == type(failure).__name__


def test_needs_reasoning_is_not_confused_with_failure(monkeypatch):
    """Запланированная эскалация обязана остаться эскалацией: правка
    закрывает оплату по сбою, а не бесплатность вообще."""
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_read_secret", lambda: "secret")

    class _Response:
        def read(self):
            return b'{"outcome": "NEEDS_REASONING", "mode": null, "answer_text": null}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(plugin.urllib.request, "urlopen", lambda *a, **kw: _Response())

    assert plugin._probe_local_answer("напиши план на неделю")["outcome"] == "NEEDS_REASONING"


def test_probe_budget_covers_the_rephrase_it_waits_for():
    """Гейт против повторного расхождения двух констант.

    Плагин ждёт probe, probe внутри ждёт рефраз. Если потолок плагина
    меньше, бесплатный ответ не успевает и вопрос уходит в платное —
    ровно то, что было до 06.09.2026 (5 против 20).
    """
    plugin = _load_plugin()
    assert plugin.PROBE_TIMEOUT >= rephrase_mod.REQUEST_TIMEOUT


# ── 2. Источники доходят до вызывающего во всех режимах ──────────────

def test_structured_answer_carries_its_spans(monkeypatch):
    """S1 считал спаны и терял их: `format_doctors()` источники не
    печатает, а наружу они не выносились."""
    _stub_common(monkeypatch)
    answer = DoctorsAnswer(question="каких врачей я посещал?",
                           intent=QuestionIntent.DOCTORS_VISITED)
    answer.path_used = AnswerPath.EVIDENCE
    answer.items = [DoctorItem(
        identity_id=str(uuid.uuid4()), person="Иванов И. И.",
        specialties=["гастроэнтеролог"],
        proofs=[Proof(source_id="src-1", window_id=4, char_start=10, char_end=42)])]
    monkeypatch.setattr(probe_mod, "answer_doctors_visited",
                        lambda session, *, question, knowledge_user_id: answer)

    result = probe_mod.probe(_FakeSession(), query="каких врачей я посещал?")

    assert result.mode == "S1"
    assert result.sources == [{"kind": "span", "source_id": "src-1", "window_id": 4,
                               "char_start": 10, "char_end": 42}]
    assert result.answer_run_id, "нечем сцепить увиденный ответ с серверной строкой"


def test_document_answer_carries_its_chunks(monkeypatch):
    _stub_common(monkeypatch)
    monkeypatch.setattr(probe_mod, "detect_intent", lambda q: QuestionIntent.UNSUPPORTED)
    evidence = [probe_mod.Evidence(chunk_id="c-1", source_id="src-2",
                                   chunk_text="Артериальное давление 120/80 мм рт. ст.",
                                   original_filename="выписка.pdf", rank=1.0)]
    monkeypatch.setattr(probe_mod, "_lexical_search", lambda *a, **kw: evidence)
    monkeypatch.setattr(probe_mod, "_health_lexical_search", lambda *a, **kw: [])
    monkeypatch.setattr(probe_mod, "embed_texts_or_none", lambda texts: [None])
    monkeypatch.setattr(probe_mod, "rephrase_or_none", lambda *a, **kw: None)

    result = probe_mod.probe(_FakeSession(), query="какое у меня было давление?")

    assert result.outcome == "LOCAL_ANSWER"
    assert result.sources == [{"kind": "chunk", "source_id": "src-2", "chunk_id": "c-1",
                               "original_filename": "выписка.pdf"}]
    assert result.answer_run_id


# ── 3. Непригодный кандидат не занимает место в колчане ──────────────

def test_unquotable_lexical_hits_do_not_block_the_vector_branch(monkeypatch):
    """Живой прогон 365 (06.09.2026): на «что там прописал врач?»
    лексика вернула пять подписей бланка, три из них `is_quotable`
    отбрасывал — но уже ПОСЛЕ того, как пятёрка закрыла колчан и
    векторная ветка не запросилась вовсе. Непригодный кандидат вытеснял
    пригодный дважды: из ответа и из самой возможности его поискать.
    """
    _stub_common(monkeypatch)
    monkeypatch.setattr(probe_mod, "detect_intent", lambda q: QuestionIntent.UNSUPPORTED)

    junk = [probe_mod.Evidence(chunk_id=f"c-{i}", source_id="src-1",
                               chunk_text="Врач: __________________",
                               original_filename=None, rank=1.0 - i / 100)
            for i in range(probe_mod.MAX_EVIDENCE)]
    real = probe_mod.Evidence(
        chunk_id="c-real", source_id="src-2",
        chunk_text="Назначен приём препарата по одной таблетке дважды в сутки.",
        original_filename="выписка.pdf", rank=0.5)

    asked_vector = {}

    def _vector(session, *, query_embedding, domain, knowledge_user_id,
                exclude_chunk_ids, source_ids=()):
        asked_vector["exclude"] = exclude_chunk_ids
        return [real]

    monkeypatch.setattr(probe_mod, "_lexical_search", lambda *a, **kw: junk)
    monkeypatch.setattr(probe_mod, "_health_lexical_search", lambda *a, **kw: [])
    monkeypatch.setattr(probe_mod, "embed_texts_or_none", lambda texts: [[0.1, 0.2]])
    monkeypatch.setattr(probe_mod, "_vector_search", _vector)
    monkeypatch.setattr(probe_mod, "_health_vector_search", lambda *a, **kw: [])
    monkeypatch.setattr(probe_mod, "rephrase_or_none", lambda *a, **kw: None)

    result = probe_mod.probe(_FakeSession(), query="какие назначения мне делали?")

    assert asked_vector, "вектор снова не запросился — колчан заняла бракованная лексика"
    assert result.outcome == "LOCAL_ANSWER"
    assert result.sources == [{"kind": "chunk", "source_id": "src-2", "chunk_id": "c-real",
                               "original_filename": "выписка.pdf"}]
    # Отбракованное не должно вернуться вторым путём.
    assert asked_vector["exclude"] == {item.chunk_id for item in junk}


def test_a_good_lexical_hit_below_the_junk_is_not_cut_off(monkeypatch):
    """Обрезка по MAX_EVIDENCE теперь после отбраковки: иначе пять строк
    бланка вытесняли бы шестого кандидата, который и есть текст."""
    _stub_common(monkeypatch)
    monkeypatch.setattr(probe_mod, "detect_intent", lambda q: QuestionIntent.UNSUPPORTED)

    junk = [probe_mod.Evidence(chunk_id=f"c-{i}", source_id="src-1",
                               chunk_text="Врач: __________________",
                               original_filename=None, rank=1.0 - i / 100)
            for i in range(probe_mod.MAX_EVIDENCE)]
    real = probe_mod.Evidence(
        chunk_id="c-real", source_id="src-2",
        chunk_text="Назначен приём препарата по одной таблетке дважды в сутки.",
        original_filename="выписка.pdf", rank=0.4)

    monkeypatch.setattr(probe_mod, "_lexical_search", lambda *a, **kw: [*junk, real])
    monkeypatch.setattr(probe_mod, "_health_lexical_search", lambda *a, **kw: [])
    monkeypatch.setattr(probe_mod, "embed_texts_or_none", lambda texts: [None])
    monkeypatch.setattr(probe_mod, "rephrase_or_none", lambda *a, **kw: None)

    result = probe_mod.probe(_FakeSession(), query="какие назначения мне делали?")

    assert result.outcome == "LOCAL_ANSWER"
    assert result.sources[0]["chunk_id"] == "c-real"


def test_graph_proof_is_reported_as_an_edge_not_an_empty_span(monkeypatch):
    """Живой прогон 367: все 35 источников структурного ответа пришли
    путём графа и уехали наружу как `span` с тремя `None`, а `edge_id`,
    который там есть, терялся. Спан без границ — не спан."""
    _stub_common(monkeypatch)
    answer = DoctorsAnswer(question="каких врачей я посещал?",
                           intent=QuestionIntent.DOCTORS_VISITED)
    answer.path_used = AnswerPath.GRAPH
    answer.items = [DoctorItem(
        identity_id=str(uuid.uuid4()), person="Иванов И. И.", specialties=[],
        proofs=[Proof(source_id="src-1", edge_id="edge-7")])]
    monkeypatch.setattr(probe_mod, "answer_doctors_visited",
                        lambda session, *, question, knowledge_user_id: answer)

    result = probe_mod.probe(_FakeSession(), query="каких врачей я посещал?")

    assert result.sources == [{"kind": "edge", "source_id": "src-1", "edge_id": "edge-7"}]


# ── 4. Строгий local-only: пустой поиск тоже не оплачивается ─────────
#
# Распоряжение владельца 06.09.2026: «Отсутствие находок не является
# моим разрешением оплатить ответ». До этой правки платный переход
# блокировался ТОЛЬКО при LOCAL_UNAVAILABLE, то есть при сбое; пустой
# поиск по личному вопросу давал NEEDS_REASONING и уходил в платную
# модель, которая данных владельца не знает.
#
# Проверяется решение диспетчера, а не флаг в журнале: `paid_ai_used=false`
# говорит, что строку записали бесплатной, и ничего не говорит о том,
# состоялся ли вызов.

class _Src:
    class _P:
        value = "telegram"
    platform = _P()
    chat_id = 42


class _Event:
    def __init__(self, text):
        self.text = text
        self.source = _Src()
        self.raw_message = {}
        self.message_id = 1
        self.user_id = 7


def _dispatch_with(monkeypatch, outcome_payload):
    """Диспетчер плагина с заглушенным всем, кроме решения про оплату."""
    plugin = _load_plugin()
    sent = []
    monkeypatch.setattr(plugin, "_message_has_attachment", lambda raw: False)
    monkeypatch.setattr(plugin, "_looks_like_remember_command", lambda text: False)
    monkeypatch.setattr(plugin, "_looks_like_admin_command", lambda text: False)
    monkeypatch.setattr(plugin, "_resolve_attachment", lambda *a, **kw: None)
    monkeypatch.setattr(plugin, "_resolve_batch", lambda *a, **kw: None)
    monkeypatch.setattr(plugin, "_register_task", lambda *a, **kw: {"task_id": "t-1"})
    monkeypatch.setattr(plugin, "_send_reply", lambda gw, src, text: sent.append(text))
    monkeypatch.setattr(plugin, "_probe_local_answer",
                        lambda text, context=None: outcome_payload)
    return plugin._on_pre_gateway_dispatch(_Event("какие анализы я сдавал?"), None), sent


def test_empty_local_search_does_not_reach_the_paid_model(monkeypatch):
    """Пустой поиск по личному вопросу: ход обязан оборваться здесь."""
    action, sent = _dispatch_with(monkeypatch, {
        "outcome": "LOCAL_NOT_FOUND", "mode": "N0",
        "answer_text": "В ваших записях я такого не нашёл."})
    assert action == {"action": "skip", "reason": "knowledge_probe_local_not_found"}
    assert sent == ["В ваших записях я такого не нашёл."]


def test_local_timeout_does_not_reach_the_paid_model(monkeypatch):
    action, sent = _dispatch_with(monkeypatch, {
        "outcome": "LOCAL_UNAVAILABLE", "error": "TimeoutError"})
    assert action == {"action": "skip", "reason": "knowledge_probe_unavailable"}
    assert sent and "не обращаюсь" in sent[0]


def test_local_model_error_does_not_reach_the_paid_model(monkeypatch):
    action, sent = _dispatch_with(monkeypatch, {
        "outcome": "LOCAL_UNAVAILABLE", "error": "URLError"})
    assert action == {"action": "skip", "reason": "knowledge_probe_unavailable"}
    assert sent


def test_a_general_question_still_reaches_the_paid_model(monkeypatch):
    """Запрет касается вопросов о данных владельца. «Переведи текст» и
    «что такое ферритин» платная модель отвечает как раньше — иначе
    правка чинила бы оплату ценой работоспособности."""
    action, _ = _dispatch_with(monkeypatch, {"outcome": "NEEDS_REASONING",
                                             "mode": None, "answer_text": None})
    assert action is None, "общий вопрос перестал доходить до модели"


# ── probe: какие вопросы вообще могут дойти до NEEDS_REASONING ───────

def test_personal_question_with_no_evidence_never_returns_needs_reasoning(monkeypatch):
    _stub_common(monkeypatch)
    monkeypatch.setattr(probe_mod, "detect_intent", lambda q: QuestionIntent.UNSUPPORTED)
    monkeypatch.setattr(probe_mod, "_lexical_search", lambda *a, **kw: [])
    monkeypatch.setattr(probe_mod, "_health_lexical_search", lambda *a, **kw: [])
    monkeypatch.setattr(probe_mod, "embed_texts_or_none", lambda texts: [None])

    result = probe_mod.probe(_FakeSession(), query="какие анализы я сдавал в мае?")

    assert result.outcome == "LOCAL_NOT_FOUND"
    assert result.mode == "N0"
    assert result.answer_run_id, "честное молчание тоже обязано попасть в журнал §14.14"
    assert "не нашёл" in result.answer_text.lower()


def test_general_question_with_no_evidence_still_escalates(monkeypatch):
    _stub_common(monkeypatch)
    monkeypatch.setattr(probe_mod, "detect_intent", lambda q: QuestionIntent.UNSUPPORTED)
    monkeypatch.setattr(probe_mod, "_lexical_search", lambda *a, **kw: [])
    monkeypatch.setattr(probe_mod, "_health_lexical_search", lambda *a, **kw: [])
    monkeypatch.setattr(probe_mod, "embed_texts_or_none", lambda texts: [None])

    result = probe_mod.probe(_FakeSession(), query="переведи этот текст на английский")

    assert result.outcome == "NEEDS_REASONING"


# ── 5. Уточнение вместо догадки ──────────────────────────────────────

def test_deictic_question_asks_instead_of_guessing(monkeypatch):
    """«Что ТАМ прописал врач» не имеет ответа сам по себе: «там»
    указывает на документ из разговора, а памяти разговора у probe нет
    (§6 CHUNKING_AND_BAD_ANSWERS). До 06.09.2026 система отвечала на
    такой вопрос ближайшим похожим текстом, то есть угадывала, о чём
    речь — и в живом чате владельца выдала подпись врача из чужого
    протокола."""
    _stub_common(monkeypatch)
    searched = []
    monkeypatch.setattr(probe_mod, "_lexical_search",
                        lambda *a, **kw: searched.append("lexical") or [])
    monkeypatch.setattr(probe_mod, "_health_lexical_search",
                        lambda *a, **kw: searched.append("health") or [])

    result = probe_mod.probe(_FakeSession(), query="что там прописал врач?")

    assert result.outcome == "NEEDS_CLARIFICATION"
    assert result.mode == "Q0"
    assert "уточните" in result.answer_text.lower()
    assert not searched, "искали, хотя не поняли, о чём вопрос"
    assert result.answer_run_id, "уточнение тоже уход от платной модели, §14.14"


def test_clarification_does_not_reach_the_paid_model(monkeypatch):
    action, sent = _dispatch_with(monkeypatch, {
        "outcome": "NEEDS_CLARIFICATION", "mode": "Q0",
        "answer_text": "Уточните, о каком документе речь."})
    assert action == {"action": "skip", "reason": "knowledge_probe_needs_clarification"}
    assert sent == ["Уточните, о каком документе речь."]
