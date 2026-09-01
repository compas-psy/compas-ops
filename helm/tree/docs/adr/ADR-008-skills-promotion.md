# ADR-008. Skills promotion

**Дата:** 31.08.2026 · **Статус:** решён гейт (policy-уровень), не
реализован исполнитель — черновиков навыков (skills) физически нет,
`skills/`/`skills-candidates/` пусты.

Не путать с `charter/11_SKILLS.md` — это про мета-навыки самих
compas-ops-агентов (superpowers, graphify и т.п.), другая тема. Здесь —
про продуктовые навыки chief-агента HELM.

## Контекст

§34 «Skills promotion» — как черновик навыка становится продакшен-
навыком, и какой гейт стоит между ними, чтобы владелец не узнавал о
новом поведении chief-агента постфактум.

## Решение

**Два уровня доверия на двух разных действиях, тот же движок action
registry, что и у всего остального (ADR-009).**

`config/policies/actions.yaml`:

- `write_candidate_skill` — `initial_level: YELLOW`,
  `minimum_allowed_level: YELLOW` («Черновик нового skill») — писать
  черновик разрешено без RED-одобрения на каждый черновик, но не ниже
  YELLOW.
- `skill_promote` — `initial_level: RED`, `minimum_allowed_level:
  YELLOW`, `approval_ttl: 24h`, `required_preconditions:
  [diff_reviewed]` («Ввести skill в продакшен») — ввод в продакшен
  требует RED-одобрения владельца И явно пройденного `diff_reviewed`,
  не одного из двух.

Механика precondition/RED-гейта, которая это исполняет — общая, уже
реализованная и протестированная инфраструктура (`actions/registry.py`,
`actions/policy.py`, ADR-009), не написана заново под этот случай —
skill promotion получает те же гарантии (идемпотентность, TTL
approval, невозможность исполнить без прохождения precondition), что и
любое другое RED-действие.

## Не в объёме этого захода

**Исполнитель не зарегистрирован.** Ни `write_candidate_skill`, ни
`skill_promote` не имеют `@registry.action(...)` нигде в кодовой базе
(в отличие от `notify_owner`/`kanban_snapshot`, которые зарегистрированы)
— попытка вызвать любое из двух действий сегодня упала бы на
`registry.get()`. `skills/` и `skills-candidates/` — пустые каталоги,
формат файла черновика не определён. UI очереди-кандидатов/diff-view/
кнопки «Promote» существует только как макет в design-source Panel
(`.dc.html`), не в `api/panel.py` — ни строки "skill"/"candidate" в
реальном роутере нет.

Решён гейт, которым это будет управляться, когда появится сама фича —
не сама фича.
