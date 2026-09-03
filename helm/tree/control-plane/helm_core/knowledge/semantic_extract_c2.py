"""R4.6.C2 (владелец 03.09.2026) — deterministic candidate generation +
relation-or-NONE classifier. Собирает воедино `relation_candidates.py`
(детерминированный, без LLM, порождает пары по доказуемой близости
текста) и `relation_classifier.py` (для каждой уже данной пары модель
возвращает NONE либо один типизированный relation, не может предложить
пару сама).

Не замена production-пути: `extract_window()` (`semantic_extract.py`)
остаётся единственным путём, которым пользуется всё остальное. Эта
функция существует только для того, чтобы R4.6 сравнил её метрики со
single-pass (R4.6.B) и two-pass (R4.6.C) на golden-наборе.

pass 1 здесь — тот же `extract_window()`, что и у single/two-pass, его
`edges` отбрасываются: право решать связи не у него ни в одной из
архитектур R4.6."""

from __future__ import annotations

from . import semantic_extract
from .relation_candidates import generate_candidates
from .relation_classifier import classify_relation
from .semantic_extract import DEFAULT_MODEL, MAX_REPAIR_ATTEMPTS, WindowExtraction


def extract_window_c2(window_text: str, *, domain: str, heading_path: tuple[str, ...] = (),
                      model: str = DEFAULT_MODEL, keep_alive: str | None = None,
                      attempts: int = MAX_REPAIR_ATTEMPTS) -> WindowExtraction:
    """R4.6.C2 эксперимент — см. docstring модуля. `WindowTruncated` из
    pass 1 не ловится, распространяется как есть (та же семантика, что
    у `extract_window_two_pass()`)."""
    pass1 = semantic_extract.extract_window(window_text, domain=domain, heading_path=heading_path,
                                            model=model, keep_alive=keep_alive, attempts=attempts)

    objects_by_id = {e.local_id: e for e in pass1.entities}
    objects_by_id.update({a.local_id: a for a in pass1.atoms})

    candidates = generate_candidates(pass1.entities, pass1.atoms, window_text)

    edges = []
    rejected = list(pass1.rejected)
    for candidate in candidates:
        edge, reason = classify_relation(
            candidate, from_obj=objects_by_id[candidate.from_id], to_obj=objects_by_id[candidate.to_id],
            model=model, keep_alive=keep_alive)
        if edge is not None:
            edges.append(edge)
        elif reason is not None:
            rejected.append(reason)

    return WindowExtraction(entities=pass1.entities, atoms=pass1.atoms, edges=edges, rejected=rejected)
