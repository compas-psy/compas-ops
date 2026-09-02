"""v4.0 §14.4.2/§14.4.3 — контракт вывода извлекателя и его проверка.

Проверяется НЕ качество извлечения: его меряет R4 отдельным бенчмарком
(§14.18), и §14.23 прямо называет нарушением выдавать сотни юнит-тестов
с поддельной моделью за доказательство качества. Здесь доказывается
другое — что мусор не попадает в граф молча, а обязательные поля
обязательны.

Каждый случай ниже взят из живых прогонов 02.09.2026: модель этого
класса возвращает один объект вместо массива, путает регистр, ссылается
на несуществующие local_id и изобретает типы связей.
"""

import json

import pytest

from helm_core.knowledge.semantic_extract import (
    MAX_ATOMS_PER_WINDOW, ExtractionFailed, WindowTruncated, extract_window, validate,
)


def payload(**overrides) -> str:
    data = {
        "entities": [
            {"local_id": "e1", "entity_type": "PERSON", "subtype": "doctor",
             "label": "Кириченко Сергей Александрович", "aliases": ["Кириченко С.А."]},
            {"local_id": "e2", "entity_type": "CONCEPT", "subtype": "medical_specialty",
             "label": "уролог", "aliases": []},
        ],
        "atoms": [
            {"local_id": "a1", "kind": "EVENT", "subtype": "medical_visit",
             "title": "Приём уролога", "text": "Приём у Кириченко.",
             "occurred_at": "2026-08-19", "date_precision": "DAY"},
        ],
        "edges": [
            {"from": "a1", "type": "INVOLVES", "to": "e1", "role": "doctor"},
            {"from": "e1", "type": "HAS_ROLE", "to": "e2"},
        ],
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def test_normative_example_from_the_spec_is_accepted() -> None:
    """Форма из §14.4.2 — не «пример красивого JSON», а контракт."""
    result = validate(payload())

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
    result = validate(payload(edges=[{"from": "a1", "type": "ЛЕЧИЛ", "to": "e1"}]))

    assert [e.relation_type for e in result.edges] == ["related_to"]
    assert any("related_to" in note for note in result.rejected)


def test_edge_into_nowhere_is_dropped_and_counted() -> None:
    """Ребро на несуществующий local_id — не связь, а опечатка модели.
    Оно отбрасывается, но счётчик отброшенного растёт: молчаливое
    выбрасывание неотличимо от «модель ничего не нашла»."""
    result = validate(payload(edges=[{"from": "a1", "type": "ABOUT", "to": "нет-такого"}]))

    assert result.edges == []
    assert result.rejected == ["связь в никуда: 'a1' → 'нет-такого'"]


def test_atom_kind_outside_the_registry_is_rejected() -> None:
    result = validate(payload(atoms=[
        {"local_id": "a1", "kind": "ЗАМЕТКА", "title": "т", "text": "т"}]))

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
        entities=[{"local_id": "e1", "entity_type": "ORGANIZATION", "label": "клиника"}],
        edges=[]))

    assert result.entities[0].entity_type == "ORGANIZATION"
    assert result.rejected == []


def test_missing_required_field_drops_the_record_not_the_answer() -> None:
    """Та же дисциплина, что у разбора frontmatter: запись без
    обязательного поля пропускается целиком, а не додумывается."""
    result = validate(payload(atoms=[
        {"local_id": "a1", "kind": "EVENT", "title": "", "text": "есть"},
        {"local_id": "a2", "kind": "FACT", "title": "есть", "text": "есть"},
    ], edges=[]))

    assert [a.local_id for a in result.atoms] == ["a2"]
    assert len(result.rejected) == 1


def test_duplicate_local_id_is_rejected() -> None:
    result = validate(payload(entities=[
        {"local_id": "e1", "entity_type": "PERSON", "label": "первый"},
        {"local_id": "e1", "entity_type": "PERSON", "label": "второй"},
    ]))

    assert [e.label for e in result.entities] == ["первый"]
    assert any("повтор local_id" in note for note in result.rejected)


def test_unknown_date_precision_keeps_the_atom_and_marks_unknown() -> None:
    """§14.8 запрещает выдумывать точность даты, а не хранить дату.
    Атом остаётся, точность становится «неизвестна»."""
    result = validate(payload(atoms=[
        {"local_id": "a1", "kind": "FACT", "title": "т", "text": "т",
         "occurred_at": "2026-08-19", "date_precision": "ПРИМЕРНО"}]))

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
        validate(raw)
    assert reason in str(err.value)


def test_window_at_the_cap_raises_instead_of_truncating() -> None:
    """§14.4.1: упёрлись в потолок — окно делится, а не обрезается.
    Именно здесь semantic-v1 делал `data[:MAX_ATOMS_PER_CALL]`."""
    atoms = [{"local_id": f"a{i}", "kind": "FACT", "title": f"т{i}", "text": "т"}
             for i in range(MAX_ATOMS_PER_WINDOW + 5)]

    with pytest.raises(WindowTruncated):
        validate(payload(atoms=atoms, edges=[]))


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
