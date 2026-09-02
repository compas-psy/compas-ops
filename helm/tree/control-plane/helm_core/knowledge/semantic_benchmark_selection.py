"""R4 (§14.18) — hard gates и выбор production-модели между кандидатами.

Здесь читается ТОЛЬКО то, что вернул `run_golden_benchmark` (+ внешние
ресурсные факты, измеренные оркестрирующим bash-скриптом на живом
сервере — RAM/CPU/OOM/деградацию HELM не измерить из Python-процесса,
который сам исполняется внутри того же контейнера).

Владелец п.7: hard gate — это НЕ вычитание из среднего балла. Кандидат,
не прошедший хотя бы один gate, не участвует в ранжировании вообще, даже
если по остальным метрикам он лучший — «из плохих не выбирают наименее
плохого» (п.7: «Если ни один кандидат не проходит gates: R4 = NO_PASS»).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .semantic_benchmark import GoldenBenchmarkReport, ShadowBenchmarkReport


@dataclass
class ResourceStats:
    """Измеряется ВНЕ этого процесса — оркестрирующим скриптом (`docker
    stats`/`free`/OOM-журнал). `None` означает «не измерено для этого
    кандидата», а не «ноль» — гейт на отсутствующих данных не молчит,
    отсутствие видно прямо в отчёте, а не тонет в нуле."""

    cold_latency_seconds: float | None = None
    warm_latency_seconds: float | None = None
    peak_rss_mb: float | None = None
    oom_occurred: bool = False
    other_services_degraded: bool = False
    keep_alive_policy: str | None = None


@dataclass
class CandidateResult:
    model: str
    quant_tag: str
    golden: GoldenBenchmarkReport
    resources: ResourceStats
    shadow: ShadowBenchmarkReport | None = None
    litellm_calls: int = 0
    openrouter_calls: int = 0


@dataclass
class GateResult:
    passed: bool
    violations: list[str] = field(default_factory=list)


def evaluate_hard_gates(candidate: CandidateResult) -> GateResult:
    v: list[str] = []
    m = candidate.golden.metrics
    s = candidate.golden.schema_stats

    if candidate.litellm_calls > 0:
        v.append(f"LiteLLM calls = {candidate.litellm_calls} (должно быть 0)")
    if candidate.openrouter_calls > 0:
        v.append(f"OpenRouter calls = {candidate.openrouter_calls} (должно быть 0)")
    if m.safety_case_hallucinations > 0:
        v.append(f"material hallucination на safety-кейсах golden: {m.safety_case_hallucinations}")
    if m.fabricated_dates > 0:
        v.append(f"выдуманная точная дата: {m.fabricated_dates}")
    if m.no_knowledge_violations > 0:
        v.append(f"NO_KNOWLEDGE придумал знание: {m.no_knowledge_violations} случаев")
    if s.malformed_results > 0:
        v.append(f"validate()/repair пропустил невалидный результат: {s.malformed_results}")
    if candidate.resources.oom_occurred:
        v.append("OOM на этом кандидате")
    if candidate.resources.other_services_degraded:
        v.append("живой HELM (postgres/telegram/...) деградировал во время замера")

    return GateResult(passed=not v, violations=v)


#: Владелец п.9: «если маленькая почти не уступает — предпочесть меньшую
#: по RAM/latency». Это ТОЛЬКО про «почти неотличимые по качеству» —
#: строгий лексикографический порядок (п.8) эпсилон не заменяет и не
#: усредняет, он лишь решает между двумя кандидатами, которые порядок
#: и так поставил рядом.
NEAR_TIE_EPSILON = 0.02
SMALLER_RAM_MARGIN = 0.20


def _combined(*values: float) -> float:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else 0.0


def _ranking_key(candidate: CandidateResult) -> tuple:
    m = candidate.golden.metrics
    s = candidate.golden.schema_stats
    stability = candidate.golden.stability
    stability_score = (sum(1 for r in stability if r.reproducible) / len(stability)) if stability else 0.0
    precision = _combined(m.entity_precision, m.atom_precision, m.relation_precision)
    recall = _combined(m.entity_recall, m.atom_recall, m.relation_recall)
    correctness = _combined(m.subtype_accuracy, m.relation_type_accuracy, m.date_accuracy)
    peak_rss = candidate.resources.peak_rss_mb if candidate.resources.peak_rss_mb is not None else 0.0
    return (
        m.total_material_hallucinations,       # 1. safety/unsupported claims — меньше лучше
        -precision,                            # 2. семантическая точность — больше лучше
        -recall,                               # 3. семантическая полнота — больше лучше
        -correctness,                          # 4. subtype+relation+date correctness — больше лучше
        s.failed_cases, s.avg_repair_attempts,  # 5. надёжность схемы/починок — меньше лучше
        -stability_score,                      # 6. стабильность — больше лучше
        candidate.golden.p50_latency,          # 7. латентность — меньше лучше
        peak_rss,                              # 8. RAM/CPU — меньше лучше
    )


@dataclass
class SelectionResult:
    ranking: list[CandidateResult]      # строго по _ranking_key, лучший первый
    winner: CandidateResult | None
    winner_reason: str
    disqualified: dict[str, GateResult]  # model -> почему не участвовал в ранжировании


def select_winner(candidates: list[CandidateResult]) -> SelectionResult:
    disqualified: dict[str, GateResult] = {}
    passing: list[CandidateResult] = []
    for c in candidates:
        gate = evaluate_hard_gates(c)
        if gate.passed:
            passing.append(c)
        else:
            disqualified[c.model] = gate

    if not passing:
        return SelectionResult(
            ranking=[], winner=None,
            winner_reason="R4 = NO_PASS: ни один кандидат не прошёл hard gates",
            disqualified=disqualified)

    ranking = sorted(passing, key=_ranking_key)
    leader = ranking[0]
    winner, reason = leader, "первый в строгом лексикографическом порядке (R4 п.8)"

    if len(ranking) > 1:
        runner_up = ranking[1]
        leader_key, ru_key = _ranking_key(leader), _ranking_key(runner_up)
        # Первые 4 компоненты (safety/precision/recall/correctness) — в
        # пределах эпсилон; 5-я и 6-я (надёжность схемы) — точное
        # совпадение, это уже не «немного», а «так же».
        close_on_quality = (
            leader_key[0] == ru_key[0]
            and all(abs(leader_key[i] - ru_key[i]) <= NEAR_TIE_EPSILON for i in (1, 2, 3))
            and leader_key[4] == ru_key[4] and leader_key[5] == ru_key[5]
        )
        leader_ram, ru_ram = leader.resources.peak_rss_mb, runner_up.resources.peak_rss_mb
        smaller_by_margin = (
            leader_ram is not None and ru_ram is not None and leader_ram > 0
            and (leader_ram - ru_ram) / leader_ram >= SMALLER_RAM_MARGIN
        )
        if close_on_quality and smaller_by_margin:
            winner = runner_up
            reason = (
                f"{runner_up.model} почти не уступает {leader.model} по качеству "
                f"(в пределах {NEAR_TIE_EPSILON}) и заметно легче по RAM "
                f"({ru_ram:.0f} МБ против {leader_ram:.0f} МБ) — R4 п.9"
            )

    return SelectionResult(ranking=ranking, winner=winner, winner_reason=reason,
                           disqualified=disqualified)
