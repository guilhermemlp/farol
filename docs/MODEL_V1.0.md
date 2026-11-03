# Modelo Analítico Farol v1.0

**Data de congelamento**: 2026-09-03  
**Referência**: relatorio_2026-09-02-v1.0.md  
**Status**: Golden Sample - Congelado para desenvolvimento

**Sucedido por `MODEL_V1.1.md`** (2026-09-04) — este documento fica congelado
exatamente como estava; nenhuma seção abaixo foi editada. Ver v1.1 para o modelo em
vigor (RECUPERAÇÃO ampliada, segundo juiz, distinção doença/fadiga, correlações,
detecção de quebra de medição).

---

## Visão Geral

O modelo analítico v1.0 estrutura a avaliação do estado do atleta em **5 domínios conceituais**, cada um com suas próprias métricas, baselines, e interpretações. Essa estrutura evita duplicação lógica e torna cada domínio auditável independentemente.

**Razão dessa organização**: Métricas derivadas da mesma série (e.g., CTL, ATL, TSB todas de TRIMP) não devem contar como múltiplas evidências independentes. Estruturar em domínios força a análise a examinar cada domínio como um único sinal, impedindo contagem dupla.

---

## Os 5 Domínios

### 1. DOMÍNIO CARGA (6 campos)

**Fonte**: `training_load` table + aggregations from `activities`

**Campos**:
| Campo | Tipo | Fórmula/Origem |
|-------|------|----------------|
| `trimp_7d` | REAL | SUM(trimp) over last 7 days from activities |
| `trimp_28d` | REAL | SUM(trimp) over last 28 days |
| `ctl` | REAL | Chronic Training Load (Banister, τ=42 days) |
| `atl` | REAL | Acute Training Load (Banister, τ=7 days) |
| `tsb` | REAL | Training Stress Balance = CTL − ATL |
| `atl_ctl_ratio` | REAL | ATL / CTL (ratio of acute to chronic) |

**Baseline e Z-score**:
- Baseline de 90 dias: média de CTL, ATL, TSB
- Z-score: (valor − média_90d) / σ_90d
- **Nota importante**: TSB, ATL, CTL são **derivados de uma única série** (TRIMP). Todos aumentam/diminuem juntos. Não contar como 3 evidências — é **1 domínio**.

**Interpretação**:
- **TSB > +2σ**: Descansado, pronto (low fatigue)
- **TSB ∈ [−0.5σ, +2σ]**: Normal
- **TSB < −0.5σ**: Fatigado/carregado
- **ATL/CTL > 1.0**: Carga aguda supera crônica (treino intenso)
- **Monotonia** (Foster): < 2.0 bom (contraste diário); > 2.0 repetitivo (risco)

**Anomalia esperada**: Este domínio naturalmente oscila com ciclo de treino. Valores altos não significam doença — podem significar plano de treino apropriado.

---

### 2. DOMÍNIO RECUPERAÇÃO (5 campos)

**Fonte**: `daily` table + `hrv` table

**Campos**:
| Campo | Tipo | Fórmula/Origem |
|-------|------|----------------|
| `hrv_avg` | REAL | daily.hrv_avg (milliseconds) |
| `rhr` | INTEGER | daily.rhr (beats per minute) |
| `night_hr_avg` | INTEGER | daily.night_hr_avg (bpm, averaged over sleep) |
| `stress_avg` | INTEGER | daily.stress_avg (0-100 scale) |
| `body_battery_max` | INTEGER | daily.body_battery_max (0-100, overnight max) |

**Baseline e Z-score**:
- Baseline de 90 dias: média de cada métrica
- Z-score: (valor − média_90d) / σ_90d
- **Direção de melhora**: HRV higher is better, RHR/night_hr lower is better, stress lower is better

**Descontinuidade conhecida** ⚠️:
- **RHR**: Quebra estrutural em 2026-06-11 (+5.9 bpm jump due to pipeline change)
  - Baseline deve ser calculada separadamente por `source` field
  - Nunca comparar valores pré-06-11 com pós-06-11 diretamente
  - **Política**: Use `from_daily(..., respect_source=True)` em código

**Interpretação**:
- **HRV < baseline**: Sugestivo de supressão autonômica (sistema nervoso simpático aumentado)
- **RHR elevado & crescente**: Indicador de fadiga sistêmica
- **Night HR em queda**: Recuperação progredindo
- **Stress baixo**: Sistema parassimpático dominante (bom para recuperação)
- **Convergência** (múltiplas métricas ruins): Padrão de deterioração autonômica

**Peso no recovery_index**: 30% HRV + 20% RHR + 30% sleep_score (fórmula em profile.yaml)

---

### 3. DOMÍNIO SONO (5 campos)

**Fonte**: `daily` table (sleep columns)

**Campos**:
| Campo | Tipo | Fórmula/Origem |
|-------|------|----------------|
| `sleep_total_min` | REAL | daily.total_sleep_minutes (duration) |
| `awake_minutes` | REAL | daily.awake_minutes (time spent awake while in bed) |
| `deep_sleep_min` | REAL | daily.deep_sleep_minutes |
| `rem_sleep_min` | REAL | daily.rem_sleep_minutes |
| `sleep_score` | INTEGER | daily.sleep_score (0-100 qualitative score) |

**Baseline e Z-score**:
- Baseline de 90 dias: média de cada métrica
- Z-score: (valor − média_90d) / σ_90d
- **Direção de melhora**: More is better for duration, deep, REM; less is better for awake

**Descontinuidade conhecida** ⚠️:
- **Awakenings** (daily.awakenings): Mudança de definição em 2026-08-01 (Garmin API update)
  - Antes: ~2.3 despertares/noite (n=49)
  - Depois: ~44.5 despertares/noite (n=31) — 19× jump, não mudança fisiológica
  - **Política**: Split baseline at 2026-08-01; never compare across boundary
  - **Nota**: Correlação com sleep_score é r=−0.097 (praticamente nula) — o campo não explica qualidade

**Cobertura de dados**:
- ~60% de cobertura de dias com sono (nem todas as noites medem)
- Quando ausente: confidence='baixa', não compare com baseline

**Interpretação**:
- **Duração < baseline**: Recuperação deficiente
- **Arquitetura normal**: Deep 15-20%, REM 10-15%, light 60-70%
- **Awake_min elevado**: Fragmentação (eficiência de sono reduzida)
- **Sleep_score sozinho não significa muito**: Correlação fraca com métricas objetivas; usar com duração/arquitetura

---

### 4. DOMÍNIO PERFORMANCE (2 campos)

**Fonte**: `activities` table + split analysis

**Campos**:
| Campo | Tipo | Interpretação |
|-------|------|----------------|
| `output_preserved` | TEXT | 'yes' / 'no' / 'indeterminate' |
| `deterioration_detected` | TEXT | 'yes' / 'no' / 'indeterminate' |

**Fórmula**:
- **output_preserved**: Compare output (pace, power, HR zones) vs recent pattern
  - 'yes' se output dentro de ±5% do padrão dos últimos 3 meses
  - 'no' se output cai > 5%
  - 'indeterminate' se sem dados suficientes

- **deterioration_detected**: Procurar por sinais de degradação sob fadiga
  - HR drift entre reps (começo vs fim)
  - Queda de cadência sob fadiga
  - GCT/oscilação aumentando
  - Precisão de pace piorando (CV aumentando)
  - 'yes' se qualquer sinal presente
  - 'no' se sem sinais
  - 'indeterminate' se sem sessão intervalada

**Interpretação**:
- Se **TSB baixo** (muito fadiga) E **output_preserved=yes**: Carga absorvida, capacidade intacta
- Se **TSB baixo** E **output_preserved=no**: Risco de overreach/overtraining
- Se **TSB baixo** E **deterioration_detected=yes**: Supressão neuromuscular (bandeira vermelha)

---

### 5. DOMÍNIO OBSERVAÇÃO (1 campo)

**Fonte**: Metadata / detecção manual

**Campos**:
| Campo | Tipo | Conteúdo |
|-------|------|----------|
| `measurement_breakpoints` | JSON | Array de descontinuidades detectadas |

**Estrutura JSON**:
```json
{
  "breakpoints": [
    {
      "field": "rhr",
      "detected_on": "2026-06-11",
      "status": "INVALID_COMPARISON",
      "reason": "pipeline_migration",
      "baseline_policy": "split_by_source",
      "magnitude": 5.9
    },
    {
      "field": "awakenings",
      "detected_on": "2026-08-01",
      "status": "INVALID_COMPARISON",
      "reason": "garmin_api_definition_change",
      "baseline_policy": "split_by_date",
      "note": "19x distribution shift"
    }
  ]
}
```

**Interpretação**:
- Cada breakpoint documenta quando um campo deixou de ser comparável com seu passado
- Policy explica como lidar com baseline (split_by_source, split_by_date, nulled_for_legacy, etc.)
- Qualquer análise que cruze um breakpoint sem respeitar a policy pode estar viciada

---

## Fluxo de Interpretação

### 1. Verificação de Descontinuidades
```
LEIA measurement_breakpoints
├─ Alguma métrica que vou usar cruza um breakpoint?
└─ Sim → respeite a baseline_policy
   └─ Não → use baseline de 90 dias normalmente
```

### 2. Análise Domínio-por-Domínio
```
Para cada domínio:
├─ Calcule z-score vs baseline (respeitando descontinuidades)
├─ Compare com histórico recente (7d, 28d)
├─ Classifique como OK / ATENÇÃO / ALERTA
└─ Documente confiança (alta: n≥79, média: n≥8, baixa: n<8)
```

### 3. Convergência Entre Domínios
```
Procure por padrões:
├─ Se 2+ domínios em ALERTA na mesma direção (ex: CARGA↑ + RECUPERAÇÃO↓)
│  └─ Padrão DETECTADO — avaliar severidade
├─ Se domínios divergem (CARGA↑ mas RECUPERAÇÃO→)
│  └─ Padrão MISTO — vigilância mas sem ação imediata
└─ Se todos domínios OK exceto um
   └─ Outlier — pode ser ruído, investigar
```

### 4. Evidência Contra Interpretação
```
Antes de concluir "atleta sobrecarregado":
├─ Procure evidência CONTRA
├─ Ex: TSB muito baixo, mas HRV, RHR, night_hr todos preservados
│  └─ Indica carga sendo absorvida, não deterioração sistêmica
└─ Documente achados negativos explicitamente
```

---

## Contrato de Dados

### Tipos e Ranges

| Campo | Tipo | Min | Max | Null | Notas |
|-------|------|-----|-----|------|-------|
| `trimp_7d` | REAL | 0 | 1000 | OK | Soma 7d; pode ser 0 em dia de repouso |
| `ctl` | REAL | 0 | 150 | OK | Nunca negativo (exponential filter) |
| `atl` | REAL | 0 | 150 | OK | Nunca negativo |
| `tsb` | REAL | −100 | +50 | OK | Pode ser muito negativo em pico |
| `hrv_avg` | REAL | 5 | 150 | OK | ~40% dos dias têm medição |
| `rhr` | INTEGER | 40 | 100 | OK | Inteiro; ~99% cobertura após 2026-06-11 |
| `night_hr_avg` | INTEGER | 45 | 110 | OK | Derivado de sleep window; 60% cobertura |
| `stress_avg` | INTEGER | 0 | 100 | OK | Score Garmin; diário |
| `sleep_total_min` | REAL | 0 | 600 | OK | Minutos; ~60% cobertura |
| `awake_minutes` | REAL | 0 | 300 | OK | Tempo acordado na janela de sono |
| `output_preserved` | TEXT | — | — | Não | 'yes' / 'no' / 'indeterminate' |
| `deterioration_detected` | TEXT | — | — | Não | 'yes' / 'no' / 'indeterminate' |

### Políticas para NULLs

- **HRV, night_hr, sleep_***: NULLs são OK (~40-60% cobertura). Confidence degradada.
- **RHR, stress_avg**: Raramente NULL. Se NULL → error no Garmin sync.
- **TRIMP**: Nunca NULL após 2026-06-11 (atividades sempre têm duração + HR).
- **performance_* fields**: NULL se sem atividade do tipo naquele dia (ex: sem intervalado → output_preserved=NULL).

### Descontinuidades Permanentes

| Campo | Data | Action |
|-------|------|--------|
| `rhr` | ≥ 2026-06-11 | Sempre split baseline |
| `awakenings` | ≥ 2026-08-01 | Não comparar com pré-08-01 |
| `distance_km` (legacy) | ≤ 2026-06-10 | Usar TRIMP, não distância |

---

## Próximos Passos para Automação

Uma vez congelado este modelo v1.0:

1. **Implementar `farol_daily_state` table** com esses 18 campos
2. **Criar rotina diária** que:
   - Puxa dados de `daily`, `activities`, `training_load`
   - Calcula baselines respeitando descontinuidades
   - Insere row em `farol_daily_state`
3. **Gerar `DAILY_STATE.md`** (1 página) automaticamente a partir de `farol_daily_state`
4. **Gerar `DAILY_ANALYSIS.md`** (full diagnostic) para análise profunda

---

## Histórico de Versões

| Versão | Data | Mudanças |
|--------|------|----------|
| **v1.0** | 2026-09-03 | Congelado: Domínios estruturados, descontinuidades documentadas, contrato de dados |

