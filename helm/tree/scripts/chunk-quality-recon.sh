#!/bin/bash
# HELM · почему «ближайшее из ваших записей» — заголовок, а не ответ.
#
# action=recon: только SELECT.
#
# Владелец 05.09.2026 прислал два живых ответа бота и назвал их
# бредовыми. Оба — ветка Z1 «Не нашёл прямого ответа. Ближайшее из ваших
# записей», и оба вернули не фрагмент документа, а его ЗАГОЛОВОК
# («ОСМОТР ГАСТРОЭНТЕРОЛОГА») и ПОЛЕ БЛАНКА с пустым значением
# («Врач: Фамилия Имя Отчество ______»).
#
# Гипотеза, которую этот замер проверяет: виноват не ранжировщик, а
# единица поиска. split_chunks() (ingest.py:59) режет текст по пустой
# строке. У распарсенного PDF пустая строка стоит после каждого
# заголовка, каждой строки бланка и каждого лабораторного значения —
# значит «чанк» это не абзац, а строка формы. Дальше
# ts_rank(normalization=2) ДЕЛИТ ранг на длину, то есть системно
# поднимает наверх самые короткие огрызки; порог
# MIN_LEXICAL_CHUNK_CHARS=20 отсекал «Врач КДЛ:» (9 символов), но
# «ОСМОТР ГАСТРОЭНТЕРОЛОГА» — 23 символа и проходит.
#
# Замер должен ответить числами: какова доля вырожденных чанков и что
# именно видит probe на РЕАЛЬНОМ вопросе владельца.
#
# Вопрос берётся дословно из его переписки: «что там прописал врач?».
# Второй вопрос из скриншота неизвестен (виден только ответ) — не
# додумываю, беру один достоверный.
#
# ЦИТАТЫ И ИМЕНА ФАЙЛОВ ПЕЧАТАЮТСЯ — решения владельца 05.09.2026.
set -uo pipefail
cd /opt/helm/compose || exit 1

Q='что там прописал врач'

psql() { sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc "$1" 2>&1; }

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo "############ 1. РАЗМЕР ЧАНКА — ЕДИНИЦА ПОИСКА ############"
echo "корзина | чанков"
psql "select bucket||' | '||count(*)::text from (
        select case
          when length(text) < 20  then 'a. менее 20 символов (отсекается порогом)'
          when length(text) < 40  then 'b. 20-39   (заголовок, строка бланка)'
          when length(text) < 80  then 'c. 40-79'
          when length(text) < 200 then 'd. 80-199'
          else                         'e. 200 и больше (похоже на абзац)'
        end as bucket from health.knowledge_chunks) t
      group by bucket order by bucket"
echo "всего | средняя длина | медиана | самый длинный:"
psql "select count(*)::text||' | '||round(avg(length(text)))::text||' | '||
             percentile_disc(0.5) within group (order by length(text))::text||' | '||
             max(length(text))::text
      from health.knowledge_chunks"

echo "############ 2. ЧТО ЭТО ЗА ЧАНКИ ############"
echo "всего | без конечной пунктуации и короче 120 | с прочерком-заполнителем | целиком заглавными:"
psql "select count(*)::text||' | '||
             (count(*) filter (where text !~ '[.!?]' and length(text) < 120))::text||' | '||
             (count(*) filter (where text ~ '_{3,}'))::text||' | '||
             (count(*) filter (where text = upper(text) and text ~ '[А-ЯA-Z]'))::text
      from health.knowledge_chunks"

echo "############ 3. ЧТО ВИДИТ PROBE НА РЕАЛЬНОМ ВОПРОСЕ ############"
echo "вопрос владельца: «${Q}?»"
echo "ранг | длина | начало чанка   (порядок и фильтр — как в проде)"
psql "select round(ts_rank(tsv, plainto_tsquery('russian','${Q}'), 2)::numeric, 5)::text||' | '||
             length(text)::text||' | '||left(regexp_replace(text,'\s+',' ','g'), 110)
      from health.knowledge_chunks
      where tsv @@ plainto_tsquery('russian','${Q}')
        and length(text) >= 20
      order by ts_rank(tsv, plainto_tsquery('russian','${Q}'), 2) desc limit 5"
echo "— и что порог 20 символов отсекает по этому же вопросу:"
psql "select round(ts_rank(tsv, plainto_tsquery('russian','${Q}'), 2)::numeric, 5)::text||' | '||
             length(text)::text||' | '||left(regexp_replace(text,'\s+',' ','g'), 110)
      from health.knowledge_chunks
      where tsv @@ plainto_tsquery('russian','${Q}')
        and length(text) < 20
      order by ts_rank(tsv, plainto_tsquery('russian','${Q}'), 2) desc limit 5"

echo "############ 4. САМЫЕ ВЫСОКОРАНГОВЫЕ ЧАНКИ ВООБЩЕ ############"
echo "длина | чанк — десять коротких, которые ранжировщик поднимает первыми:"
psql "select length(text)::text||' | '||left(regexp_replace(text,'\s+',' ','g'), 110)
      from health.knowledge_chunks
      where length(text) between 20 and 45 order by random() limit 10"

echo "############ ГОТОВО ############"
