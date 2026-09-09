"""Гейт универсальности: один исполнитель, разные спеки.

`QUERY_LAYER_UNIVERSALITY_2026-09-05.md` §1.5 и §4: «Два существенно
разных немедицинских запроса из разных типов знаний проходят тем же
executor'ом. Без двух новых специализированных роутеров… Копирование
алгоритма под домен — FAIL».

Здесь это проверяется на уровне механизма: НЕмедицинская спека собирается
прямо в тесте, из неё не следует ни одного нового пути в
`query_router.py`, и ответ приходит тем же `_answer_in`, что у врачей.
Живые срезы A/B/C — отдельно и на сервере; этот файл отвечает за то, что
исполнителю нечем узнать, врачебный вопрос перед ним или нет.

База не нужна: сессия поддельная, правила настоящие — тот же приём, что
в `test_knowledge_query_router.py`.
"""

from __future__ import annotations

import uuid

from helm_core.knowledge import query_router as qr
from helm_core.knowledge.semantic_publish import PUBLIC_MODELS

from test_knowledge_query_router import (RUN, SOURCE, TENANT, _FakeSession,
                                         _Identity, _Mention, _Node)


def _employer_proof(span_text: str, label: str) -> tuple[bool, str | None]:
    """Доказывает ли собственная цитата, что это место работы.

    Правило намеренно другое по существу, а не по названию: маркер стоит
    ПЕРЕД подписью, роль берётся из скобок. Если бы исполнитель хоть в
    одном месте полагался на врачебное правило, этот тест бы не прошёл.
    """
    pos = span_text.find(label)
    if pos <= 0:
        return False, None
    before = span_text[:pos].lower()
    if "работал в" not in before:
        return False, None
    start = span_text.find("(", pos)
    end = span_text.find(")", start + 1) if start != -1 else -1
    if start == -1 or end == -1:
        return True, None
    return True, span_text[start + 1:end].strip().lower()


EMPLOYERS = qr.StructuralSpec(
    intent="employers_worked_at",
    subject=qr.SubjectRule(entity_types=("ORGANIZATION", "organization"),
                           counted_as="организации с составом"),
    attribute=qr.AttributeRule(prove=_employer_proof,
                               missing="должность не подтверждена"),
)


def _answer_for(spec, text, node_label):
    node = _Node(node_label)
    identity = _Identity(node_label)
    session = _FakeSession(pairs=[(identity, node)],
                           members=[(identity.id, node.id)],
                           mentions=[_Mention(node.id, 0, len(text))])
    answer = qr.StructuralAnswer(question="где я работал?", intent=spec.intent)
    items, path = qr._answer_in(session, PUBLIC_MODELS, spec=spec, tenant_id=TENANT,
                                run_ids={RUN}, text_by_source={SOURCE: text},
                                answer=answer)
    return items, path, answer


def test_немедицинская_спека_проходит_тем_же_исполнителем():
    text = "Илья работал в ООО «Компас» (руководитель практики) до 2022 года."
    items, path, answer = _answer_for(EMPLOYERS, text, "ООО «Компас»")

    assert len(items) == 1
    assert items[0].subject == "ООО «Компас»"
    assert items[0].attributes == ["руководитель практики"]
    assert items[0].proofs[0].char_start == 0
    assert path == qr.AnswerPath.EVIDENCE


def test_счётчик_называется_словом_из_спеки_а_не_врачебным():
    # «рассмотрено 60» без имени не отличить от «60 чего»; имя приходит
    # из спеки, и врачебное сюда попасть не может.
    text = "Илья работал в ООО «Компас» (руководитель практики)."
    _items, _path, answer = _answer_for(EMPLOYERS, text, "ООО «Компас»")

    assert answer.considered["организации с составом"] == 1
    assert "личности-люди с составом" not in answer.considered


def test_недоказанный_признак_называется_словами_спеки():
    text = "Илья работал в ООО «Компас» до 2022 года."
    items, _path, _answer = _answer_for(EMPLOYERS, text, "ООО «Компас»")

    assert items[0].attributes == []
    assert items[0].line() == "ООО «Компас» — должность не подтверждена"


def test_чужая_цитата_не_даёт_пункта_ответа():
    # Упоминание есть, маркера нет — пункта быть не должно, и причина
    # обязана попасть в счётчики, а не потеряться.
    text = "ООО «Компас» упоминается в списке партнёров."
    items, path, answer = _answer_for(EMPLOYERS, text, "ООО «Компас»")

    assert items == []
    assert path == qr.AnswerPath.EVIDENCE
    assert answer.skipped["в цитате нет подтверждающего маркера"] == 1


def test_врачебная_спека_остаётся_одной_из_спек():
    # Ровно то, что требует §1.4: врачи — не встроенный случай, а запись
    # в том же виде, что и любой другой вопрос.
    assert isinstance(qr.DOCTORS, qr.StructuralSpec)
    assert qr.DOCTORS.attribute.prove is qr.doctor_proof
    assert qr.DOCTORS.edge is not None and qr.DOCTORS.edge.role == "doctor"


def test_у_исполнителя_нет_спеки_по_умолчанию():
    """Спека обязана приходить аргументом.

    Значение по умолчанию означало бы «врачи, пока не сказано иное», то
    есть ту самую вертикаль, ради снятия которой гейт и заведён.
    """
    import inspect

    for name in ("_answer_in", "_graph_items", "_evidence_items"):
        parameter = inspect.signature(getattr(qr, name)).parameters["spec"]
        assert parameter.default is inspect.Parameter.empty, name
