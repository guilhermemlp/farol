# Modelo Analítico Farol v1.1

**Data**: 2026-09-04
**Referência**: parecer técnico de fisiologia do esporte e ciência de dados, 2026-09-04
**Status**: Em vigor. Sucede `MODEL_V1.0.md`, que fica **congelado como histórico** — não
foi editado para escrever este documento, e continua sendo a descrição exata do que
`farol_daily_state` era em 2026-09-03.

---

## O que muda em relação ao v1.0

O v1.0 tinha 18 campos em `farol_daily_state`, 5 por domínio em RECUPERAÇÃO e SONO. O
v1.1 tem **23 campos na tabela**, mais **capacidades analíticas que deliberadamente não
entram na tabela** — a diferença entre "dado estruturado, comparável dia a dia" e
"leitura pontual ou de mais baixo nível de evidência" passou a ser explícita, não
implícita.

Resumo:

| | v1.0 | v1.1 |
|---|---|---|
| Campos em `farol_daily_state` | 18 | 23 |
| DOMÍNIO RECUPERAÇÃO | 5 campos | 9 campos |
| DOMÍNIO SONO (domínio) | 5 campos | 5 campos (inalterado) |
| Campo derivado fora do domínio | — | `sleep_regularity_min` (informativo, não classificado) |
| Segundo juiz independente | não existia | comparação com `training_readiness_score` do Garmin |
| Distinção doença vs. fadiga de treino | não existia | `today.signature_doenca()` |
| Predição de prova | não existia | `farol.insights.race_prediction` (Riegel/Cameron/VDOT sobre provas observadas; estimativa, nunca prescrição) |
| Camada de interpretação (leitura em linguagem) | não existia | `farol.domain.interpretation` (seção 11 do `DAILY_ANALYSIS.md`, bloco `LEITURA` do `today`) |
| Correlação entre domínios | não existia | `farol.insights.correlations` |
| Detecção de nova quebra de medição | manual (auditoria pontual) | `farol.insights.changepoint` (CUSUM, estimativa para revisão humana) |
| Calibração operacional | manual, implícita | `farol.insights.calibration_auto` (Farol × Training Readiness Garmin) |

Nenhuma fórmula do v1.0 foi alterada — CTL/ATL/TSB, TRIMP, z-score de 90 dias, a regra
de convergência (2+ domínios) continuam exatamente como estavam. Tudo abaixo é adição,
não revisão.

---

## Os 5 domínios (RECUPERAÇÃO e OBSERVAÇÃO mudaram)

### 1. DOMÍNIO CARGA (6 campos, inalterado)

Ver `MODEL_V1.0.md`, seção 1 — `trimp_7d`, `trimp_28d`, `ctl`, `atl`, `tsb`,
`atl_ctl_ratio`. Nenhuma mudança.

**Contexto informativo adicionado (fora da tabela)**: `farol.reports.today` agora também mostra
o VO2max do Garmin (`daily.vo2max`) como uma `Metric` de tendência, ao lado de CTL/ATL/
TSB. PROFILE.md é explícito: esse valor não é comparável ao VO2max do exame ergométrico
(53,8 ml/kg/min) e não deve ancorar nada — só a tendência contra os próprios 90 dias
importa (regra 6). Por isso ele nunca entra em `farol_daily_state`, nem no dict de
métricas que alimenta `detect_patterns`/`recovery_index`: é um sinal lento (o Garmin só
recalcula depois de certos treinos, o mesmo valor se repete por dias), e deixá-lo
participar da convergência inflaria streak com um valor que não mudou de verdade.

### 2. DOMÍNIO RECUPERAÇÃO (9 campos, era 5)

**Campos do v1.0** (inalterados): `hrv_avg`, `rhr`, `night_hr_avg`, `stress_avg`,
`body_battery_max`.

**Campos novos**:

| Campo | Tipo | Direção | Cobertura típica |
|-------|------|---------|-------------------|
| `body_battery_min` | INTEGER | maior é melhor | ~40% |
| `respiration_avg` | REAL | menor é melhor | ~40% |
| `spo2_avg` | REAL | maior é melhor | ~40% |
| `spo2_min` | REAL | maior é melhor | ~40% |

Todos já eram coletados pelo Garmin e guardados em `daily` desde o início do projeto —
nunca tinham sido lidos por nenhuma análise. Mesmo tratamento de `confidence` que HRV já
recebe (a cobertura parcial não é dado sujo, é o mesmo padrão que HRV/sono sempre
tiveram). Entram no `domain_status()` de RECUPERAÇÃO normalmente (o domínio usa "pior
status entre as métricas com dado"), e em `farol.reports.today` participam de
`detect_patterns`/convergência — mas **não** entram em `PESOS_PADRAO` do
`recovery_index`: mesmo precedente que `body_battery_max`/`stress_avg` já seguiam
(no domínio, fora do índice calibrado, que exige calibração explícita antes de qualquer
peso novo).

**Sinal complementar, fora da tabela**: `farol.domain.hrv_variability.hrv_cv()` mostra o
coeficiente de variação do HRV numa janela de 7 dias, ao lado de uma referência de 30
dias. Plews & Buchheit: CV colapsando (ficando artificialmente estável) é indicador
precoce de overreaching não-funcional, às vezes antes da média cair. Deliberadamente sem
bucket ok/atenção/alerta e fora de `farol_daily_state`: é uma janela móvel de 7 dias,
sujeita ao mesmo problema de autocorrelação descrito abaixo para `sleep_regularity_min`,
e não há limiar numérico validado — nem na literatura, nem no histórico deste usuário —
para "quanto é colapso". Mostra os dois números e deixa o padrão para o olho humano ao
longo de vários dias, como CTL/ATL já são mostrados sem z-score.

**Segundo juiz**: `farol.insights.calibration` agora compara o índice de recuperação do farol
contra `daily.training_readiness_score` — calculado pelo próprio Garmin, por outro
algoritmo, a partir de um tipo de sinal parecido. Classificado pela mesma janela pessoal
de 90 dias que qualquer métrica do farol usa (nunca o limiar genérico do Garmin — seria
repetir, na comparação, o erro que o resto do farol evita desde o v1.0, PROFILE.md regra
10). O resultado (`concorda` / `diverge` / `sem dado suficiente`) fica registrado em
`CALIBRACAO_AUTOMATICA.md` compara automaticamente os dois lados em cada sincronização,
sempre contra a baseline pessoal disponível naquela data. A comparação descreve
concordância e nunca altera pesos ou a classificação. O `CALIBRACAO_LOG.txt` permanece
apenas como complemento subjetivo opcional.

### 3. DOMÍNIO SONO (5 campos no domínio, inalterado — 1 campo novo fora dele)

Os 5 campos que entram em `domain_status()` continuam os mesmos do v1.0:
`sleep_total_min`, `awake_minutes`, `deep_sleep_min`, `rem_sleep_min`, `sleep_score`.

**Campo novo, armazenado mas fora da classificação**: `sleep_regularity_min` — SD
circular (minutos) do horário de `sleep_start`, numa janela móvel de 14 dias
(`farol/domain/sleep_regularity.py`). Circular porque o horário de dormir cruza meia-noite
(23h50 e 00h10 são 20 minutos de diferença, não 1420 — um SD linear ingênuo infla o
resultado para qualquer atleta que durma perto da meia-noite). Pesquisa recente (SLEEP,
2024) liga irregularidade de horário a maior risco cardiometabólico e de mortalidade,
independente de duração.

**Por que fica fora de `domain_status`/streak/convergência, apesar de estar na tabela**:
achado ao validar contra o histórico real, não uma decisão teórica. A primeira versão
deste campo entrava no domínio SONO normalmente — e inflou um episódio de streak
validado ao vivo de 7 para 15 dias, porque a janela de 14 dias é fortemente
autocorrelacionada dia a dia (13 dos 14 pontos se repetem no dia seguinte): uma única
mudança de padrão de sono virava uma sequência de ~2 semanas "ruins" só pela sobreposição
da janela, não por evidência independente repetida. Exatamente o tipo de artefato que a
regra de convergência (regra 8) foi desenhada para evitar. `sleep_regularity_min` fica
gravado em `farol_daily_state` (dado útil para análise futura) mas só é exibido de forma
informativa em `farol.reports.today`, sem bucket.

**Consequência prática**: um consumidor de `farol_daily_state` que itere todas as colunas
sem saber disso pode achar, por engano, que `sleep_regularity_min` deveria contar como
6º sinal de SONO. Não deve — releia o parágrafo acima antes de mudar isso.

### 4. DOMÍNIO PERFORMANCE (2 campos, inalterado)

`output_preserved`, `deterioration_detected` — sem mudança de fórmula.

**Capacidade relacionada, fora da tabela**: `today.signature_doenca()` distingue, dentro
da mesma convergência da regra 8, duas causas com a mesma assinatura de superfície: 3+
de {HRV, RHR, FC noturna, respiração, SpO2} fora do padrão ao mesmo tempo que TSB
**carregado** é fadiga de treino absorvendo carga (já coberto pelo domínio CARGA). Os
mesmos 3+ sinais **sem** TSB carregado é o padrão que estudos de detecção de doença via
wearable associam a resposta imunológica em curso — TSB é o proxy de "precedido por pico
de carga" (TSB = CTL − ATL, TSB baixo já é a própria assinatura de fadiga aguda recente).

Limiar calibrado contra o histórico real, não teórico: a versão inicial usava "2 de 5"
sinais (mesmo "2" da regra 8 original, calibrada para 2 de 3 domínios largos) e disparava
em 17% dos dias — frequência incompatível com "resposta imunológica ativa" ser um evento
raro. Subir para 3-de-5 caiu para ~8%. **`body_battery_min` ficou fora da contagem em
2026-09-04 (C2, após VISTORIA)**: o campo marca 33/35 dias recentes (94%) como
atenção/alerta — piso volátil do Garmin, não discrimina resposta imunológica de ruído — e
com ele dentro o 3-de-6 disparava em ~20% dos dias; removido, o 3-de-5 dispara em 6% do
histórico (13/212), dentro da faixa calibrada, e o dia 04/09 deixa de disparar (5 sinais
agudos reais ok). **Limiar de partida, não recalibrado com percepção real** — revisar
contra `CALIBRACAO_LOG.txt`/`farol.insights.calibration_review` depois de acumular entradas
suficientes.

**Guarda-corpo obrigatório** (mesma regra de `medical_context.md` já citada em
PROFILE.md): o texto só descreve o padrão ("compatível com resposta imunológica ativa"),
nunca diagnostica.

### 5. CAMADA DE INTERPRETAÇÃO (`farol.domain.interpretation`, nova em 2026-09-04)

Nenhum campo novo na tabela. É uma camada **acima** da classificação já
congelada: `state.gather()` continua sendo a fonte única (domínios, status,
confiança, streak/trend, performance, breakpoints) e `farol.domain.interpretation`
só **agrupa, nomeia e reconcilia** o que já foi classificado, em linguagem:

- **Estado fisiológico** — núcleo autonômico comprometido (2+ de
  HRV/FC repouso/FC noturna/Stress fora do padrão) ≠ 2 domínios borderline
  puxados por 1 campo cada (sinal concentrado, não deterioração ampla).
- **Sinal dominante** — quando todos os sinais do dia são campos noturnos
  (SpO2, sono profundo/REM/score, FC noturna), a leitura diz "alteração
  noturna", não "deterioração sistêmica".
- **Convergência qualificada** — a regra 8 (2+ domínios) vira texto com
  força: fraca (2 domínios, 1 sinal cada) vs. forte (vários sinais em
  vários domínios ao mesmo tempo).
- **Conclusão** — nenhuma métrica, nenhum limiar novo: só a classificação
  existente lida com as regras acima (núcleo, alerta, espalhamento).

Princípio mantido: descreve, não prescreve. A vistoria de 2026-09-04
(`docs/archive/VISTORIA_2026-09-04.md`) mostrou que a saída antiga chamava
"deterioração sistêmica" de um dia com 2 domínios BORDERLINE puxados por 1
campo cada — a camada existe para a leitura não contradizer a própria
classificação.

### 6. DOMÍNIO OBSERVAÇÃO (1 campo, inalterado — processo de descoberta mudou)

`measurement_breakpoints` continua a mesma lista fixa de descontinuidades conhecidas
(`rhr` em 2026-06, `awakenings` em 2026-08) — nenhum campo novo adicionado
automaticamente por código nenhum. **`sleep_score` entrou na lista em 2026-09-04**
(mesma atualização da API do Garmin de 2026-08-01 que mudou `awakenings`, política
`split_by_date`): a descontinuidade já estava documentada no `schema.sql`, mas a
execução não a aplicava — a baseline de 90 dias de um dia de setembro misturava
scores de junho/julho (definição antiga) com os de agosto/setembro (definição nova),
z-score que mediria troca de definição, não fisiologia. Agora a política é executada
em `farol.core.metrics.DATE_SPLITS` (a janela nunca cruza 2026-08-01), registrada em
`measurement_breakpoints` e coberta por teste (`TestSplitPorData`).

Honestidade da medição: o deslocamento no histórico real é pequeno (média 59,3 → 56,6;
CUSUM de `farol.insights.changepoint` não acha candidata ≥ 1,0 SD em `sleep_score`) — o split é
a decisão conservadora da política documentada, não um achado estatístico novo. O custo
é cobertura: nos primeiros dias após 08-01 o campo fica `sem_dado` (sem baseline
comparável no lado novo ainda), o que degrada confiança, nunca vira erro.

O que mudou é como a *próxima* quebra é procurada.

**`farol.insights.changepoint`** (CUSUM — Page, 1954): soma cumulativa dos desvios padronizados
contra a média geral; o ponto de maior soma cumulativa é a melhor estimativa de onde um
deslocamento de nível aconteceu numa série. Validado contra as duas quebras já
documentadas antes de ser considerado confiável: achou `rhr` em 2026-06-06 (a fonte
muda em 06-04, o salto visível é em 06-11) e `awakenings` em 2026-08-03 (a mudança real é
em 08-01) — a poucos dias das datas reais, nos dois casos. Uma primeira versão (janela
local de 14 dias, sem CUSUM) achava `awakenings` mas perdia `rhr` — o deslocamento de
+5,9 bpm era pequeno demais frente ao ruído para o poder estatístico de uma janela curta.

Deliberadamente uma **estimativa para revisão humana**, nunca um classificador
automático: uma mudança abrupta de nível pode ser quebra de medição (o padrão de
`rhr`/`awakenings`) ou mudança fisiológica/de contexto real (doença, viagem, fase de
treino) — só quem tem o contexto decide qual, e só depois disso o campo entra
manualmente em `measurement_breakpoints`.

---

## Capacidade nova: predição de prova (`farol.insights.race_prediction`)

Capacidade F1 da auditoria de 2026-09-04: estimar o tempo da meia de 19/09 a
partir de provas observadas recentes — **decisão de produto, não mudança de modelo**.
Nenhum campo novo na tabela, nenhuma fórmula do v1.1 alterada: é uma camada
**acima** dos dados, como `farol.insights.decoupling`.

Método: três modelos independentes, cada um uma estimativa, nunca prescrição
(PROFILE.md, regras 7 e 9):

- **Riegel (1981)** — `T2 = T1 × (D2/D1)^1.06`, lei de potência com expoente de
  fadiga único (1.06 = padrão recreacional; elite ~1.05, menos treinado
  ~1.08). Sem calibração própria para ajustar `k` (exigiria 2+ provas em
  distâncias diferentes), o padrão fica explícito e documentado como populacional.
- **Cameron (1999)** — `T2 = (T1/D1) × D2 × (f(D1)/f(D2))`, com
  `f(x) = 13.49681 − 0.000030363x + 835.7114/x^0.7905` (x em metros). Fadiga
  específica por distância, calibrada contra os melhores tempos mundiais;
  tende a ser mais conservador que Riegel em distâncias longas.
- **VDOT (Daniels/Gilbert)** — converte a prova num equivalente de VO2max
  (`VO2 = −4.60 + 0.182258S + 0.000104S²`, S em m/min, dividido pela fração de
  VO2max sustentável `0.8 + 0.1894393e^(−0.012778t) + 0.2989558e^(−0.1932605t)`)
  e acha o tempo na distância-alvo que mantém o mesmo VDOT.

O relatório (`reports/RACE_PREDICTION.md`) separa **observado** (provas do
banco declaradas em `profile.yaml -> recent_races`), **estimativa** (os três
modelos + a faixa entre eles, que é a incerteza) e **a validar** (comparação
contra o resultado real de 19/09; a prova real entra como nova âncora e o
loop fecha). Mostra também o TSB atual e o alvo conceitual (positivo/descansado
no dia da prova) — decidir como chegar lá é do treinador.

Calibração honesta: fórmulas validadas contra valores publicados (5k 20:00 →
VDOT ~49,8; tempo de volta de uma prova reproduz o original); a âncora atual é
a prova de 10 km de 16/08 (10,033 km em 59,4 min, VDOT ~32,8 → meia ~2:11).
Faixa entre modelos: a incerteza do método, não um número único.

---

## Capacidade nova sem lugar num domínio: correlação entre domínios

`farol.insights.correlations` responde a uma pergunta que nenhum dos 5 domínios respondia
sozinho: "o que prediz o quê, com que defasagem?" Duas decisões deliberadas, contra os
erros clássicos de correlacionar séries diárias de fisiologia:

1. Correlaciona `delta_vs_baseline` (o desvio contra a própria média de 90 dias, que
   `Metric` já calcula), não o valor bruto — remove tendência lenta compartilhada (fase
   de treino, estação, quebra de fonte) que infla *r* sem relação causal nenhuma.
2. Nunca reporta *r* sem *n* ao lado, nunca calcula p-valor — dado diário de fisiologia é
   autocorrelacionado, um p-valor ingênuo superestimaria confiança. Mínimo de 30 pares
   antes de reportar qualquer número.

Roda 7 pares curados (não exaustivos — testar toda combinação seria pesca estatística),
sob demanda, fora do fluxo diário. Resultado sempre descritivo, gera hipótese, nunca
confirma causa (mesma regra 3 do PROFILE.md).

---

## Fluxo de Interpretação (estendido)

O fluxo do v1.0 (verificação de descontinuidades → análise domínio-por-domínio →
convergência → evidência contra) continua valendo sem mudança. Duas etapas novas,
inseridas no lugar certo:

```
2.5. Segundo juiz (apenas quando ha dado)
├─ training_readiness_score do Garmin concorda com o indice do farol?
└─ Divergencia nao invalida o indice -- e' informacao para o log de calibracao,
   nao um veto.

3.5. Distinguir causa da convergencia (quando RECUPERACAO+CARGA convergem)
├─ TSB carregado? -> fadiga de treino absorvendo carga (dominio CARGA ja cobre)
└─ TSB normal, 3+ sinais agudos fora do padrao? -> signature_doenca(): padrao
   compativel com resposta imunologica, nunca diagnostico
```

---

## Capacidade nova: decoupling aeróbico intra-corrida

`farol.insights.decoupling` (destravado em 2026-09-04, mesmo dia — a recomendação 6 do parecer
técnico ficou congelada só até se confirmar que os dados existiam). Diferente de
`aerobic_efficiency`/`continuous_mechanics`, que comparam FC~pace **entre** corridas ao
longo de meses, decoupling compara a 1ª metade contra a 2ª metade de **uma única**
corrida contínua (Pa:Hr, TrainingPeaks/Uphill Athlete) — um sinal por sessão, não uma
tendência.

Precisava de laps reais por km dentro de uma corrida, que `farol.pipeline.splits` (via
`get_activity_typed_splits`) não fornece — essa chamada só cobre treino intervalado
estruturado. `farol.pipeline.splits.fetch_lap_splits` (`get_activity_splits`) resolve isso:
confirmado ao vivo contra uma corrida contínua real (Longo 15 km, 2026-08-22) — 15 laps
de 1000 m + 1 lap final parcial, cada um com `distance`/`duration`/`averageHR` e, de
bônus, `averageTemperature` (não capturado ainda — ver parecer técnico, achado sobre
temperatura como possível confundidor de `aerobic_efficiency`, ainda em aberto).

Sob demanda, como `farol.pipeline.splits` — não entra no sync diário. `<5%` de variação é
comumente citado na literatura como boa base aeróbica; **não validado contra o
histórico deste usuário** (diferente de `signature_doenca`, calibrado contra 214 dias
reais antes de virar código) — o número é mostrado, sem veredito embutido.

---

## Contrato de Dados (campos novos)

| Campo | Tabela | Tipo | Min | Max | Notas |
|-------|--------|------|-----|-----|-------|
| `body_battery_min` | `farol_daily_state` | INTEGER | 0 | 100 | ~40% cobertura |
| `respiration_avg` | `farol_daily_state` | REAL | 8 | 30 | ~40% cobertura, rpm |
| `spo2_avg` / `spo2_min` | `farol_daily_state` | REAL | 70 | 100 | ~40% cobertura, % |
| `sleep_regularity_min` | `farol_daily_state` | REAL | 0 | — | derivado (janela 14d), não coluna direta de `daily`; NULL com <4 pontos na janela |

Política de NULL: igual às demais colunas de cobertura parcial (RHR/stress são as
únicas quase sempre presentes) — NULL é normal, `confidence` degrada, nunca vira erro.

**Migração de bancos criados antes do v1.1**: `db.ensure_schema()` adiciona as 5 colunas
via `ALTER TABLE` (checado por `PRAGMA table_info`, idempotente) — `CREATE TABLE IF NOT
EXISTS` sozinho não adiciona coluna a uma tabela que já existe. Validado por simulação
contra o schema exato do v1.0 nesta auditoria (2026-09-04).

---

## Histórico de Versões

| Versão | Data | Mudanças |
|--------|------|----------|
| v1.0 | 2026-09-03 | Congelado — ver `MODEL_V1.0.md` |
| **v1.1** | 2026-09-04 | RECUPERAÇÃO 5→9 campos; `sleep_regularity_min` informativo; segundo juiz (Garmin); `signature_doenca`; `farol.insights.correlations`; `farol.insights.changepoint`; `farol.insights.calibration_review`; `farol.insights.decoupling` (recomendação 6, destravada no mesmo dia); **camada de interpretação** (`farol.domain.interpretation`, seção 11 do `DAILY_ANALYSIS.md` + `LEITURA` no `today`). Correções pós-vistoria (C1–C5): `_work_session` exige ≥2 reps (corrida contínua não vira `output_preserved`); `body_battery_min` sai de `_SINAIS_DOENCA` (3-de-5, ~6% do histórico); `detect_patterns` passa a falar em sinais, nunca domínios; conclusão distingue deterioração de sinal isolado; `sleep_score` entra em OBSERVAÇÃO (`split_by_date`, ver seção 6) |
| v1.1 (F1) | 2026-09-04 | **Predição de prova** (`farol.insights.race_prediction`): Riegel/Cameron/VDOT sobre provas observadas (`profile.yaml -> recent_races`); `reports/RACE_PREDICTION.md` com observado/estimativa/a validar + bloco no `today`; nenhuma fórmula do v1.1 alterada |
| v1.1 (correções) | 2026-11-03 | **Coerência da leitura**: definição única e pública de núcleo autonômico comprometido (`interpretation.nucleo_comprometido`/`alteracao_sistemica`, 2+ campos) — `daily_analysis` deixava de declarar "núcleo fora do padrão" com um único campo do núcleo em atenção, contradizendo `interpretation`. **Correlações**: o período reportado passa a terminar na última data de X efetivamente emparelhada, não no corte nominal (com lag > 0 o último dia não tem Y dentro do corte). **Manutenção**: `farol/domain/snapshot.py` centraliza a montagem das `Metric` diárias de `today`/`dashboard`. Nenhuma fórmula, baseline, limiar ou classificação alterada |
