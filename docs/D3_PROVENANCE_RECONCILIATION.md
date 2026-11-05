# Frente 3 — proveniência e reconciliação

Este documento registra o contrato mínimo de proveniência. A reconciliação
histórica posterior usa um export local do Strava somente quando o usuário
executa explicitamente `farol.pipeline.strava_backfill --apply`.

## Contrato mínimo

Não há tabela nova de proveniência por célula nesta etapa. A menor estrutura
que responde às perguntas de auditoria é:

| termo | significado |
|---|---|
| `expected_origin` | origem esperada para o campo, informada em cada item do relatório (`imported`, `derived`, `cache` ou `unknown`) |
| `status` | resultado da comparação: `match`, `db_only`, `source_only`, `different`, `derived_difference`, `suspect` ou `unknown` |
| `reconstructible` | se a origem atualmente disponível basta para recalcular o valor sem inventar dados |
| `evidence_level` | `inconclusivo` por padrão; `forte evidência` somente quando há padrão verificável. Não existe atribuição automática de culpa |

Os campos `daily.source` e `activities.source` já são a proveniência persistida
disponível. Eles distinguem `garmin` de `legado`, mas não demonstram que uma
medição individual veio do Garmin: em `daily`, a tag é inferida a partir de
marcadores presentes. Quando não houver evidência suficiente, o relatório usa
`unknown`, em vez de inferir a origem.

`0`, `NULL`, falha e ausência são preservados como estados distintos no
relatório. Por exemplo, `0` contra `0` é `match`; `NULL` contra `NULL` também é
`match`; um valor do banco sem valor de fonte é `db_only` ou `suspect`, nunca é
convertido para zero.

## Mapa atual

| dado | fonte e caminho | derivado? | reproduzível? | risco conhecido |
|---|---|---:|---:|---|
| `daily` — saúde | Garmin (`fetch_day`/`_daily_row`) ou histórico legado → `Daily Health` → `load_into_db` | não | quando a célula existe na planilha | `_db_upsert` usa `COALESCE`: `NULL` de nova carga preserva valor anterior |
| `daily` — sono | Garmin (`_sleep_row`) ou histórico legado → `Sleep` → junção por data em `load_into_db` | não | quando a célula existe na planilha | mesmo `COALESCE`; um dia em uma aba sem a outra deixa campos ausentes |
| `daily.source` | inferido por `_tag_daily_source` a partir de marcadores exclusivos do sync atual | sim | sim, a partir da planilha | é uma inferência de lote, não prova por campo |
| `activities` observadas | Garmin (`fetch_activities`) ou legado → `Activities` → `load_into_db` | não | quando a linha existe na planilha | todos os campos importados usam `COALESCE`; valor antigo pode sobreviver a `NULL` posterior |
| `activities.trimp` | `duration_min`, `avg_hr`, `daily.rhr`, `profile.hr_max` → `training_load.trimp` | sim | sim, se os quatro inputs existirem | falta de FC/RHR devolve `0.0`, que significa “não calculável”, não carga fisiológica comprovadamente zero |
| `activities.splits` | resumo de typed splits do Garmin, gravado por `sync_activity_splits` | cache/derivado | não pela planilha atual | a planilha guarda laps brutos com semântica diferente; `"null"` conflui vazio e erro |
| `activities.source` | `activity_id > 0` → `garmin`; id negativo → `legado` | sim | sim | distância/pace legados sem `historical_source=strava_export` são anulados; o load limpa também qualquer valor anterior retido por `COALESCE` |
| `activities.strava_activity_id` e `historical_source` | comparação por data + duração com CSV local do Strava | não | sim, quando o export existe | vínculo por atividade; não modifica `source` nem baselines |
| `activities.running_dynamics_source` | FIT original dentro do export local | não | sim, quando o FIT existe | medianas por registro em movimento no legado; resumos Garmin atuais têm agregação distinta |
| `activities.historical_only` | 1 quando uma corrida existe apenas no export retrospectivo | sim | sim, pelo ID da atividade no export | mantém distância/biomecânica no histórico, com `trimp=NULL` para preservar a classificação diária calibrada |

Na limpeza de atividades duplicadas durante `load_into_db`, o mesmo dia não é
mais evidência suficiente para remover uma linha legada. A rotina exige duração
próxima e FC média compatível (ou tipo igual quando falta FC), e preserva linhas
`historical_only=1`. Isto mantém sessões independentes feitas no mesmo dia.
| `hrv` | Garmin (`_hrv_row`) → `HRV` → `load_into_db` | não | quando a linha existe na planilha | também usa `COALESCE`; `raw` é payload cacheado, não medição normalizada |
| `training_load` | soma de `activities.trimp` por dia → Banister CTL/ATL/TSB | sim | sim | fórmula e arredondamentos são recalculados pela mesma rotina pura; não foram alterados |
| `farol_daily_state` | `daily`, `activities`, `training_load`, regularidade de sono e análises → `build_daily_state` | cache/materialização | sim, em clone de memória | se o pipeline não roda para uma data, o cache antigo pode sobreviver; as classificações continuam derivadas da materialização, não são fonte primária |

### Upsert e `NULL`

`sheets._db_upsert` aplica `COALESCE(excluded.col, col)` a todas as colunas
importadas de `daily`, `activities` e `hrv`. Portanto, para elas:

- `NULL` da planilha significa “não observado nesta carga; preserve o que já
  havia”, não “apague o valor”.
- A exceção é `daily.errors`: ausência explícita após sync limpa o erro antigo,
  pois ele descreve o estado da última coleta, não uma medida fisiológica.
- `training_load` e `farol_daily_state` atualizam os campos derivados sem
  `COALESCE` quando são recalculados.

As fixtures atuais de estado usam banco em memória. Ainda assim, qualquer
caminho que abra `db.connect()` sem um caminho de teste aponta por padrão para
o banco pessoal; essa é a superfície que a conexão read-only e os hashes de
teste passam a proteger.

## Reconciliação read-only

`python -m farol.pipeline.reconcile` abre o SQLite com `mode=ro`; não chama
`ensure_schema`, não faz migration e não altera PRAGMAs. Ela lê a planilha em
modo somente leitura e produz JSON estruturado. Para verificar derivados:

- calcula `training_load` em memória com a mesma fórmula do projeto;
- clona o banco read-only para `:memory:` e reconstrói apenas
  `farol_daily_state` no clone;
- compara `daily`, `activities`, `hrv` com as abas de origem;
- sinaliza `splits` como `unknown` quando houver payload, pois o formato da
  planilha não é equivalente ao resumo cacheado no banco.

O padrão suspeito é estrito: campo ausente na planilha **e** valor igual a uma
constante de `TestBuildReport` (`hrv_avg=18`, `stress_avg=45`,
`body_battery_max=30`, `sleep_score=55`). Isso recebe `suspect` com “forte
evidência”, não “confirmado”: os mesmos números podem ocorrer em uma observação
real. A ferramenta não apaga nem atualiza esses valores.

## Splits: estado proposto

Estado atual:

1. a consulta trata SQL `NULL` como pendente;
2. `fetch_typed_splits()` captura qualquer exceção e retorna `[]`;
3. `summarize_work_segments([])` retorna `None`;
4. `json.dumps(None)` grava o texto JSON `"null"`;
5. a próxima consulta só procura SQL `NULL`.

Assim, “erro de coleta” e “resposta válida sem `INTERVAL_ACTIVE`” se tornam o
mesmo valor e não são tentados novamente. A migração segura proposta é uma
coluna `activities.splits_status` com estes estados:

| estado | significado | retry |
|---|---|---:|
| `pending` | ainda não coletado | sim |
| `success` | resposta coletada e resumo de trabalho válido preservado | não |
| `empty` | resposta Garmin válida, mas sem segmentos aplicáveis | não |
| `error` | tentativa falhou, com motivo/horário se houver metadados | sim |
| `unknown` | histórico ambíguo, inclusive `"null"` legado | sim, sob ação explícita futura |

Backfill não é seguro nesta etapa. Quando for autorizado, SQL `NULL` pode virar
`pending`; JSON objeto válido de resumo pode virar `success`; `"null"` e JSON
malformado devem virar `unknown`, nunca `error`; `empty` só deve ser atribuído
depois de uma coleta que confirmou resposta válida vazia. A implementação deve
separar o resultado da chamada Garmin (sucesso/erro) do resultado semântico do
resumo (success/empty).
