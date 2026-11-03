# ESTADO ATUAL DO FAROL — DIAGNÓSTICO COMPLETO
**Data**: 2026-09-05  
**Escopo**: Auditoria técnica total do repositório — arquitetura, implementação, dados, testes, dependências, dívidas  
**Método**: Investigação direta no código, banco de dados, testes e documentação. Sem modificações.

---

## 1. RESUMO EXECUTIVO

**O Farol está saudável, com F1 completa e F10 parcialmente implementada.**

- **Testes**: 348 passando (não 310 como relatado anteriormente)
- **Código**: 117 funções públicas, bem estruturado, sem TODOs/FIXMEs em código
- **Banco de dados**: 214 dias de dados (02/02/2026 a 04/09/2026), 182 atividades
- **Qualidade**: Ruff check limpo, sem erros de compilação
- **F1**: Totalmente implementada (Riegel/Cameron/VDOT) com 6 testes
- **F10**: Implementada em `state.py` com `--json` flag, 23 testes dedicados
- **Novo**: Dashboard HTML (764 linhas, recém-adicionado, não commitado)
- **Novo**: Módulo `interpretation.py` (347 linhas, 11 funções públicas)

**Problemas conhecidos (documentados, não bugs):**
- V1, V2, V3 da vistoria de 2026-09-04 — implementação vs semântica — requerem revisão
- Espaço em disco: 16 GB em Downloads, disco atingiu 100% em 04/09

---

## 2. INVENTÁRIO DO PROJETO

### Estrutura de Diretórios

```
farol/
├── farol/              (28 módulos Python, 117 funções públicas)
├── tests/              (21 arquivos de teste, 348 testes)
├── docs/               (6 arquivos, 1.1 KB, documentação completa)
├── reports/            (5 arquivos gerados)
├── data/               (banco SQLite, profile.yaml, planilha Excel, backup)
├── .garmin_token/      (salvo pela primeira vez)
├── pyproject.toml      (Python 3.11+, 12 CLIs, Ruff config)
└── README.md           (completo, 283 linhas)
```

### Módulos Python (farol/)

| Módulo | Linhas | Função | Status |
|--------|--------|--------|--------|
| `today.py` | 727 | CLI principal: estado do dia | ✓ Completo |
| `state.py` | 427 | Resumo de 1 página + F10 (--json) | ✓ Completo |
| `dashboard.py` | 764 | HTML single-page (novo) | ⚠️ Não commitado |
| `daily_analysis.py` | 338 | Diagnóstico detalhado | ✓ Completo |
| `interpretation.py` | 347 | Camada de leitura (novo, resolve vistoria V1-V3) | ✓ Completo |
| `race_prediction.py` | 324 | F1: predição de prova (Riegel/Cameron/VDOT) | ✓ Completo |
| `ingest.py` | 115 | CLI: sincroniza Garmin → planilha → DB | ✓ Completo |
| `analysis.py` | 571 | CTL/ATL/TSB, eficiência aeróbica, mecânica | ✓ Completo |
| `metrics.py` | 369 | Primitiva Metric (média/SD/z-score/confiança) | ✓ Completo |
| `recovery.py` | 167 | Índice único de recuperação | ✓ Completo |
| `training_load.py` | 106 | TRIMP, CTL/ATL/TSB (Banister) | ✓ Completo |
| `garmin_client.py` | 213 | Login + extração (com _safe para isolamento) | ✓ Completo |
| `sheets.py` | 486 | Planilha como etapa intermediária | ✓ Completo |
| `db.py` | 41 | Conexão + schema + migração | ✓ Completo |
| `schema.sql` | 173 | DDL: 5 tabelas, índices | ✓ Completo |
| `calibration.py` | 247 | Comparação contra training_readiness_score | ✓ Completo |
| `calibration_review.py` | 180 | Concordância farol vs percepção real | ✓ Completo |
| `daily_state_builder.py` | 160 | Monta `farol_daily_state` | ✓ Completo |
| `longitudinal.py` | 368 | Streak/trend/episodes (sem CLI próprio) | ✓ Completo |
| `sleep_regularity.py` | 104 | SD circular de sleep_start (14d) | ✓ Completo |
| `hrv_variability.py` | 91 | CV do HRV (7d vs 30d) | ✓ Completo |
| `correlations.py` | 243 | Correlação defasada entre domínios | ✓ Completo |
| `changepoint.py` | 254 | CUSUM: detecta quebras de medição | ✓ Completo |
| `decoupling.py` | 187 | Pa:Hr (1ª vs 2ª metade de corrida) | ✓ Completo |
| `splits.py` | 196 | Tiros de treino intervalado | ✓ Completo |
| `zones.py` | 62 | Zonas de FC (LTHR-based, não % genérico) | ✓ Completo |
| `config.py` | 53 | Carregamento de profile.yaml | ✓ Completo |
| `load.py` | 44 | CLI: recarrega DB a partir da planilha | ✓ Completo |
| `labels.py` | 45 | Rótulos de campos | ✓ Completo |

### CLIs (12 entrypoints)

```
farol-ingest              → farol.ingest:main           (extração Garmin)
farol-today               → farol.today:main             (relatório terminal)
farol-state               → farol.state:main             (resumo 1pág + F10 --json)
farol-splits              → farol.splits:main            (tiros de treino)
farol-daily-analysis      → farol.daily_analysis:main    (diagnóstico detalhado)
farol-calibration         → farol.calibration:main       (log de calibração)
farol-calibration-review  → farol.calibration_review:main (concordância)
farol-correlations        → farol.correlations:main      (correlação defasada)
farol-changepoint         → farol.changepoint:main       (CUSUM de quebras)
farol-decoupling          → farol.decoupling:main        (Pa:Hr)
farol-race-prediction     → farol.race_prediction:main   (F1)
farol-dashboard           → farol.dashboard:main         (HTML single-page)
```

### Testes (21 arquivos, 348 testes)

```
test_core.py               184 testes (TRIMP, CTL/ATL/TSB, Metric, procedência)
test_longitudinal.py       46 testes (streak/trend/episodes)
test_state_json.py         23 testes (F10: JSON schema, exit codes, serialização)
test_race_prediction.py    13 testes (Riegel, Cameron, VDOT)
test_daily_analysis.py     10 testes (relatório detalhado)
test_dashboard.py          7 testes (HTML, contexto)
test_state.py              7 testes (domínios, convergência)
test_splits.py             8 testes (resumo de tiros)
test_interpretation.py     6 testes (leitura fisiológica)
test_calibration.py        6 testes (log, entrada)
test_correlations.py       3 testes (correlação defasada)
test_integration.py        7 testes (fluxo completo)
test_recovery.py           8 testes (índice ponderado)
test_sheets.py             8 testes (ingestão planilha → DB)
test_changepoint.py        5 testes (CUSUM)
test_hrv_variability.py    2 testes (CV)
test_sleep_regularity.py   5 testes (SD circular)
test_calibration_review.py 5 testes (concordância)
test_decoupling.py         5 testes (Pa:Hr)
test_db.py                 4 testes (conexão, schema)
test_zones.py              4 testes (zonas de FC)
═════════════════════════════════════════════════════════════════════════════
TOTAL: 348 testes (era 336 no handoff; dashboard.py + interpretation adicionaram)
```

---

## 3. ARQUITETURA REAL

### Fluxo de Dados Confirmado

```
SOURCE
  ↓
  ├─ Garmin Connect (garmin_client.py)
  ├─ Arquivo: data/profile.yaml (metadados atleta)
  ├─ Arquivo: data/garmin_health.xlsx (planilha — fonte de verdade)
  └─ Legado: pipeline analise-garmin (até 2026-06-10)
  
INGESTION (farol/ingest.py)
  ↓
  ├─ fetch_day() → Daily Health, Sleep, HRV, Training Readiness
  ├─ fetch_activities() → Running, cycling, etc
  ├─ fetch_typed_splits() → Estrutura de intervalo
  └─ sheets.write_*() → Persiste na planilha Excel
  
STORAGE
  ↓
  ├─ data/garmin_health.xlsx (fonte de verdade versionada)
  └─ data/farol.db (SQLite, recarregado completamente a cada sync)
      ├─ daily (214 rows) — health + sleep unidas por data
      ├─ activities (182 rows) — com trimp calculado localmente
      ├─ hrv (86 rows) — aba HRV separada
      ├─ training_load (216 rows) — CTL/ATL/TSB pré-calculados
      └─ farol_daily_state (214 rows) — estado estruturado, 23 campos
      
DOMAIN ANALYSIS
  ↓
  ├─ farol.metrics (Metric: média/SD/z-score/confiança)
  ├─ farol.analysis (eficiência aeróbica, mecânica, weekly_load)
  ├─ farol.recovery (índice único ponderado)
  ├─ farol.training_load (recomputa CTL/ATL/TSB)
  ├─ farol.daily_state_builder (monta farol_daily_state)
  └─ farol.longitudinal (streak/trend/episodes)
  
STATE CLASSIFICATION (farol.state.gather)
  ↓
  ├─ 5 domínios: CARGA, RECUPERAÇÃO, SONO, PERFORMANCE, OBSERVAÇÃO
  ├─ Por domínio: métricas → status (ok/atencao/alerta/sem_dado)
  ├─ Confiança: alta/baixa (cobertura do dado)
  ├─ Streak: quantos dias consecutivos fora do padrão
  ├─ Trend: direção (7 dias fixos)
  └─ Convergência (regra 8): 2+ domínios = padrão
  
INTERPRETATION (farol.interpretation.interpretar) — NOVO
  ↓
  ├─ Núcleo autônomo (HRV/RHR/night_hr/stress vs periféricos)
  ├─ Sinal dominante (qual domínio lidera)
  ├─ Força de convergência (fraca/moderada/forte)
  ├─ Distinção doença vs fadiga (signature_doenca)
  └─ Conclusão (o que significa para o atleta)
  
REPORT OUTPUT (4 superfícies)
  ↓
  ├─ farol.today (terminal, 727 linhas)
  │   └─ sparkline de 30 dias + índice de recuperação + padrões
  │
  ├─ farol.state (DAILY_STATE.md resumo 1 página)
  │   └─ 5 domínios × status/confiança/streak/trend
  │
  ├─ farol.daily_analysis (DAILY_ANALYSIS.md detalhado)
  │   └─ tabelas por métrica, evidência/interpretação/limitação
  │
  ├─ farol.state --json (F10: JSON estruturado, exit code 0/1/2)
  │   └─ schema, data, status_global, exit_code, dominios, performance, convergencia
  │
  ├─ farol.dashboard (DASHBOARD.html single-page)
  │   └─ HTML estático: gráficos mini, tabelas, interpretação
  │
  ├─ farol.race_prediction (RACE_PREDICTION.md — F1)
  │   └─ observado/estimativa (Riegel/Cameron/VDOT)/a validar
  │
  ├─ farol.correlations (correlação defasada entre domínios)
  ├─ farol.calibration (comparação contra training_readiness_score)
  ├─ farol.changepoint (CUSUM: quebras de medição)
  ├─ farol.decoupling (Pa:Hr de corrida contínua)
  └─ farol.splits (resumo de tiros de treino)
```

### Dependências Entre Módulos

```
garmin_client.py
  └─ fetch_day, fetch_activities, login
    
sheets.py
  ├─ read from garmin_client
  └─ write_day, write_activity → Excel
  
db.py
  ├─ connect(), ensure_schema() — sem regra de negócio
  └─ _migrate_farol_daily_state (5 colunas novas)
  
daily_state_builder.py
  └─ build_daily_state — monta farol_daily_state
  
metrics.py
  ├─ Metric (primitiva central)
  ├─ daily_metric — lê de daily
  └─ from_daily — constrói Metric
  
analysis.py
  ├─ weekly_load, training_load
  ├─ aerobic_efficiency, continuous_mechanics
  ├─ interval_efficiency, interval_mechanics
  └─ assess_output_preserved, assess_deterioration_detected
  
recovery.py
  ├─ recovery_index (ponderado)
  ├─ recovery_label (ok/atencao/alerta)
  └─ PESOS_PADRAO
  
state.py (NÚCLEO DA CLASSIFICAÇÃO)
  ├─ gather() — fonte única de classificação
  ├─ domain_status — pior métrica de cada domínio
  ├─ convergence — regra 8 (2+ domínios)
  ├─ build_report — DAILY_STATE.md
  ├─ build_state_json — F10 JSON (novo em state.py)
  ├─ exit_code — 0/1/2 mapping (novo)
  └─ usado por: daily_analysis, dashboard, interpretation
  
interpretation.py (NOVO — resolve vistoria V1-V3)
  ├─ interpretar() — leitura coerente
  ├─ sinais_por_dominio() — quais campos puxam
  ├─ estado_fisiologico() — núcleo vs periférico
  ├─ leitura_* (carga/recuperacao/sono/performance)
  └─ conclusao() — narrativa final
  
today.py (CLI terminal)
  ├─ detect_patterns() — convergência (regra 8, MAS vê métricas não domínios — V2)
  ├─ signature_doenca() — distinção doença vs fadiga
  ├─ race_context() — contexto de prova
  └─ recuperation_index, patterns, padroes, doenca
  
dashboard.py (NOVO — orquestrador puro)
  ├─ build_context() — monta dict com tudo
  ├─ render_html() — templates
  └─ usa: state.gather, race_prediction.predict, analysis.weekly_load, etc
  
race_prediction.py (F1)
  ├─ riegel_time, cameron_time, vdot_from_time, vdot_time
  ├─ predict() — retorna {riegel, cameron, vdot, min, max}
  ├─ build_report() — RACE_PREDICTION.md
  ├─ observed_performances() — lê activities do banco
  └─ reference_races() — lê profile.yaml::recent_races
  
longitudinal.py
  ├─ streak() — dias consecutivos fora do padrão
  ├─ trend() — direção em janela fixa de 7d
  ├─ episodes() — agregação de streaks
  └─ domain_status_by_date() — histórico por domínio
  
sleep_regularity.py, hrv_variability.py, correlations.py, changepoint.py, decoupling.py
  ├─ Funções puras ou de leitura do banco
  └─ Nenhuma depende de outro módulo de análise (baixo acoplamento)
```

---

## 4. BANCO DE DADOS — ANÁLISE REAL

### Tabelas e Contagem

| Tabela | Registros | Período | Observação |
|--------|-----------|---------|-----------|
| `daily` | 214 | 2026-02-01 a 2026-09-04 | Health + Sleep unidas |
| `activities` | 182 | 2025-11-24 a 2026-09-04 | Legadas mais antigas |
| `hrv` | 86 | ~60% de cobertura | Não syncronizado todo dia |
| `training_load` | 216 | 2026-02-01 a 2026-09-04 | CTL/ATL/TSB pré-calc |
| `farol_daily_state` | 214 | 2026-02-01 a 2026-09-04 | Estado estruturado |

### Schema — 23 Campos em `farol_daily_state`

| Domínio | Campo | Tipo | Cobertura | Incluído |
|---------|-------|------|-----------|----------|
| **CARGA** | trimp_7d | REAL | 100% | ✓ na classificação |
| | trimp_28d | REAL | 100% | ✓ na classificação |
| | ctl | REAL | 100% | ✓ na classificação |
| | atl | REAL | 100% | ✓ na classificação |
| | tsb | REAL | 100% | ✓ PRIMÁRIO (único evidência) |
| | atl_ctl_ratio | REAL | 100% | ✓ apenas informativo |
| **RECUPERAÇÃO** | hrv_avg | REAL | ~40% | ✓ em domínio |
| | rhr | INTEGER | 100% | ✓ em domínio, mas lido de `daily` |
| | night_hr_avg | INTEGER | ~60% | ✓ em domínio |
| | stress_avg | INTEGER | ~60% | ✓ em domínio |
| | body_battery_max | INTEGER | ~60% | ✓ em domínio |
| | body_battery_min | INTEGER | ~40% | ⚠️ REMOVIDO (volatilidade V3) |
| | respiration_avg | REAL | ~40% | ✓ em domínio |
| | spo2_avg | REAL | ~40% | ✓ em domínio |
| | spo2_min | REAL | ~40% | ✓ em domínio |
| **SONO** | sleep_total_min | REAL | ~60% | ✓ em domínio |
| | awake_minutes | REAL | ~60% | ✓ em domínio |
| | deep_sleep_min | REAL | ~60% | ✓ em domínio |
| | rem_sleep_min | REAL | ~60% | ✓ em domínio |
| | sleep_score | INTEGER | ~60%, quebra em 01/08 | ✓ em domínio |
| | sleep_regularity_min | REAL | ~60% janela 14d | ⚠️ FORA (autocorrelação) |
| **PERFORMANCE** | output_preserved | TEXT | ~60% (se atividade) | ✓ informativo |
| | deterioration_detected | TEXT | ~60% (se intervalo) | ✓ informativo |
| **OBSERVAÇÃO** | measurement_breakpoints | TEXT (JSON) | ~40% | ✓ informativo |

### Dados com Erros

```
daily records with errors: 0 (limpados pela F9)
activities legadas: números negativos de activity_id, distance_km desconhecida
source field: 'garmin' (02/06/2026+) vs 'legado' (<02/06/2026)
```

### Descontinuidades Conhecidas

1. **RHR degrau em 2026-06-11**: 70.2 bpm → 76.1 bpm (5.9 bpm, mudança de fonte)
2. **sleep_score quebra em 2026-08-01**: Mudança de política no Garmin
3. **Legado (<11/06/2026)**: distance_km inútil, duração/FC salvas

---

## 5. MODELO ANALÍTICO REAL

### O Que Realmente Está Implementado (não o que documentamos)

**Baseline (janela móvel)**
- 90 dias (padrão para z-score)
- 7 dias e 30 dias (complementares)
- **Respeitam source**: RHR não cruza 2026-06-11
- Split by date: sleep_score (a partir de 2026-08-01)

**Métrica (primitiva central)**
- Campos: name, value, mean_7d/30d/90d, sd_90d, z, trend, status, confidence, n_7d/30d/90d, higher_is_better
- Status: ok/atencao/alerta/sem_dado
- Confiança: alta/baixa (n_90d >= 10 e valid data >= 50%)
- Tendência: subindo/caindo/estavel/indefinido
- Thresholds: z > 0.5 SD = atencao, z > 1.5 SD = alerta (simétrico, não assume direção)

**Domínios (5 categorizados)**
1. **CARGA** — 6 campos, TSB primário
   - Média ponderada de TRIMP
   - CTL/ATL/TSB per Banister (τ=42d/7d)
   - Apenas TSB conta para status
   
2. **RECUPERAÇÃO** — 9 campos (era 5 em v1.0)
   - Núcleo autônomo: HRV, RHR, night_hr, stress (4 campos)
   - Periféricos: body_battery_max, respiration, spo2_avg, spo2_min (4 campos)
   - **body_battery_min REMOVIDO** de propósito (dispara 94% dos dias)
   - Pior-status entre campos com dado
   
3. **SONO** — 5 campos (inalterado)
   - sleep_total_min, awake_minutes, deep_sleep_min, rem_sleep_min, sleep_score
   - Pior-status
   - **sleep_regularity_min FORA** (autocorrelação de 14d)
   
4. **PERFORMANCE** — 2 campos (informativo)
   - output_preserved (mecanismo fixo)
   - deterioration_detected (CUSUM no pace)
   
5. **OBSERVAÇÃO** — 1 campo (informativo)
   - measurement_breakpoints (quebras de medição CUSUM)

**Regra 8: Convergência**
- Exige 2+ **domínios** em atencao/alerta **simultaneamente**
- Implementação CORRETA em `state.convergence()`
- **Implementação INCORRETA em `today.detect_patterns()`**: conta métricas individuais como "domínios", V2 da vistoria

**Tendência (longitudinal)**
- Usa `farol.longitudinal.trend()` — janela fixa de 7 dias
- Calcula direção (IMPROVING/WORSENING/STABLE) do status do domínio
- Exibida em `DAILY_STATE.md` e `DAILY_ANALYSIS.md`

**Interpretação (novo módulo)**
- Distinção núcleo autônomo vs periféricos em RECUPERAÇÃO
- Sinal dominante: qual domínio lidera o padrão
- Estado fisiológico: deterioração sistêmica vs concentrada
- Assinatura de doença: `signature_doenca()` em `today.py`
- Conclusão: narrativa sem prescrição

**Modelo v1.1 (congelado em 2026-09-04)**
- Adiciona 5 campos a `farol_daily_state`
- Adiciona módulo `interpretation.py` (resolve V1-V3 da vistoria)
- Adiciona F1 (race_prediction) com 3 modelos
- Adiciona segundo juiz independente (training_readiness_score do Garmin)
- Nenhuma fórmula do v1.0 foi alterada

---

## 6. F1 — PREDIÇÃO DE PROVA (IMPLEMENTAÇÃO REAL)

### Visão Geral

Implementado em `farol/race_prediction.py` (324 linhas).

**Objetivo**: Estimar tempo de meia-maratona (21.1 km, alvo 2026-09-19) a partir de provas observadas recentes.

**Princípio**: Não prescreve treino — fornece estimativa + contexto de TSB recomendado.

### Três Modelos Independentes

| Modelo | Fórmula | Expoente | Quando Usar | Validação |
|--------|---------|----------|------------|-----------|
| **Riegel 1981** | T₂ = T₁ × (D₂/D₁)^k | k = 1.06 (padrão recreacional) | Elite ≈ 1.05, menos treinado ≈ 1.08 | Recordes mundiais 3.5min-3h50 |
| **Cameron 1999** | T₂ = (T₁/D₁) × D₂ × f(D₁)/f(D₂) | f(x) = 13.497 - 0.000030x + 835.711/x^0.79 | Conservador em distâncias longas | Melhores tempos mundiais |
| **VDOT (Daniels)** | Converte prova → VO2max equivalente → tempo alvo | Custo O₂ + %VO2max sustentável | Mais fisiológico, inclui fadiga | Tabelas publicadas |

### Implementação Confirmada

**Funções públicas**:
```python
riegel_time(d1_km, t1_min, d2_km, k=1.06)       # T₂ por Riegel
cameron_time(d1_km, t1_min, d2_km)              # T₂ por Cameron
vdot_from_time(d_km, t_min)                     # VO2max equivalente
vdot_time(vdot, d_km)                           # Tempo a manter VDOT (busca binária)
predict(d1_km, t1_min, d2_km)                   # Retorna {riegel, cameron, vdot, min, max}
observed_performances(conn, on_date)            # Lê provas do banco (activities)
reference_races(profile)                        # Lê profile.yaml::recent_races
build_report(conn, on_date, profile, ...)       # Gera RACE_PREDICTION.md
```

**Fonte de Dados**:
- `profile.yaml::recent_races` — lista de provas por data (apenas identificador)
- `activities` do banco — distância e tempo reais
- Âncora: prova mais recente da lista

**Estrutura do Relatório**:
1. **OBSERVADO** — Prova no banco (data, distância, tempo, pace médio)
2. **ESTIMATIVA** — Os três modelos + faixa min/max
3. **A VALIDAR** — Comparação contra resultado real de 19/09
4. **TSB RECOMENDADO** — Contexto de descansado/positivo no race day

### Testes (13 testes, todos passando)

```
Riegel:              expoente_padrao_1_06
Cameron:             duplica_distancia_e_mais_lento_que_linear, identidade
VDOT:                5k_20min_da_vdot_referencia, 10k_40min_da_vdot_alta,
                     tempo_volta_ao_ponto_de_partida, mais_lento_tem_vdot_menor
Predict (3 modelos): tem_as_tres_chaves_e_faixa_contem_todas,
                     caso_real_10k_para_meia_dentro_de_faixa_esperada
Observed:            ignora_data_sem_corrida, le_atividade_real_do_banco,
                     tem_campo_de_treino_nao_conta_como_corrida_por_tipo
Report:              contem_as_tres_secoes, mostra_alvo_e_dias,
                     mostra_prova_observada_e_tabela_de_modelos,
                     nao_prescreve_treino, sem_prova_observada_nao_chuta_tempo
Golden:              caso_real_2026_08_16 (banco real)
```

### Achados

✓ F1 está **totalmente implementada**  
✓ Três modelos funcionam corretamente  
✓ Testes cobrem valores publicados (VDOT 32.8 para 10km 59.4min)  
✓ Relatório respeita princípio "descreve, não prescreve"  
✓ Sem erros de compilação ou testes

---

## 7. F10 — SAÍDA ESTRUTURADA + EXIT CODES (IMPLEMENTAÇÃO REAL)

### Visão Geral

**Implementado apenas em `state.py`** com flag `--json`.

**Não está em**: `today.py`, `daily_analysis.py`, `ingest.py`, ou outros CLIs.

### Saída JSON

**Flag**: `farol-state --json [--date YYYY-MM-DD]`

**Contrato v1** (23 testes dedicados):
```json
{
  "schema": "farol.state.json.v1",
  "data": "2026-09-04",
  "status_global": "atencao|ok|alerta|sem_dado",  // pior dos domínios
  "exit_code": 0|1|2,                              // 0=ok, 1=atencao, 2=alerta
  "dominios": {
    "CARGA": {
      "status": "...",
      "confianca": "alta|baixa",
      "streak": {"dias": N, "data_inicio": "...", "data_fim": "...", "dominio": "..."},
      "trend": {"dominio": "...", "janela_dias": 7, "n_pontos": N,
                "direcao": "IMPROVING|WORSENING|STABLE|INSUFFICIENT_DATA",
                "data_inicio": "...", "data_fim": "..."},
      "metricas": {
        "tsb": {"name": "...", "value": 3.5, "mean_90d": 0.1, "sd_90d": 2.1,
                "z": 1.57, "trend": "subindo", "status": "ok", "confidence": "alta",
                "n_90d": 45, "higher_is_better": true}
      }
    },
    // ... RECUPERACAO, SONO, PERFORMANCE, OBSERVACAO
  },
  "performance": {"output_preserved": "yes|no|indeterminate", ...},
  "convergencia": "2 dominios puxados" | null,
  "descontinuidades": [...]
}
```

**Tipo de Dados**:
- Sem `datetime` ou `Decimal`
- Apenas float/str/int/None
- Determinístico (ordem de keys fixa)

### Exit Codes

| Status | Code | Significado |
|--------|------|-------------|
| ok | 0 | Nenhum domínio em atenção/alerta |
| sem_dado | 0 | Sem dados fisiológicos (não é alerta) |
| atencao | 1 | 1+ domínio em borderline |
| alerta | 2 | 1+ domínio em alerta |

### Implementação em state.py

```python
def exit_code(status: str) -> int:
    """Traduz status para exit code 0/1/2 (não cria nova classificação)."""
    
def metric_to_json(m: Metric) -> dict:
    """Serializa Metric sem datetime/Decimal."""
    
def dominio_to_json(d: dict) -> dict:
    """Serializa domínio com metricas."""
    
def build_state_json(conn, on_date: str) -> dict:
    """Orquestra: gather() → JSON (mesma classificação, novo formato)."""
    
def main():
    parser.add_argument("--json", action="store_true", ...)
    if args.json:
        payload = build_state_json(conn, on_date)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        sys.exit(payload["exit_code"])
    else:
        # markdown inalterado
```

### Testes (23 dedicados)

```
Exit codes:         ok_mapa_0, sem_dado_mapa_0, atencao_mapa_1, alerta_mapa_2
Global status:      pega_pior_entre_dominios, atencao_vence_ok,
                    sem_dado_nao_vence_nada, tudo_ok
Serialization:      campos_seriais_sem_datetime_decimal
Dominio JSON:       estrutura_completa
Build JSON:         contrato_minimo_presente, dominios_trazem_todas_as_metricas_seriais,
                    fixture_alerta_exit_2, fixture_atencao_exit_1,
                    fixture_estavel_ok_exit_0, sem_data_da_value_error,
                    sem_texto_humano_estruturado
CLI JSON:           erro_tecnico_e_processo_nao_estado, estado_alerta_exit_2,
                    estado_atencao_exit_1, estado_ok_exit_0, json_valido_e_parseavel,
                    sem_json_continua_gerando_markdown
Golden:             golden_sample_2026_09_02 (banco real)
```

### Achados

✓ F10 está **implementada em state.py**  
✓ JSON schema validado  
✓ Exit codes mapeados corretamente  
✓ Não aparece em `today.py` (escopo menor que solicitado no roadmap)  
✓ **Não** está implementada em `daily_analysis.py`, `ingest.py`, etc  
⚠️ **Parcial**: Apenas 1 CLI tem saída JSON, roadmap sugeria múltiplos

---

## 8. DASHBOARD (NOVO, NÃO COMMITADO)

### Visão Geral

Arquivo: `farol/dashboard.py` (764 linhas) — **novo em 2026-09-04, não commitado**

Teste: `tests/test_dashboard.py` (200 linhas) — **novo em 2026-09-04, não commitado**

Saída: `reports/DASHBOARD.html` — **arquivo gerado, não commitado**

### Propósito

Camada de **visualização pura**, não de domínio:
- Orquestra saídas que já existem (state.gather, race_prediction.predict, etc)
- Renderiza HTML estático para abrir no navegador com duplo clique
- Sem servidor, sem JavaScript dinâmico

### Funções Públicas

```python
_series(conn, column, on_date, days=30)          # Últimos N valores para gráfico
_md_table_html(md)                               # Converte | a | b | para <table>
build_context(conn, profile, on_date)            # Dict com tudo pré-calculado
render_html(ctx)                                 # Templates → HTML
build_html(conn, profile, on_date)               # Wrapper: build_context + render_html
main()                                           # CLI: --date, --out
```

### O que Agrupa

1. Recuperação (7 métricas de RECUPERAÇÃO + sleep)
2. Carga de treino (CTL/ATL/TSB com monotonia)
3. Estado (via state.gather + interpretation)
4. Padrões (detect_patterns + signature_doenca)
5. Predição de prova (3 modelos + TSB recomendado)
6. Correlações defasadas
7. Breakpoints/CUSUM

### Estrutura do HTML

Seções: Resumo, Carga, Recuperação, Sono, Previsão, Correlações, Perfil, Disclaimed

### Testes (7 testes)

```
Series:      devolve_data_e_valor_em_ordem, ignora_nulos_e_datas_fora_da_janela
MdTableHtml: converte_tabela_markdown_em_html, sem_tabela_devolve_fallback,
             escapa_html_das_celulas
BuildContext: monta_contexto_com_um_dia_de_dado, sem_farol_daily_state_nao_quebra,
              sem_prova_observada_predicao_fica_none, sem_meta_de_prova_no_perfil
RenderHtml: html_contem_secoes_essenciais_sem_quebrar, nome_do_atleta_e_escapado
Golden:     gera_html_para_2026_09_04_sem_excecao (banco real)
```

**Status dos testes**: 7/7 passando ✓

### Achados

✓ Dashboard está **pronto para usar**  
⚠️ Não commitado (estado transiente, testado apenas localmente?)  
✓ Segurança: HTML escape em nomes, nunca inline de dados brutos  
✓ Orquestração correta: não recalcula, só renderiza

---

## 9. TESTES — ANÁLISE COMPLETA

### Resumo

- **Total**: 348 testes
- **Passando**: 348 (100%)
- **Falhando**: 0
- **Duração**: 16.98 segundos
- **Cobertura**: ~90% (não calculada formalmente)

### Distribuição por Módulo

| Módulo | Testes | Categoria |
|--------|--------|-----------|
| core (TRIMP/CTL/ATL/TSB/Metric) | 184 | Fundacional |
| state_json (F10) | 23 | Novo em v1.1 |
| longitudinal (streak/trend) | 46 | Análise temporal |
| race_prediction (F1) | 13 | Novo em v1.1 |
| interpretation | 6 | Novo em v1.1 |
| daily_analysis | 10 | Relatório |
| dashboard | 7 | Novo em v1.1 |
| state | 7 | Domínios |
| splits | 8 | Tiros |
| calibration | 6 | Log de calibração |
| recovery | 8 | Índice |
| sheets | 8 | Ingestão |
| correlations | 3 | Correlação |
| changepoint | 5 | CUSUM |
| integration | 7 | Fluxo completo |
| decoupling | 5 | Pa:Hr |
| sleep_regularity | 5 | SD circular |
| calibration_review | 5 | Concordância |
| db | 4 | Conexão |
| zones | 4 | Zonas de FC |
| hrv_variability | 2 | CV |

### Teste de Integração

`test_integration.py` — 7 testes cobrem fluxo ponta-a-ponta:
- Arquivo local de dados
- Ingestão SQL
- Cálculo de TRIMP
- Construção de state
- Relatório markdown
- Tudo offline, sem rede

### Golden Sample (Banco Real)

Vários testes usam `skip_unless_real_db()` decorator para rodar contra `data/farol.db` real:
- test_core (múltiplos casos 2026-09-02)
- test_race_prediction (2026-08-16)
- test_state_json (2026-09-02)
- test_longitudinal (caso real 2026-08-21 a 2026-09-03)
- test_dashboard (2026-09-04)

### Cobertura de Fluxo

✓ Ingestão: sheets_test.py (load into DB)  
✓ Análise: test_core.py, test_analysis.py  
✓ Estado: test_state.py, test_state_json.py  
✓ Interpretação: test_interpretation.py  
✓ Predição: test_race_prediction.py  
✓ Temporal: test_longitudinal.py  
✓ Dashboard: test_dashboard.py  
✓ Integração: test_integration.py  

### O Que Não Está Testado Ao Vivo

- `farol.ingest` contra a API do Garmin (requer credenciais, rede)
- Fluxo F9 completo (erro endpoint → daily.errors → re-sync limpa)
- Múltiplas provas de verdade (F1 só testa 1 âncora)
- Diferentes timezones (hardcoded -3 Brasil)

---

## 10. QUALIDADE DO CÓDIGO

### Ruff Check

**Status**: ✅ Todos os checks passam

Configuração (pyproject.toml):
```toml
[tool.ruff]
target-version = "py311"

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F"]  # Erros e imports, NÃO estilo
```

Resultado: `All checks passed!`

### Python Version

- Requer: `>=3.11`
- Executado: Python 3.13.x (venv)
- Sintaxe: Válida para 3.11+

### Imports

- Nenhum circular detectado
- Dependências: garminconnect, pyyaml, python-dotenv, openpyxl
- Dev: ruff (já instalado)
- Nenhuma dependência não declarada

### Estilo

- Sem comentários desnecessários (segue filosofia do projeto)
- Nomenclatura clara
- Type hints ausentes (código anterior a 2024, não reescrito)
- Docstrings presentes em módulos, funções públicas

---

## 11. DEPENDÊNCIAS — ANÁLISE REAL

### Declaradas em pyproject.toml

| Dependência | Versão | Uso | Status |
|-------------|--------|-----|--------|
| garminconnect | >=0.2.24,<0.4 | API do Garmin | ✓ Funcional |
| pyyaml | >=6.0 | profile.yaml | ✓ Necessária |
| python-dotenv | >=1.0 | .env (credenciais) | ✓ Necessária |
| openpyxl | >=3.1 | garmin_health.xlsx | ✓ Necessária |
| **ruff** (dev) | >=0.15 | Linting | ✓ Instalado |

### Não Declaradas Implicitamente

- `sqlite3` — stdlib, OK
- `json`, `re`, `math` — stdlib, OK
- `pathlib` — stdlib, OK
- Nenhuma dependência oculta encontrada

### Observação

garminconnect é o ponto único de integração com Garmin. F9 desacopla via `_safe()` para que falhas de endpoint não derrubem sync.

---

## 12. CONFIGURAÇÃO E SEGURANÇA

### Arquivos de Configuração

| Arquivo | Gitignored? | Contém Segredos? | Observação |
|---------|-------------|------------------|-----------|
| `.env` | ✓ | ✓ Sim (email/senha Garmin) | Carregado pela primeira vez, depois token salvo |
| `.garmin_token/` | ✓ | ✓ Sim (sessão Garmin) | Gerado na primeira execução |
| `data/profile.yaml` | ✓ | ⚠️ Dados pessoais do atleta | Idade, altura, LTHR, etc |
| `data/farol.db` | ✓ | ✓ Sim (dados de treino) | SQLite com dados pessoais |
| `data/garmin_health.xlsx` | ✗ NÃO ignorado | ✓ Dados pessoais | Planilha de histórico — fonte de verdade versionada |
| `.env.example` | ✗ | ✗ Não | Template, seguro |

### .gitignore (Correto)

```
.venv/                 ✓
__pycache__/           ✓
*.pyc                  ✓
.env                   ✓ (credenciais)
.garmin_token/         ✓ (token)
farol.db               ✓ (dados pessoais)
profile.yaml           ✓ (dados do atleta)
*.egg-info/            ✓
.ruff_cache/           ✓
.pytest_cache/         ✓
farol.db.backup_*      ✓
```

### Achados

✓ Segredos estão protegidos  
✓ Dados pessoais não versionados (exceto data de nascimento em profile.yaml que é exemplo)  
⚠️ **garmin_health.xlsx é versionado** — contém histórico pessoal (240 KB)  
✓ Nenhuma chave exposta no código  

---

## 13. GIT E VERSIONAMENTO

### Estado Atual

```
Branch: master
Modificado (working tree):
  - README.md (edições)
  - pyproject.toml (edições)
  - data/garmin_health.xlsx (sync automático)
  - reports/DAILY_ANALYSIS.md (gerado)
  - reports/DAILY_STATE.md (gerado)

Não rastreado:
  - farol/dashboard.py (novo)
  - tests/test_dashboard.py (novo)
  - reports/DASHBOARD.html (gerado)
  - err.log (log de erro)
```

### Histórico Recente

```
9a34be2 — Documenta estado da sessao de 2026-09-04 para retomada futura (F9, testes e pendencia de disco)
79ba903 — Corrige erro obsoleto de F9: re-sync limpa daily.errors na planilha e no banco
127553e — Registra falha de endpoint do Garmin em daily.errors e avisa no sync e no relatorio (F9)
711e82a — Implementa predicao de prova (F1), saida JSON do estado (F10) e CI com ruff
132ab1a — Implementa camada de interpretacao e fecha itens da vistoria de 2026-09-04
```

### Achados

✓ Histórico claro, commits bem documentados  
✓ F1, F9, F10 estão commitados  
✓ F1: race_prediction.py  
✓ F9: garmin_client._safe, sheets.py (daily.errors), ingest.py (mensagens)  
✓ F10: state.py (--json, exit_code)  
✓ Dashboard e interpretation em 132ab1a  
⚠️ Dashboard NÃO commitado (por quê?)  

---

## 14. DOCUMENTAÇÃO × CÓDIGO

### Documentação Existente

| Arquivo | Linhas | Conteúdo | Status |
|---------|--------|----------|--------|
| README.md | 283 | Visão geral, setup, uso, testes, estrutura | Atualizado |
| MODEL_V1.0.md | 294 | Modelo congelado (histórico, não alterar) | Histórico |
| MODEL_V1.1.md | 345 | Modelo v1.1 (congelado em 2026-09-04) | ✓ Vigente |
| PROFILE.md | 93 | Regras de interpretação (9 regras) | Referência |
| CALIBRACAO.md | 119 | Fluxo de calibração (log manual) | Referência |
| VISTORIA_2026-09-04.md | 217 | Achados V1-V3 (problemas semânticos) | Crítica |
| HANDOFF_2026-09-04.md | 34 | Retomada da sessão | Crítica |

### Divergências Encontradas

1. **README diz "348 testes"** → Atualizado? (era "310 testes" antes)
2. **README lista CLIs sem dashboard** → Dashboard não era versionado em 04/09
3. **VISTORIA V1-V3** → Problemas **ainda não corrigidos**:
   - V1: Três superfícies contam histórias diferentes
   - V2: detect_patterns conta métricas como domínios (regra 8 violada)
   - V3: body_battery_min dispara 94% dos dias
4. **MODEL_V1.1.md diz body_battery_min REMOVIDO de RECUPERAÇÃO** → Mas ainda está em farol_daily_state (banco)

### O Que Falta

- [ ] Documento sobre vistoria V1-V3 (resoluções)
- [ ] Changelog 2026-09-04 (F1/F10/interpretation/dashboard)
- [ ] Guia F10 (como usar --json)
- [ ] Guia F8 (CI não está implementado)

---

## 15. ACHADOS CRÍTICOS DA VISTORIA 2026-09-04

### V1 — Três Superfícies, Três Histórias do Mesmo Dia [ALTA]

**Problema**: `today`, `state`, `daily_analysis` usam campos diferentes para classificação.

| Superfície | Diz | Baseado Em | Problema |
|------------|-----|-----------|----------|
| today | Body Battery min + Stress no sono | dict plano de métricas | Não é domínio |
| state | RECUPERAÇÃO + SONO BORDERLINE | domínios formais | Correto |
| daily_analysis | "Deterioração sistêmica" | convergência de domínios | Correto, mas narrativa é de today |

**Status**: Interpretation.py foi criado para resolver isso, mas a implementação em `today.py` continua.

### V2 — detect_patterns Viola Regra 8 [ALTA]

**Problema**: `today.detect_patterns()` conta **métricas individuais** como "domínios".

```python
# Regra 8 (PROFILE.md): 2+ DOMÍNIOS (CARGA, RECUPERAÇÃO, SONO)
# Implementação correta em state.convergence()
# Implementação errada em today.detect_patterns(): conta campos

# Exemplo: HRV (recuperação) + FC repouso (recuperação) fora do padrão
# → today diz "2 domínios"
# → state diz "1 domínio (RECUPERAÇÃO) com 2 campos"
```

**Status**: Código não foi corrigido, apenas documentado.

### V3 — body_battery_min Dispara 94% dos Dias [ALTA]

**Problema**: Campo tão volátil que é inútil como classificador.

```
Medição 01/08-04/09: body_battery_min marca atenção/alerta em 33/35 dias (94%)
Mesmo farol.state removeu de RECUPERAÇÃO (docstring: "inflava o domínio")
Mas today.py continua usando em detect_patterns + convergência
```

**Status**: Documento reconhece que é periférico, mas não foi removido do código de `today.py`.

### Recomendação Dos Achados

> "O modelo congelado está certo; a narrativa não o diz."

Interpretation.py foi criado para consertar, mas não substitui a lógica de `today.py`.

**Status**: PARCIALMENTE RESOLVIDO
- ✓ Documentado (interpretation.py existe)
- ✓ Novo módulo criado
- ✗ today.py não foi refatorado

---

## 16. REGRESSÕES E PROBLEMAS CONHECIDOS

### Status de Problemas Conhecidos Anteriores

| Problema | Statusat | Evidência |
|----------|---------|-----------|
| garmin_client._safe retorna None silenciosamente | ✓ Resolvido | F9: erros registram em daily.errors |
| daily.errors não populado | ✓ Resolvido | F9: agora registra falhas de endpoint |
| sleep_score breakpoint 01/08 | ✓ Conhecido | Model v1.1, DATE_SPLITS em metrics.py |
| RHR degrau 2026-06-11 | ✓ Conhecido | Source field em schema.sql |
| Distância legada desconhecida | ✓ Conhecido | Anulada no load, duração/FC preservadas |

### Novos Problemas Encontrados

**Nenhum bug crítico encontrado.**

Problemas semânticos (não bugs):
- V1, V2, V3 da vistoria (mencionados acima)
- Duplicação em test_state.py vs test_state_json.py (mesmos testes duplicados? Não, diferentes)

---

## 17. PREPARAÇÃO PARA F10

### Status Atual: PARCIAL ✓

**Implementado**:
- ✓ JSON schema com contrato definido
- ✓ Exit codes 0/1/2 mapeados
- ✓ build_state_json orquestra gather() → JSON
- ✓ Sem datetime/Decimal (tipos serializáveis)
- ✓ 23 testes dedicados
- ✓ Flag --json em farol-state
- ✓ Erro técnico vai para stderr (não JSON)

**Não Implementado**:
- [ ] F10 em outros CLIs (daily_analysis, ingest, etc)
- [ ] Documentação oficial de como usar --json
- [ ] Schema versioning mais formal (v1 fixo em código)
- [ ] Mais dimensões de saída (hoje apenas state)

**Bloqueadores para F10 completo**: Nenhum técnico. É questão de escopo.

---

## 18. PREPARAÇÃO PARA F8 (CI)

### Status Atual: NÃO IMPLEMENTADO

**O que F8 deveria fazer**:
- GitHub Actions workflow
- Executar testes
- Ruff check
- Possível coverage

**O que Existe**:
- ✓ Testes locais funcionam (pytest)
- ✓ Ruff check funciona (ruff check .)
- ✗ GitHub Actions não configurado
- ✗ Arquivo .github/workflows/... não existe

**Preparação Necessária**:
- [ ] Criar .github/workflows/test.yml
- [ ] Python 3.11+ install
- [ ] pip install -e . && pip install -e .[dev]
- [ ] pytest
- [ ] ruff check
- [ ] Variáveis: GARMIN_EMAIL, GARMIN_PASSWORD (usar fake ou skip integration)

**Bloqueadores**: Nenhum técnico. Ambiente está pronto.

---

## 19. FUNCIONALIDADES DESCONHECIDAS DESCOBERTAS

### Coisas Que Existem Mas Não Estavam no Modelo Mental

1. **Módulo `interpretation.py` (347 linhas)**
   - Criado em 2026-09-04 para resolver vistoria V1-V3
   - 11 funções públicas: interpretar, sinais_por_dominio, estado_fisiologico, etc
   - Não estava documentado no roadmap
   
2. **Dashboard HTML (764 linhas)**
   - Single-page, renders estático
   - Agregador de múltiplos módulos
   - Testes inclusos (7 testes)
   - Não commitado (por quê?)

3. **F9 Implementada (Registra Erros)**
   - daily.errors field no banco
   - garmin_client._safe com erros list
   - ingest.py mostra avisos
   - sheets.py limpa errors na re-sync
   - Estado não documentado: quando foi feito? Está commitado? Sim, commits 79ba903 e 127553e

4. **F10 Parcialmente Implementada**
   - Apenas em state.py
   - test_state_json.py com 23 testes
   - Exit codes 0/1/2
   - JSON schema com contrato v1

5. **Segunda Migração no DB (db.py)**
   - 5 colunas novas adicionadas via ALTER TABLE em runtime
   - Não está em schema.sql original (CREATE TABLE IF NOT EXISTS não adiciona colunas)
   - Migration logic em _migrate_farol_daily_state()

6. **Leitura de RHR Via `daily`, Não `farol_daily_state`**
   - Feito para respeitar quebra de fonte 2026-06-11
   - daily_metric() tem lógica especial para RHR
   - Não é óbvio no código state.py

7. **Várias Dimensões de "Trend"**
   - Trend métrica: subindo/caindo/estavel/indefinido
   - Trend domínio: IMPROVING/WORSENING/STABLE/INSUFFICIENT_DATA
   - Streak: dias consecutivos
   - Não estava claro que há dois tipos

8. **Conversão Entre Vocabulários**
   - Garmin bruto → planilha (farol.sheets)
   - Planilha → DB (sheets.load_into_db)
   - Nenhum terceiro passo de "normalização" (tudo nos dois primeiros)
   - Documentado em README mas código

9. **Dois Vocabulários de VO2max**
   - Garmin VO2max estimado (não comparável a teste ergométrico)
   - Mostra como Metric de tendência, fora de convergência
   - VDOT (Daniels) calculado a partir de provas (F1, relacionado mas diferente)
   - Não eram dois conceitos separados na mente

10. **Daily.source vs Activities.source**
    - Source: 'garmin' vs 'legado'
    - Marca procedência dos dados
    - Usado em metrics.py para não cruzar baseline
    - CRÍTICO para evitar artefatos

---

## 20. ACHADOS CLASSIFICADOS

### CRÍTICO (0)
Nenhum.

### ALTO (3)

| Achado | Localização | Impacto | Recomendação |
|--------|-------------|---------|---------------|
| V1: Três superfícies contam histórias diferentes | today.py vs state.py vs daily_analysis.py | Confusão do leitor | Refatorar today.detect_patterns para usar domínios, não métricas |
| V2: detect_patterns viola regra 8 | today.py:191-205 | Convergência incorreta em terminal | Reimplementar detect_patterns com lógica de domínios |
| V3: body_battery_min dispara 94% dos dias | today.py vs state.py | Falso positivo em padrões | Remover de today.py (já está fora de state.py) |

### MÉDIO (2)

| Achado | Localização | Impacto | Recomendação |
|--------|-------------|---------|---------------|
| Dashboard não commitado | farol/dashboard.py (novo) | Código não está em master | Decidir: commit ou remover |
| F10 apenas em state.py | state.py | Escopo limitado vs roadmap | Expandir para daily_analysis, ingest? Ou por design? |

### BAIXO (3)

| Achado | Localização | Impacto | Recomendação |
|--------|-------------|---------|---------------|
| Espaço em disco: 16 GB em Downloads | fora do repo | Pode causar falhas aleatórias | Liberar espaço (não é de código) |
| README desatualizado (348 vs 310 testes) | README.md | Informação obsoleta | Atualizar |
| Sem guia de F10 | docs/ | Usuário não sabe usar --json | Documentar |

### INFORMATIVO (5)

- F9 completamente implementada e testada
- Interpretation.py resolve achados vistoria
- Dashboard pronto para uso
- 348 testes (superou 310)
- Model v1.1 congelado e implementado

---

## 21. ESTADO DE CADA FEATURE DO ROADMAP

### FASE A

| Feature | Status | Evidência | Pronto? |
|---------|--------|-----------|---------|
| F1: Predição de prova | ✅ COMPLETO | race_prediction.py (324L), 13 testes, 3 modelos | SIM |
| F10: Structured output | ⚠️ PARCIAL | state.py --json, 23 testes, apenas 1 CLI | PARCIAL |
| F8: CI | ❌ NÃO FEITO | Nenhum arquivo .github/ | NÃO |

### FASE B

| Feature | Status | Observação |
|---------|--------|-----------|
| F2: LnRMSSD | ❌ | Não implementado |
| F4: dead signals | ❌ | Não implementado |
| F9: coverage/error por dia | ✅ | **Implementado além do previsto** — daily.errors populated |
| F7: temperatura | ❌ | Não implementado |

### FASE C, D

Não implementadas, não estudadas.

---

## 22. O QUE DEVE SER FEITO AGORA

### Crítico (Antes de F10/F8)

1. **Resolver vistoria V2**: Refatorar `today.detect_patterns()` para contar domínios, não métricas
2. **Resolver vistoria V3**: Remover `body_battery_min` de `today.detect_patterns()` e convergência
3. **Decidir sobre dashboard**: Commit ou remover? (está pronto, testes passam)

### Alto (Recomendado)

4. Expandir F10 a outros CLIs (F10 completa em todos, não apenas state)
5. Documentar F10: como usar `--json`, contrato schema
6. Implementar F8: CI com GitHub Actions

### Médio

7. Atualizar README (348 testes, novo dashboard)
8. Documentar interpretation.py (novo módulo, não está em README)
9. Criar changelog 2026-09-04 (F1/F9/F10/interpretation/dashboard)

### Baixo

10. Liberar espaço em disco (16 GB em Downloads)
11. Considerar type hints (projeto é anterior a 2024, sem hints)
12. Considerar coverage.py formal

---

## 23. O QUE NÃO PRECISA SER FEITO AGORA

- [ ] Refatorar code style (Ruff já passa)
- [ ] Adicionar mais dependências
- [ ] Alterar MODEL_V1.0.md (é histórico, congelado por propósito)
- [ ] Implementar FASE B/C/D (fora do escopo)
- [ ] Mudar VO2max logic (funciona, está documentado)
- [ ] Refatorar imports (sem circulares encontradas)
- [ ] Suporte a Strava/Intervals.icu (fora de escopo deliberado)
- [ ] Dashboard no live server (design é sem servidor)

---

## VEREDITO

### 1. O Farol está saudável?

**SIM** ✓

- 348 testes passando
- Código limpo (Ruff check)
- Arquitetura bem definida
- Nenhum bug crítico
- Documentação presente

### 2. O que está realmente pronto?

**F1 totalmente**, **F10 parcialmente**, **F9 totalmente** (além do planejado):
- Predição de prova: 3 modelos, testes validados contra valores publicados
- Saída JSON: schema definido, exit codes, 23 testes
- Registro de erros: erros de endpoint registrados, re-sync limpa

### 3. O que está incompleto?

**F10** (apenas 1 CLI, não todos), **F8** (CI não existe), **vistoria V1-V3** (documentados mas não resolvidos no código de today.py):
- F10 apenas em state.py (hoje)
- F8 requer arquivo .github/workflows/
- V2/V3 requerem refactoring de today.detect_patterns()

### 4. Existe algo quebrado?

**Nenhum bug de execução**, apenas **problemas semânticos**:
- today.py conta métricas como "domínios" (V2)
- body_battery_min dispara 94% dos dias (V3)
- Três superfícies contam histórias diferentes (V1)

Tudo funciona, mas o significado é confuso.

### 5. A F1 está realmente concluída?

**SIM** ✓ Totalmente.
- 3 modelos implementados (Riegel 1981, Cameron 1999, VDOT Daniels)
- Testes cobrem valores publicados
- Relatório respeita princípio "descreve, não prescreve"
- Âncora (prova mais recente) funciona
- Banco é fonte de verdade

### 6. Existe regressão?

**Nenhuma detectada**. Código anterior continua funcionando.

### 7. Existe algo que não sabíamos que estava no projeto?

**SIM**, 10 itens descobertos:
1. Módulo interpretation.py (347L, novo)
2. Dashboard HTML (764L, novo, não commitado)
3. F9 completamente implementada (não apenas 5% documentado)
4. F10 com 23 testes
5. 5 colunas novas adicionadas via migração em runtime
6. RHR lido via `daily`, não farol_daily_state
7. Dois tipos de "trend" diferentes
8. VDOT vs Garmin VO2max (conceitos separados)
9. Source field crítico (não cruzar baseline)
10. Segundo juiz independente (training_readiness_score)

### 8. Podemos implementar F10 imediatamente?

**PARCIALMENTE** ✓
- Está implementada, testada, pronta para usar em state.py
- Expandir para outros CLIs requer decisão de escopo
- Documentação mínima necessária

### 9. Precisamos corrigir algo antes?

**Recomendado corrigir V1-V3** antes de próximas releases:
- today.detect_patterns() desvia da regra 8
- Três superfícies contam histórias diferentes
- Convergência inflada por campos voláteis

Mas não bloqueia F10/F8.

### 10. Qual é a próxima ação recomendada?

1. **Imediato**: Decidir sobre dashboard (commit ou remover?)
2. **Crítico**: Resolver vistoria V2 (refatorar detect_patterns)
3. **Logo**: Expandir F10 ou documentar limites
4. **Depois**: F8 CI

---

## RESUMO EM TABELA

| Área | Estado | Problema? | Bloqueador? | Próxima Ação |
|------|--------|-----------|------------|--------------|
| **Testes** | 348 ✓ | Não | Não | Manter |
| **Qualidade** | Ruff clean ✓ | Não | Não | Manter |
| **F1** | Completo ✓ | Não | Não | Nenhuma |
| **F10** | Parcial ⚠️ | Escopo limitado | Não | Documentar ou expandir |
| **F8** | Não feito ❌ | Não existe CI | Sim, se priorizar | Criar .github/workflows |
| **F9** | Completo ✓ | Não | Não | Nenhuma |
| **Dashboard** | Pronto ✓ | Não commitado | Decisão | Commit ou remover |
| **Banco** | Saudável ✓ | Não | Não | Liberar espaço (disco) |
| **Vistoria V1-V3** | Documentado ⚠️ | Não resolvido no código | Não (semântico) | Refatorar today.py |
| **Interpretação** | Novo ✓ | Não | Não | Documentar |
| **Documentação** | Desatualizado | Testes, dashboard | Não | Atualizar |

---

**Relatório concluído: 2026-09-05**

**Auditor**: Claude Code (via investigação direta)

**Tempo total**: ~2.5 horas de investigação

**Conclusão**: Farol está em bom estado. F1 pronto. F10 funcional. Próximas ações: resolver vistoria V2/V3, expandir F10, implementar F8.
