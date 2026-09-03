"""R4 (§14.18) — hard gates и выбор production-модели между кандидатами.

Здесь читается ТОЛЬКО то, что вернул `run_golden_benchmark` (+ внешние
ресурсные факты, измеренные оркестрирующим bash-скриптом на живом
сервере — RAM/CPU/OOM/деградацию HELM не измерить из Python-процесса,
который сам исполняется внутри того же контейнера).

Владелец п.7: hard gate — это НЕ вычитание из среднего балла. Кандидат,
не прошедший хотя бы один gate, не участвует в ранжировании вообще, даже
если по остальным метрикам он лучший — «из плохих не выбирают наименее
плохого» (п.7: «Если ни один кандидат не проходит gates: R4 = NO_PASS»).

Ретракция владельца 02.09.2026, п.6: `peak_rss_mb=None` («не измерено»)
молча превращался в `_ranking_key()` в `0.0` («идеально мало RAM») —
отсутствие измерения выглядело как лучший результат. Это BLOCKER: гейт
`missing_resource_fields()` ниже делает недостающее обязательное
ресурсное измерение причиной дисквалификации, а не нейтральным нулём —
`_ranking_key()` вызывает `AssertionError`, если такой кандидат всё же
до неё дойдёт (по построению `select_winner()` до этого не доводит).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .semantic_benchmark import GoldenBenchmarkReport, ShadowBenchmarkReport


@dataclass
class ResourceStats:
    """Измеряется ВНЕ этого процесса — оркестрирующим скриптом (`docker
    stats`/`free`/OOM-журнал/healthcheck). `None` означает «не измерено
    для этого кандидата», а НЕ «ноль» и НЕ «False» — булевы поля тоже
    `None`-по-умолчанию: `oom_occurred=False`, которого никто не
    проверял, неотличимо от реально проверенного «OOM не было», а это
    ровно то умолчание, которое владелец запретил (п.6)."""

    cold_latency_seconds: float | None = None
    warm_latency_seconds: float | None = None
    total_benchmark_seconds: float | None = None
    peak_rss_mb: float | None = None
    peak_cpu_percent: float | None = None
    swap_before_mb: float | None = None
    swap_peak_mb: float | None = None
    swap_after_mb: float | None = None
    #: None = не проверялось. Только явные True/False считаются измерением.
    oom_occurred: bool | None = None
    other_services_degraded: bool | None = None
    keep_alive_policy: str | None = None
    #: Информационные, не входят в REQUIRED_RESOURCE_FIELDS (п.6: «если
    #: backend честно не даёт prompt_eval/eval — UNAVAILABLE», не gate).
    #: `None` здесь и означает UNAVAILABLE — печатается в отчёте явно,
    #: не заменяется нулём.
    prompt_eval_rate: float | None = None
    generation_rate: float | None = None

    #: R4.5.6.4 (владелец 03.09.2026) — диагностический контекст для
    #: `oom_occurred`, не отдельный gate: сырые before/after cgroup v2
    #: `memory.events oom_kill` счётчики и StartedAt/RestartCount
    #: контейнера. Позволяют человеку перепроверить сам вывод
    #: `oom_occurred`, не входят в REQUIRED_RESOURCE_FIELDS — их
    #: отсутствие само по себе не гейт, гейт уже стоит на oom_occurred=None.
    oom_kill_before: float | None = None
    oom_kill_after: float | None = None
    container_started_before: str | None = None
    container_started_after: str | None = None
    container_restart_count_before: str | None = None
    container_restart_count_after: str | None = None


#: p50/p95 latency сюда намеренно не входят: они считаются ВНУТРИ
#: `GoldenBenchmarkReport` из реальных per-case таймингов самого
#: Python-процесса (`statistics.median` по непустому списку) — в
#: отличие от RSS/CPU/swap/OOM, это не внешнее измерение, которое можно
#: забыть снять, и `None` для них структурно невозможен после
#: состоявшегося прогона.
REQUIRED_RESOURCE_FIELDS: tuple[str, ...] = (
    "cold_latency_seconds", "warm_latency_seconds", "total_benchmark_seconds",
    "peak_rss_mb", "peak_cpu_percent", "swap_before_mb", "swap_peak_mb", "swap_after_mb",
    "oom_occurred", "other_services_degraded", "keep_alive_policy",
)


def missing_resource_fields(resources: ResourceStats) -> list[str]:
    return [f for f in REQUIRED_RESOURCE_FIELDS if getattr(resources, f) is None]


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
    """§14.18 «Hard gates for initial golden set» — нормативный список,
    буквально:

        processed-window coverage                    100%
        unsupported critical facts                   0
        identifier/date corruption on exact fixture   0
        schema-invalid terminal windows               0 after retry
        critical expected entity/event recall         >= 90%
        relation precision on labeled edges           >= 90%

    Владелец 03.09.2026 (R4.5.6): предыдущая версия этой функции
    проверяла `safety_case_hallucinations`/`fabricated_dates`/
    `no_knowledge_violations` по отдельности и вообще не проверяла
    coverage/relation_precision/critical recall/exact corruption —
    drift от спеки, найденный при сверке run 210 (qwen2.5:7b с
    `failed_cases=2` и `relation_precision=0.28` формально «проходил»
    старую версию гейта). `unsupported_critical_facts` — то же самое
    измерение, что раньше называлось `total_material_hallucinations`
    (весь golden набор — куратированные фикстуры, здесь «критично» всё
    выдуманное, не только safety-помеченные кейсы) под именем гейта.
    """
    v: list[str] = []
    m = candidate.golden.metrics
    s = candidate.golden.schema_stats

    missing = missing_resource_fields(candidate.resources)
    if missing:
        v.append(f"обязательные ресурсные измерения отсутствуют: {', '.join(missing)}")
    if candidate.litellm_calls > 0:
        v.append(f"LiteLLM calls = {candidate.litellm_calls} (должно быть 0)")
    if candidate.openrouter_calls > 0:
        v.append(f"OpenRouter calls = {candidate.openrouter_calls} (должно быть 0)")

    if s.failed_cases > 0:
        v.append(f"failed_cases = {s.failed_cases} (processed-window coverage < 100%)")
    if s.truncated_cases > 0:
        v.append(f"truncated_cases = {s.truncated_cases} (processed-window coverage < 100%)")
    if s.malformed_results > 0:
        v.append(f"malformed_results = {s.malformed_results} (schema-invalid terminal window)")
    if s.processed_window_coverage < 1.0:
        v.append(f"processed-window coverage = {s.processed_window_coverage:.1%} (требуется 100%)")
    # Владелец 03.09.2026 (R4.6.B.1 п.2): защита от «идеальная точность
    # через молчание» — явная проверка, НЕ полагающаяся на то, что
    # 0 извлечённых связей уже сегодня даёт relation_precision=0.0, а не
    # 1.0. Кандидат, вообще не пытавшийся извлечь связи там, где gold их
    # ожидает, не проходит гейт по этой причине САМА ПО СЕБЕ.
    if m.relation_gold_scoreable > 0 and m.relation_extracted_total == 0:
        v.append(
            f"relation precision: gold ожидал {m.relation_gold_scoreable} связей, "
            f"извлечено 0 — молчание не засчитывается за точность")
    if m.relation_precision < 0.90:
        v.append(f"relation precision on labeled edges = {m.relation_precision:.1%} (требуется >= 90%)")
    if m.critical_entity_event_recall < 0.90:
        v.append(
            f"critical expected entity/event recall = {m.critical_entity_event_recall:.1%} "
            f"(требуется >= 90%)")
    if m.unsupported_critical_facts > 0:
        v.append(
            f"unsupported critical facts = {m.unsupported_critical_facts} "
            f"(no_knowledge={m.no_knowledge_violations}, fabricated_dates={m.fabricated_dates}, "
            f"fabricated_relations={m.fabricated_relations}, inverted_negations={m.inverted_negations}, "
            f"unsupported_fact_additions={m.unsupported_fact_additions})")
    if m.exact_identifier_corruption > 0:
        v.append(f"identifier corruption on exact fixture = {m.exact_identifier_corruption}")
    if m.exact_date_corruption > 0:
        v.append(f"date corruption on exact fixture = {m.exact_date_corruption}")

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
    missing = missing_resource_fields(candidate.resources)
    assert not missing, (
        f"{candidate.model}: ранжирование вызвано на не полностью измеренном кандидате "
        f"(нет {missing}) — select_winner() обязан был исключить его гейтом раньше, "
        f"а не дать 'нет данных' незаметно превратиться в 0.0"
    )
    m = candidate.golden.metrics
    s = candidate.golden.schema_stats
    stability = candidate.golden.stability
    stability_score = (sum(1 for r in stability if r.reproducible) / len(stability)) if stability else 0.0
    precision = _combined(m.entity_precision, m.atom_precision, m.relation_precision)
    recall = _combined(m.entity_recall, m.atom_recall, m.relation_recall)
    correctness = _combined(m.subtype_accuracy, m.relation_type_accuracy, m.date_accuracy)
    return (
        m.total_material_hallucinations,       # 1. safety/unsupported claims — меньше лучше
        -precision,                            # 2. семантическая точность — больше лучше
        -recall,                               # 3. семантическая полнота — больше лучше
        -correctness,                          # 4. subtype+relation+date correctness — больше лучше
        s.failed_cases, s.avg_repair_attempts,  # 5. надёжность схемы/починок — меньше лучше
        -stability_score,                      # 6. стабильность — больше лучше
        candidate.golden.p50_latency,          # 7. латентность — меньше лучше
        candidate.resources.peak_rss_mb,       # 8. RAM/CPU — меньше лучше (никогда не None здесь)
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
        smaller_by_margin = leader_ram > 0 and (leader_ram - ru_ram) / leader_ram >= SMALLER_RAM_MARGIN
        if close_on_quality and smaller_by_margin:
            winner = runner_up
            reason = (
                f"{runner_up.model} почти не уступает {leader.model} по качеству "
                f"(в пределах {NEAR_TIE_EPSILON}) и заметно легче по RAM "
                f"({ru_ram:.0f} МБ против {leader_ram:.0f} МБ) — R4 п.9"
            )

    return SelectionResult(ranking=ranking, winner=winner, winner_reason=reason,
                           disqualified=disqualified)
