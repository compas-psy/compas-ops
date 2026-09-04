"""v4.0 §14.4.2/§14.4.3 — контракт вывода извлекателя и его проверка.

Проверяется НЕ качество извлечения: его меряет R4 отдельным бенчмарком
(§14.18), и §14.23 прямо называет нарушением выдавать сотни юнит-тестов
с поддельной моделью за доказательство качества. Здесь доказывается
другое — что мусор не попадает в граф молча, а обязательные поля
обязательны.

Каждый случай ниже взят из живых прогонов 02.09.2026: модель этого
класса возвращает один объект вместо массива, путает регистр, ссылается
на несуществующие local_id и изобретает типы связей.

R4.5.3 (владелец 03.09.2026): «словами в промпте безопасность не
обеспечить» — evidence_quote обязателен на entity/atom/edge и обязан
быть дословной подстрокой окна. WINDOW_TEXT ниже — окно, на фоне
которого проверяются все цитаты payload() по умолчанию.
"""

import json

import pytest

from helm_core.knowledge.semantic_extract import (
    MAX_ATOMS_PER_WINDOW, MAX_SPLIT_DEPTH, NODE_RESPONSE_SCHEMA, NODE_SYSTEM_PROMPT,
    ExtractionFailed, ExtractionTimedOut, WindowTruncated, extract_nodes_window, extract_window,
    validate,
)

WINDOW_TEXT = (
    "19 августа 2026 года в клинике был приём у уролога Кириченко "
    "Сергея Александровича. Приём у Кириченко."
)


def payload(**overrides) -> str:
    data = {
        "entities": [
            {"local_id": "e1", "entity_type": "PERSON", "subtype": "doctor",
             "label": "Кириченко Сергей Александрович", "aliases": ["Кириченко С.А."],
             "evidence_quote": "Кириченко Сергея Александровича"},
            {"local_id": "e2", "entity_type": "CONCEPT", "subtype": "medical_specialty",
             "label": "уролог", "aliases": [], "evidence_quote": "уролога"},
        ],
        "atoms": [
            {"local_id": "a1", "kind": "EVENT", "subtype": "medical_visit",
             "title": "Приём уролога", "text": "Приём у Кириченко.",
             "occurred_at": "2026-08-19", "date_precision": "DAY",
             "evidence_quote": WINDOW_TEXT},
        ],
        "edges": [
            {"from": "a1", "type": "INVOLVES", "to": "e1", "role": "doctor",
             "evidence_quote": "Приём у Кириченко."},
            {"from": "e1", "type": "HAS_ROLE", "to": "e2",
             "evidence_quote": "уролога Кириченко Сергея Александровича"},
        ],
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def test_normative_example_from_the_spec_is_accepted() -> None:
    """Форма из §14.4.2 — не «пример красивого JSON», а контракт."""
    result = validate(payload(), window_text=WINDOW_TEXT)

    assert [e.local_id for e in result.entities] == ["e1", "e2"]
    assert result.entities[0].aliases == ("Кириченко С.А.",)
    assert result.atoms[0].kind == "event"
    assert result.atoms[0].date_precision == "day"
    assert [(e.from_local_id, e.relation_type, e.to_local_id) for e in result.edges] == [
        ("a1", "involves", "e1"), ("e1", "has_role", "e2")]
    assert result.rejected == []


def test_registry_of_relations_is_closed_and_unknown_becomes_related_to() -> None:
    """§14.9: неизвестный тип нормализуется к реестру либо становится
    RELATED_TO — но НЕ выбрасывается: связь была, и потерять её значит
    соврать об отсутствии."""
    result = validate(payload(edges=[
        {"from": "a1", "type": "ЛЕЧИЛ", "to": "e1", "evidence_quote": "Приём у Кириченко."}]),
        window_text=WINDOW_TEXT)

    assert [e.relation_type for e in result.edges] == ["related_to"]
    assert any("related_to" in note for note in result.rejected)


def test_edge_into_nowhere_is_dropped_and_counted() -> None:
    """Ребро на несуществующий local_id — не связь, а опечатка модели.
    Оно отбрасывается, но счётчик отброшенного растёт: молчаливое
    выбрасывание неотличимо от «модель ничего не нашла»."""
    result = validate(payload(edges=[
        {"from": "a1", "type": "ABOUT", "to": "нет-такого", "evidence_quote": "Приём у Кириченко."}]),
        window_text=WINDOW_TEXT)

    assert result.edges == []
    assert result.rejected == ["связь в никуда: 'a1' → 'нет-такого'"]


def test_atom_kind_outside_the_registry_is_rejected() -> None:
    result = validate(payload(atoms=[
        {"local_id": "a1", "kind": "ЗАМЕТКА", "title": "т", "text": "т",
         "evidence_quote": "Приём у Кириченко."}]),
        window_text=WINDOW_TEXT)

    assert result.atoms == []
    assert any("вне реестра" in note for note in result.rejected)


def test_entity_kinds_are_not_forced_into_the_node_registry() -> None:
    """`entity_type` модели — это подвид сущности (PERSON, ORGANIZATION),
    а не `kind` узла: у сущности вид всегда ENTITY. Смешать их значило бы
    завести узлы вида «person», которых нет в реестре §14.5."""
    # edges=[] намеренно: связи из образца ссылаются на сущности,
    # которых в этом наборе нет, и их отбрасывание засоряло бы проверку
    # посторонним поводом.
    result = validate(payload(
        entities=[{"local_id": "e1", "entity_type": "ORGANIZATION", "label": "клиника",
                   "evidence_quote": "в клинике"}],
        edges=[]), window_text=WINDOW_TEXT)

    assert result.entities[0].entity_type == "ORGANIZATION"
    assert result.rejected == []


def test_missing_required_field_drops_the_record_not_the_answer() -> None:
    """Та же дисциплина, что у разбора frontmatter: запись без
    обязательного поля пропускается целиком, а не додумывается."""
    result = validate(payload(atoms=[
        {"local_id": "a1", "kind": "EVENT", "title": "", "text": "есть",
         "evidence_quote": "Приём у Кириченко."},
        {"local_id": "a2", "kind": "FACT", "title": "есть", "text": "есть",
         "evidence_quote": "Приём у Кириченко."},
    ], edges=[]), window_text=WINDOW_TEXT)

    assert [a.local_id for a in result.atoms] == ["a2"]
    assert len(result.rejected) == 1


def test_duplicate_local_id_is_rejected() -> None:
    result = validate(payload(entities=[
        {"local_id": "e1", "entity_type": "PERSON", "label": "первый",
         "evidence_quote": "в клинике"},
        {"local_id": "e1", "entity_type": "PERSON", "label": "второй"},
    ]), window_text=WINDOW_TEXT)

    assert [e.label for e in result.entities] == ["первый"]
    assert any("повтор local_id" in note for note in result.rejected)


def test_unknown_date_precision_keeps_the_atom_and_marks_unknown() -> None:
    """§14.8 запрещает выдумывать точность даты, а не хранить дату.
    Атом остаётся, точность становится «неизвестна»."""
    result = validate(payload(atoms=[
        {"local_id": "a1", "kind": "FACT", "title": "т", "text": "т",
         "occurred_at": "2026-08-19", "date_precision": "ПРИМЕРНО",
         "evidence_quote": "Приём у Кириченко."}]),
        window_text=WINDOW_TEXT)

    assert result.atoms[0].occurred_at == "2026-08-19"
    assert result.atoms[0].date_precision == "unknown"


@pytest.mark.parametrize("raw,reason", [
    ("не json вовсе", "невалидный JSON"),
    ("[]", "ожидался объект"),
    ('"строка"', "ожидался объект"),
])
def test_malformed_answer_is_a_failure_not_an_empty_result(raw, reason) -> None:
    """Живой дефект: `format: "json"` гарантирует валидный JSON, но не
    его форму — модель возвращала массив или один объект. Пустой
    результат вместо ошибки означал бы «в тексте ничего нет»."""
    with pytest.raises(ExtractionFailed) as err:
        validate(raw, window_text=WINDOW_TEXT)
    assert reason in str(err.value)


def test_window_at_the_cap_raises_instead_of_truncating() -> None:
    """§14.4.1: упёрлись в потолок — окно делится, а не обрезается.
    Именно здесь semantic-v1 делал `data[:MAX_ATOMS_PER_CALL]`.

    evidence_quote="т" — не реалистичная цитата, а дешёвая грамматическая
    случайность («августа» внутри WINDOW_TEXT содержит «т»): тесту нужен
    только факт превышения потолка, не правдоподобие содержимого."""
    atoms = [{"local_id": f"a{i}", "kind": "FACT", "title": f"т{i}", "text": "т",
              "evidence_quote": "т"}
             for i in range(MAX_ATOMS_PER_WINDOW + 5)]

    with pytest.raises(WindowTruncated):
        validate(payload(atoms=atoms, edges=[]), window_text=WINDOW_TEXT)


def test_repair_attempts_are_bounded_and_then_the_window_fails() -> None:
    """§14.4.3: «invalid output → one or more bounded local repair/retry
    attempts; persistent failure → DEGRADED, never cloud fallback».
    Проверяется и число попыток, и то, что вторая попытка получает
    жалобу на первую — повтор того же промпта дал бы тот же ответ."""
    seen = []

    def broken(prompt, *, model, keep_alive=None):
        seen.append(prompt)
        return "не json"

    import helm_core.knowledge.semantic_extract as module
    original = module._call_ollama
    module._call_ollama = broken
    try:
        with pytest.raises(ExtractionFailed):
            extract_window("текст окна", domain="personal", attempts=3)
    finally:
        module._call_ollama = original

    assert len(seen) == 3
    assert "Прошлый ответ отклонён" not in seen[0]
    assert "Прошлый ответ отклонён" in seen[1]


def test_extraction_never_leaves_the_machine() -> None:
    """§14.4.3 «Ollama/local deterministic extraction only; no LiteLLM/
    OpenRouter». Проверяется буквально по исходнику: единственный адрес,
    к которому модуль обращается, — локальная Ollama."""
    import ast
    import inspect

    import helm_core.knowledge.semantic_extract as module

    # Проверяются строковые литералы, а не весь файл: докстринг модуля
    # называет запрещённые пути своими именами — иначе запрет пришлось
    # бы объяснять иносказаниями. Значение имеет то, куда код может
    # обратиться, а не то, о чём он рассказывает.
    tree = ast.parse(inspect.getsource(module))
    urls = [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and "://" in node.value]

    assert urls == [module.OLLAMA_URL], f"извлекатель знает посторонние адреса: {urls}"
    assert module.OLLAMA_URL.startswith("http://ollama:")


def test_call_ollama_requests_deterministic_generation(monkeypatch) -> None:
    """R4 п.4: «temperature=0, fixed seed where supported» — иначе
    reprocess (§14.20) того же источника менял бы граф без причины, а
    сравнение кандидатов в бенчмарке мерило бы шум, а не разницу моделей."""
    import helm_core.knowledge.semantic_extract as module

    captured = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read(self):
            return json.dumps({"response": "{}"}).encode()

    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data.decode())
        return _FakeResponse()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    module._call_ollama("окно", model="gemma2:2b")
    assert captured["body"]["options"]["temperature"] == 0
    assert captured["body"]["options"]["seed"] == module.DETERMINISTIC_SEED


class TestNodeOnlyProductionPath:
    """P1/P2 (владелец 2026-09-04, remediation после R4 RCA run 241):
    node-only схема/промпт для `extract_nodes_window()` и timeout→split
    вместо identical retry. `extract_window()` (старая схема, с edges)
    остаётся нетронутым — эти тесты не про него."""

    def test_schema_has_no_edges_and_strict_enums(self) -> None:
        assert "edges" not in NODE_RESPONSE_SCHEMA["properties"]
        assert NODE_RESPONSE_SCHEMA["required"] == ["entities", "atoms"]
        entity_type_enum = (
            NODE_RESPONSE_SCHEMA["properties"]["entities"]["items"]["properties"]["entity_type"]["enum"])
        assert set(entity_type_enum) == {"PERSON", "ORGANIZATION", "PLACE", "CONCEPT"}
        kind_enum = NODE_RESPONSE_SCHEMA["properties"]["atoms"]["items"]["properties"]["kind"]["enum"]
        assert set(kind_enum) == {"event", "fact", "decision", "concept"}

    def test_prompt_never_mentions_edges_or_relations(self) -> None:
        """P1: «Prompt тоже не должен упоминать создание relations/local-id
        edges»."""
        lowered = NODE_SYSTEM_PROMPT.lower()
        assert "edge" not in lowered
        assert "связ" not in lowered

    def test_accepts_response_without_edges_key(self, monkeypatch) -> None:
        import helm_core.knowledge.semantic_extract as module

        def fake_call_ollama(prompt, *, model, keep_alive=None, system=None, response_schema=None):
            assert response_schema is NODE_RESPONSE_SCHEMA
            assert system is NODE_SYSTEM_PROMPT
            return json.dumps({
                "entities": [{"local_id": "e1", "entity_type": "PLACE", "label": "Казань",
                             "evidence_quote": "Казани"}],
                "atoms": [],
            })

        monkeypatch.setattr(module, "_call_ollama", fake_call_ollama)
        result = extract_nodes_window("В Казани.", domain="personal")
        assert [e.local_id for e in result.entities] == ["e1"]
        assert result.edges == []

    def test_transport_timeout_is_not_retried_identically(self, monkeypatch) -> None:
        """R4 RCA run 241: `long_dense_window` получило 3 identical
        120-секундных timeout подряд (360.32с, 0 новой информации). На
        timeout полный текст окна уходит на вход РОВНО ОДИН раз — вместо
        повтора окно делится и отправляется частями."""
        import helm_core.knowledge.semantic_extract as module

        original_text = "Первое предложение тут. Второе предложение там."
        fragment_marker = f"Фрагмент:\n{original_text}"
        seen_prompts = []

        def fake(prompt, *, model, keep_alive=None, system=None, response_schema=None):
            seen_prompts.append(prompt)
            if fragment_marker in prompt:
                try:
                    raise TimeoutError("timed out")
                except TimeoutError as exc:
                    raise ExtractionFailed(f"извлекатель недоступен: {exc}") from exc
            return json.dumps({"entities": [], "atoms": []})

        monkeypatch.setattr(module, "_call_ollama", fake)
        extract_nodes_window(original_text, domain="personal")

        full_window_calls = sum(1 for p in seen_prompts if fragment_marker in p)
        assert full_window_calls == 1
        assert len(seen_prompts) > 1, "после timeout окно должно быть поделено и отправлено частями"

    def test_timeout_then_split_pieces_merge_without_colliding_local_ids(self, monkeypatch) -> None:
        """Результаты кусков после деления по timeout склеиваются в один
        `WindowExtraction`, а local_id из разных кусков не путаются, даже
        если модель независимо назвала оба куска `e1`."""
        import helm_core.knowledge.semantic_extract as module

        original_text = "Встречу вёл Иванов. Встречу вела Петрова."
        fragment_marker = f"Фрагмент:\n{original_text}"

        def fake(prompt, *, model, keep_alive=None, system=None, response_schema=None):
            if fragment_marker in prompt:
                try:
                    raise TimeoutError("timed out")
                except TimeoutError as exc:
                    raise ExtractionFailed(f"извлекатель недоступен: {exc}") from exc
            if "Петрова" in prompt:
                return json.dumps({"entities": [{"local_id": "e1", "entity_type": "PERSON",
                                                 "label": "Петрова", "evidence_quote": "Петрова"}],
                                   "atoms": []})
            return json.dumps({"entities": [{"local_id": "e1", "entity_type": "PERSON",
                                             "label": "Иванов", "evidence_quote": "Иванов"}],
                               "atoms": []})

        monkeypatch.setattr(module, "_call_ollama", fake)
        result = extract_nodes_window(original_text, domain="personal")
        labels = sorted(e.label for e in result.entities)
        assert labels == ["Иванов", "Петрова"]
        assert len({e.local_id for e in result.entities}) == 2

    def test_malformed_json_still_uses_repair_retry_not_split(self, monkeypatch) -> None:
        """P2: «Repair retries сохранить только для malformed JSON/schema
        failure» — не транспортный timeout, значит окно НЕ делится, а
        чинится тем же identical-с-жалобой retry, что и раньше."""
        import helm_core.knowledge.semantic_extract as module

        seen = []

        def broken(prompt, *, model, keep_alive=None, system=None, response_schema=None):
            seen.append(prompt)
            return "не json"

        monkeypatch.setattr(module, "_call_ollama", broken)
        with pytest.raises(ExtractionFailed):
            extract_nodes_window("текст окна", domain="personal", attempts=3)

        assert len(seen) == 3
        assert "Прошлый ответ отклонён" not in seen[0]
        assert "Прошлый ответ отклонён" in seen[1]

    def test_split_gives_up_when_text_is_unsplittable(self, monkeypatch) -> None:
        """P2: «bounded recursion» — текст без границ абзаца/предложения,
        который всё равно timeout-ит, не крутится бесконечно: явный провал
        (coverage contract), не тихая потеря содержимого."""
        import helm_core.knowledge.semantic_extract as module

        calls = []

        def always_timeout(prompt, *, model, keep_alive=None, system=None, response_schema=None):
            calls.append(prompt)
            try:
                raise TimeoutError("timed out")
            except TimeoutError as exc:
                raise ExtractionFailed(f"извлекатель недоступен: {exc}") from exc

        monkeypatch.setattr(module, "_call_ollama", always_timeout)
        with pytest.raises(ExtractionTimedOut):
            extract_nodes_window("однопредложениебезточкиибезграниц", domain="personal")
        assert len(calls) == 1

    def test_split_recursion_is_bounded_by_max_split_depth(self, monkeypatch) -> None:
        """Даже когда текст ДЕЛИТСЯ (в отличие от предыдущего теста), но
        каждый кусок всё равно timeout-ит — рекурсия не бесконечна: рано
        или поздно кусок становится неделимым (одно предложение), и
        исключение поднимается, а не проглатывается."""
        import helm_core.knowledge.semantic_extract as module

        calls = []

        def always_timeout(prompt, *, model, keep_alive=None, system=None, response_schema=None):
            calls.append(prompt)
            try:
                raise TimeoutError("timed out")
            except TimeoutError as exc:
                raise ExtractionFailed(f"извлекатель недоступен: {exc}") from exc

        monkeypatch.setattr(module, "_call_ollama", always_timeout)
        with pytest.raises(ExtractionTimedOut):
            extract_nodes_window("Первое. Второе.", domain="personal")
        assert 1 < len(calls) <= 2 * (MAX_SPLIT_DEPTH + 1)


class TestEvidenceGrounding:
    """R4.5.3 (владелец 03.09.2026): «не пытаться добиться безопасности
    только словами в prompt» — каждый пункт ниже соответствует одной из
    четырёх проверок, прямо перечисленных в распоряжении."""

    def test_atom_without_evidence_quote_is_rejected(self) -> None:
        result = validate(payload(atoms=[
            {"local_id": "a1", "kind": "FACT", "title": "т", "text": "т"}],
            edges=[]), window_text=WINDOW_TEXT)

        assert result.atoms == []
        assert any("evidence_quote" in note for note in result.rejected)

    def test_entity_without_evidence_quote_is_rejected(self) -> None:
        result = validate(payload(
            entities=[{"local_id": "e1", "entity_type": "PERSON", "label": "клиника"}],
            edges=[]), window_text=WINDOW_TEXT)

        assert result.entities == []
        assert any("evidence_quote" in note for note in result.rejected)

    def test_edge_without_evidence_quote_is_rejected(self) -> None:
        result = validate(payload(edges=[
            {"from": "a1", "type": "INVOLVES", "to": "e1"}]),
            window_text=WINDOW_TEXT)

        assert result.edges == []
        assert any("evidence_quote" in note for note in result.rejected)

    def test_evidence_quote_not_present_in_window_text_is_rejected(self) -> None:
        """Цитата обязана быть дословной подстрокой окна — придуманная
        (пусть и правдоподобная) цитата не граунд, а тот же произвол,
        только в новом поле."""
        result = validate(payload(atoms=[
            {"local_id": "a1", "kind": "FACT", "title": "т", "text": "т",
             "evidence_quote": "этого не было в тексте окна"}],
            edges=[]), window_text=WINDOW_TEXT)

        assert result.atoms == []
        assert any("не найден в тексте окна" in note for note in result.rejected)

    def test_precise_date_without_absolute_date_in_evidence_is_rejected(self) -> None:
        """precise occurred_at обязан подтверждаться абсолютной датой в
        evidence — иначе точная дата остаётся выдумкой модели, просто с
        цитатой-прикрытием, где даты вообще нет."""
        result = validate(payload(atoms=[
            {"local_id": "a1", "kind": "EVENT", "title": "т", "text": "Приём у Кириченко.",
             "occurred_at": "2026-08-19", "date_precision": "DAY",
             "evidence_quote": "Приём у Кириченко."}],
            edges=[]), window_text=WINDOW_TEXT)

        assert result.atoms == []
        assert any("не подтверждён абсолютной датой" in note for note in result.rejected)

    def test_relative_date_marker_in_evidence_forbids_precise_occurred_at(self) -> None:
        """relative unanchored date → только date_precision=unknown.
        Evidence с «в прошлый вторник» и одновременно occurred_at —
        противоречие, а не находка."""
        window_text = "В прошлый вторник встречались по поводу контракта."
        result = validate(payload(atoms=[
            {"local_id": "a1", "kind": "EVENT", "title": "т", "text": "Встреча по контракту.",
             "occurred_at": "2026-08-25", "date_precision": "DAY",
             "evidence_quote": "В прошлый вторник встречались по поводу контракта."}],
            edges=[]), window_text=window_text)

        assert result.atoms == []
        assert any("относительную дату" in note for note in result.rejected)

    def test_negation_lost_between_evidence_and_atom_text_is_rejected(self) -> None:
        """Отрицание есть в evidence, но потеряно в atom.text — тот же
        класс дефекта, что inverted_negations в R4-бенчмарке, только
        пойманный валидатором, а не пост-фактум метрикой."""
        window_text = "Диагноз не подтверждён по результатам биопсии."
        result = validate(payload(atoms=[
            {"local_id": "a1", "kind": "FACT", "title": "т", "text": "Диагноз подтверждён.",
             "evidence_quote": "Диагноз не подтверждён по результатам биопсии."}],
            edges=[]), window_text=window_text)

        assert result.atoms == []
        assert any("потеряно в тексте атома" in note for note in result.rejected)
