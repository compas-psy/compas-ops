#!/bin/bash
set -euo pipefail
docker exec helm-postgres-1 psql -U helm -d litellm -c '
select model, count(*) as calls, sum(spend) as total_spend
from "LiteLLM_SpendLogs"
group by model
order by total_spend desc;
'
echo ---TOTAL---
docker exec helm-postgres-1 psql -U helm -d litellm -c '
select sum(spend) as total_spend_all_models, count(*) as total_calls
from "LiteLLM_SpendLogs";
'
