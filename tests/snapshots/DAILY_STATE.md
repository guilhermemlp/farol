# ESTADO DO ATLETA — 2026-11-02

Gerado automaticamente a partir de `farol_daily_state` (`python -m farol.domain.state`). Ver MODEL_V1.1.md para a definicao dos campos.

> **Leitura descritiva:** os estados comparam dados ao historico individual. Convergencia e um sinal para revisar evidencias, confianca e persistencia; nao e diagnostico nem decisao automatica de treino.

> **Dados:** este relatorio le o banco SQLite local e nao consulta Garmin ou clima. Para atualizar o historico, rode `python -m farol.pipeline.ingest`; o clima do percurso e uma consulta externa separada.

## MATRIZ RAPIDA

| Dominio | Estado | Confianca | Streak |
|---------|--------|-----------|--------|
| **CARGA** | 🔴 ALERTA | ALTA | 3d |
| **RECUPERACAO** | 🔴 ALERTA | ALTA | 6d |
| **SONO** | 🔴 ALERTA | ALTA | 10d |

## ANALISE POR DOMINIO

### 1. CARGA
| Metrica | Hoje | Baseline 90d | z |
|---------|------|---------------|---|
| TRIMP 7d | 302 | 299.7 | +0.10 |
| TRIMP 28d | 987 | 1102.97 | -1.69 |
| CTL | 55.8 | 55 | +0.78 |
| ATL | 57.4 | 57 | +0.31 |
| TSB | -4 | 1.93 | -1.60 |
| ATL/CTL | 1.06 | 1.04 | +0.67 |

### 2. RECUPERACAO
| Metrica | Hoje | Baseline 90d | z |
|---------|------|---------------|---|
| HRV | 34 | 45.01 | -3.47 |
| FC repouso | 60 | 58 | +0.78 |
| FC noturna | 64 | 61.97 | +1.02 |
| Stress | 24 | 22.01 | +0.63 |
| Body Battery | 70 | 70.07 | -0.01 |
| Respiracao | 15.4 | 14.2 | +3.75 |
| SpO2 media | 96 | 95.85 | +1.00 |
| SpO2 minima | 92.4 | 93 | -1.43 |

### 3. SONO
| Metrica | Hoje | Baseline 90d | z |
|---------|------|---------------|---|
| Duracao do sono | 405 | 429.6 | -1.40 |
| Tempo acordado | 24 | 17.93 | +1.64 |
| Sono profundo | 86 | 89.97 | -0.66 |
| Sono REM | 81 | 84.97 | -0.66 |
| Sleep score | 74 | 79.93 | -1.60 |

### 4. PERFORMANCE
- **output_preserved**: yes
- **deterioration_detected**: no
- Regra: caminhadas, aquecimento e recuperacoes nao entram na analise dos tiros; somente `INTERVAL_ACTIVE` compoe esse output.

### 5. OBSERVACAO
Nenhuma descontinuidade conhecida.

## CONVERGENCIA
3 dominios simultaneamente fora do normal: CARGA, RECUPERACAO, SONO

Ha 6 dias consecutivos (desde 2026-10-28).
## OBSERVACAO MANUAL: LESOES

Sem observacao manual registrada.

Esta observacao nao altera CARGA, RECUPERACAO, SONO, TSB ou convergencia.
