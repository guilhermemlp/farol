# Farol

Relatório diário do seu estado de recuperação e carga de treino, a partir dos
seus dados do Garmin. Sem servidor, sem fila, sem múltiplos bancos.

**O farol não prescreve treino.** Ele descreve o que aconteceu e como você está
em relação ao seu próprio padrão. O que fazer com isso é seu e do seu treinador
(docs/PROFILE.md, regras 7 e 9).

Fluxo: **Garmin → extração → planilha (`data/garmin_health.xlsx`) → banco (SQLite)**.
A planilha é a fonte de verdade e reaproveita as abas do histórico do
`analise-garmin`; o banco é sempre recarregado a partir dela, então uma correção
manual numa célula entra no banco na sincronização seguinte.

## Setup (uma vez)

```bash
cd farol
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env
```

Edite `.env` com seu email/senha do Garmin (só é lido na primeira sincronização
— depois a sessão fica salva em `.garmin_token/`). `data/profile.yaml` já vem
preenchido com dados reais; ver [docs/PROFILE.md](docs/PROFILE.md) para as fontes.

`.env` também aceita `FAROL_TZ_OFFSET_HOURS` (default `-3`, sem horário de verão
desde 2019 no Brasil) — só importa se você não estiver nesse fuso; ajusta como
`sleep_start`/`sleep_end` (epoch UTC) viram horário local para a "Regularidade" do
sono em `farol.today`.

## Uso

Para uma primeira leitura dos estados, tendências, convergência e previsão de
prova, consulte o [guia de leitura e capacidades](docs/CAPACIDADES_FAROL.md).
Os relatórios descrevem dados frente ao histórico individual; não são
diagnóstico nem prescrição de treino.

```bash
# sincroniza os últimos N dias: Garmin -> planilha -> banco -> carga de treino
python -m farol.ingest --days 7

# imprime o estado de hoje
python -m farol.today

# recarrega o banco a partir da planilha, sem tocar no Garmin
python -m farol.load

# só os tiros de um treino intervalado (ignora aquecimento e recuperação)
python -m farol.splits
python -m farol.splits --activity-id 24208281560

# decoupling aerobico (Pa:Hr) de uma corrida continua: 1a vs 2a metade da MESMA sessao
python -m farol.decoupling
python -m farol.decoupling --activity-id 24070320249

# resumo de 1 página do estado do atleta (farol_daily_state -> reports/DAILY_STATE.md)
python -m farol.state
python -m farol.state --date 2026-09-02 --out /tmp/estado.md

# calibração automática Farol x Training Readiness Garmin
# (também é regenerada ao final de farol.ingest)
python -m farol.calibration_auto

# correlacao defasada entre dominios (ex.: estresse hoje x sono amanha)
python -m farol.correlations

# revisão opcional do log subjetivo legado
python -m farol.calibration_review

# candidata a nova quebra de medicao (tipo a de rhr/awakenings), por campo de `daily`
python -m farol.changepoint

# predicao de prova: estimativa da meia (21.1 km) a partir de provas recentes
# (Riegel/Cameron/VDOT) -> reports/RACE_PREDICTION.md
python -m farol.race_prediction

# dashboard HTML de 1 pagina (recuperacao, sono, carga, previsao de prova,
# correlacoes/quebras de medicao) -> reports/DASHBOARD.html, abre no navegador
python -m farol.dashboard
python -m farol.dashboard --date 2026-09-02 --out /tmp/dashboard.html
```

Rode `farol.ingest` uma vez por dia e depois `farol.today`. O histórico que já
está em `data/garmin_health.xlsx` entra no banco automaticamente — não existe passo
de backfill separado.

`farol.load` serve para quando você editou a planilha à mão, ou quando quer
reconstruir o banco do zero (`rm data/farol.db && python -m farol.load`).

## O que o relatório mostra

```
RESUMO                índice de recuperação, dias até a prova, alertas
TREINO REALIZADO      só se houve treino: dados, tiros, CV do pace, deriva de FC
CARGA DE TREINO       CTL/ATL/TSB, VO2max (Garmin, só tendência) + 8 semanas com monotonia
EFICIENCIA AEROBICA   FC a um pace de referência fixo, primeira vs segunda metade
RECUPERACAO           HRV (+ CV de 7d), FC repouso, FC noturna, respiração, SpO2, Body Battery
SONO                  score, duração, estágios, fragmentação, regularidade do horário
PADROES DETECTADOS    convergência de 2+ indicadores, incl. padrão compatível com doença
                       vs. fadiga de treino (docs/PROFILE.md, regra 8)
```

Cada métrica aparece assim, com um sparkline dos últimos 30 dias nas linhas principais:

```
HRV   18 ms ▄▄▁▁▄▇▆▂▄▃▄▂▄▆▅▅▄▂▇▄▃▂▅█▅▄▂▃▄▃ | normal 18.67 ms | z -0.46 | 7d = | ok
```

O "normal" é calculado do seu próprio histórico, em janela móvel de 90 dias.
Nenhum limiar fisiológico está escrito no código do relatório.

## Estrutura

```
farol/
├── docs/           # explicativo, escrito à mão (PROFILE, MODEL, CALIBRACAO)
├── reports/        # gerado pelos comandos abaixo (DAILY_STATE.md etc.)
├── data/           # farol.db, garmin_health.xlsx, profile.yaml (gitignorados,
│                   # exceto garmin_health.xlsx, a fonte de verdade versionada)
├── farol/          # o pacote (inclui schema.sql)
├── tests/
└── pyproject.toml, README.md
```

- `farol/schema.sql` — o banco espelha a planilha: toda coluna extraída do
  Garmin tem coluna aqui. Tabelas: `daily`, `activities`, `hrv`, `training_load`.
- `farol/db.py` — só conexão e schema. Não conhece regra de negócio nenhuma.
- `farol/daily_state_builder.py` — popula `farol_daily_state` combinando
  `db` (leitura das tabelas) com `analysis` (avaliação de performance);
  fica fora de `db.py` de propósito, para a camada de persistência não
  depender da camada de domínio.
- `farol/garmin_client.py` — login e extração.
- `farol/sheets.py` — planilha como etapa intermediária; escreve o que foi
  extraído e recarrega o banco a partir dela.
- `farol/metrics.py` — a primitiva `Metric`: todo valor sai com médias móveis,
  desvio, z-score, tendência e confiança. É o que substituiu os limiares fixos.
- `farol/snapshot.py` — retrato diário de `Metric` compartilhado por
  `farol.today` e `farol.dashboard`: mesma lista de campos, mesma escolha de
  tabela (TSB em `training_load`, RHR em `daily`) e nenhum cálculo próprio.
- `farol/analysis.py` — carga semanal com monotonia de Foster, e eficiência
  de corrida (indicador a pace fixo), em duas granularidades:
  - corrida contínua (longão, rodagem, tempo run) — `aerobic_efficiency` (FC)
    e `continuous_mechanics` (cadência/tempo de contato/oscilação vertical),
    usando a atividade inteira como ponto; o Garmin já exclui tempo parado
    por auto-pause, então isso é sempre tempo em corrida, nunca descanso.
  - tiro de treino intervalado — `interval_efficiency` (FC) e
    `interval_mechanics` (idem mecânico), usando cada tiro individualmente
    (`activities.splits.reps`, nunca a recuperação ou caminhada entre eles).
    Mais denso na faixa de esforço forte, onde a média da corrida inteira
    dilui aquecimento e recuperação junto com o esforço.

  As quatro funções mecânicas/cardíacas compartilham o mesmo núcleo
  (`_split_and_fit`): ajustam indicador ~ pace na primeira e na segunda
  metade cronológica do histórico e comparam a previsão num pace de
  referência fixo — nunca o valor cru, que melhora sozinho quando você
  simplesmente corre mais rápido.
- `farol/training_load.py` — TRIMP e CTL/ATL/TSB (Banister, local).
- `farol/zones.py` — zonas de FC ancoradas em LTHR, não em percentual genérico
  de FC máxima. Usado para rotular a FC do treino no relatório.
- `farol/recovery.py` — o índice único de recuperação (`recovery_index`,
  `recovery_label`, pesos padrão): compartilhado por `today.py` (relatório do
  dia) e `calibration.py` (log de calibração), daí viver fora dos dois.
- `farol/ingest.py`, `today.py`, `load.py`, `splits.py`, `state.py`,
  `calibration.py`, `daily_analysis.py` — os comandos.
- `farol/state.py` — gera `reports/DAILY_STATE.md` a partir de
  `farol_daily_state`, classificando cada domínio (CARGA/RECUPERAÇÃO/SONO)
  contra a janela móvel de 90 dias do próprio atleta (docs/MODEL_V1.1.md, que sucede
  docs/MODEL_V1.0.md).
  `gather()` é a fonte única de classificação usada tanto por
  `DAILY_STATE.md` quanto por `DAILY_ANALYSIS.md` (`farol/daily_analysis.py`).
- `farol/longitudinal.py` — streak/trend/episodes: há quanto tempo um domínio
  (ou a convergência) está fora do padrão e para que direção está indo.
  Biblioteca sem CLI próprio; `state.gather()` a consome para preencher a
  coluna "Streak" de `DAILY_STATE.md` e as notas de persistência de
  `DAILY_ANALYSIS.md`.
- `farol/calibration_auto.py` — gera `reports/CALIBRACAO_AUTOMATICA.md` a
  partir da concordância entre o índice do Farol e o Training Readiness do
  Garmin, ambos contra suas próprias baselines individuais. Roda ao fim da
  sincronização e não altera modelo ou perfil.
- `farol/calibration.py` e `farol/calibration_review.py` — registro
  subjetivo e revisão manual legados, opcionais e separados da rotina
  automática.
- `farol/sleep_regularity.py` — SD circular do horário de `sleep_start`
  (janela de 14 dias): circular porque o horário cruza meia-noite. Só
  informativo em `farol.today` — fica de fora do domínio SONO porque a
  janela móvel é autocorrelacionada dia a dia (inflaria streak/convergência
  artificialmente, achado ao validar contra o histórico real).
- `farol/hrv_variability.py` — coeficiente de variação do HRV (7d vs. 30d de
  referência): CV colapsando é indicador precoce de overreaching
  não-funcional na literatura (Plews & Buchheit), às vezes antes da média
  cair. Sem bucket/limiar de propósito — não há base validada ainda.
- `farol/correlations.py` — correlação defasada (lag) entre **desvios**
  contra a própria baseline de 90 dias (não valores brutos, que inflariam
  *r* com tendência lenta compartilhada). Nunca reporta *r* sem *n* ao
  lado, nunca calcula p-valor (dado diário é autocorrelacionado).
- `farol/changepoint.py` — estima candidata a nova quebra de medição
  (CUSUM) por campo de `daily`, para não depender de notar a próxima "no
  olho" como as de `rhr`/`awakenings` foram achadas. Estimativa para
  revisão humana, nunca classificação automática.
- `farol/interpretation.py` — a camada de interpretação: transforma a
  classificação já calculada por `state.gather()` numa leitura em linguagem
  do estado (estado fisiológico, sinal dominante, força da convergência,
  conclusão). Função pura — não recalcula nada, não cria métrica nem limiar
  novo. É a seção 11 do `DAILY_ANALYSIS.md` e o bloco `LEITURA` do
  `farol.today` (vistoria de 2026-09-04: a saída tratava métricas como
  domínios e chamava "deterioração sistêmica" de um dia com 2 domínios
  puxados por 1 campo cada).
- `farol/decoupling.py` — decoupling aeróbico clássico (Pa:Hr): 1ª vs 2ª
  metade de uma única corrida contínua, diferente de
  `aerobic_efficiency`/`continuous_mechanics` (que comparam entre corridas
  ao longo de meses). Usa `farol.splits.fetch_lap_splits`
  (`get_activity_splits`, laps reais por km) — chamada diferente de
  `fetch_typed_splits`, que só cobre treino intervalado estruturado.

- `farol/race_prediction.py` — predição de prova (capacidade F1 da auditoria
  de 2026-09-04). Três modelos independentes — Riegel (1981), Cameron (1999)
  e VDOT (Daniels/Gilbert) — aplicados às provas observadas declaradas em
  `data/profile.yaml -> recent_races` (a mais recente é a âncora; os dados
  vêm da tabela `activities`, o banco é a fonte de verdade). Gera
  `reports/RACE_PREDICTION.md` separando **observado** (a prova no banco),
  **estimativa** (os três modelos + a faixa entre eles, que é a incerteza) e
  **a validar** (comparação contra o resultado real de 19/09). Mostra também
  o TSB atual e o alvo conceitual (descansado/positivo no dia da prova) —
  descreve, não prescreve (PROFILE.md, regra 9). A faixa entre os modelos é
  deliberada: quando eles concordam, o número é mais confiável.

- `farol/dashboard.py` — dashboard HTML de 1 pagina (`reports/DASHBOARD.html`).
  Camada de visualizacao, nao de dominio: nao recalcula nada, cada secao
  chama a mesma funcao publica que o comando de terminal/markdown
  correspondente ja usa (`state.gather`, `race_prediction.predict`,
  `analysis.weekly_load`, `recovery.recovery_index`,
  `correlations.render_report`, `changepoint.render_report`). Arquivo
  estatico, sem servidor -- abre com duplo clique. O changepoint do
  dashboard varre um campo a mais que o padrao do terminal
  (`body_battery_min`): achado da vistoria de 2026-09-04, quebra de -1,66 DP
  em 2026-08-03 fora da lista curada original (farol/changepoint.py).

O `farol/signal.py` (sinal verde/amarelo/vermelho) foi removido em 02/09/2026:
a lógica de convergência dele vive em `today.py::detect_patterns` e a baseline
móvel virou `metrics.py`. Não sobrava nada só dele.

O resumo dos tiros de cada corrida é buscado uma vez pelo `ingest` e guardado em
`activities.splits`. Por isso `farol.today` roda offline, em ~0,2s — antes fazia
login no Garmin a cada execução só para montar essa parte.

## Testes

```bash
python -m unittest discover -s tests
```

435 testes cobrindo TRIMP, CTL/ATL/TSB, a primitiva `Metric`, separação de
procedência, as análises, correlação, CUSUM, regularidade de sono, a camada
de interpretação, a predição de prova e o dashboard HTML. A
maioria roda sem rede e sem planilha, em bancos SQLite em memória; alguns
testes de `farol_daily_state` (golden sample de 2026-09-02) rodam contra o
`data/farol.db` real via `db.connect()`, de forma idempotente.

## Procedência dos dados

O histórico tem duas origens e elas **não são intercambiáveis**:

- **até 10/06/2026** — pipeline do `analise-garmin`
- **a partir de 11/06/2026** — sync atual

As provas de verdade (não treinos fortes) são declaradas à mão em
`data/profile.yaml -> recent_races` (só a data e o nome); distância e tempo
continuam vindo da tabela `activities`. Isso evita duplicar dado e deixa
explícito o que o farol considera prova observada.

O RHR dá um degrau de +5,9 bpm exatamente nessa fronteira (70,2 antes, 76,1
depois) enquanto a FC noturna atravessa estável — dois campos do mesmo dia se
comportando diferente na mesma data indica troca de mapeamento, não mudança
fisiológica. Por isso `daily.source` e `activities.source` existem, e nenhuma
baseline cruza fontes.

As atividades legadas tinham `distance_km` em unidade desconhecida e não
constante (0,0 a 150158 para corridas de 5 a 16 km). Sem reconciliação, a
distância e o pace são anulados no load. Um export local do Strava permite
recuperar essas medidas por data e duração, com `historical_source=strava_export`.
`activities.source` continua `legado`; as baselines de performance, recuperação
e o índice diário não passam a misturar as duas origens.

Reconciliação histórica sob demanda, sem chamada à rede:

```powershell
python -m farol.strava_backfill --zip C:\caminho\export.zip
python -m farol.strava_backfill --zip C:\caminho\export.zip --apply
python -m farol.load
python -m farol.running_history
```

O primeiro comando é prévia. `--apply` faz cópia de segurança local da planilha,
atualiza `Activities` e registra o ID do Strava; `farol.load` atualiza o banco
a partir da planilha preservando os splits tipados cacheados. O relatório
`reports/RUNNING_HISTORY.md` descreve volume, esforço e dinâmica de corrida,
sem entrar na classificação diária. Pace legado usa tempo em movimento, enquanto
o tempo original da planilha continua sendo a entrada do TRIMP. Cadência do
CSV (passadas/min) é convertida para passos/min; dados FIT de dinâmica ficam
marcados com `running_dynamics_source`. Corrida presente apenas no export recebe
`historical_only=1`: aparece no relatório retrospectivo, mas não entra no TRIMP
nem modifica estados diários já calibrados.

## O que fica de fora por enquanto (de propósito)

Sincronização contínua com Strava, Intervals.icu, distribuição por zona, correção farmacológica,
agendamento automático, MCP.
