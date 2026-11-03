# DIAGNOSTICO DIARIO — 2026-11-02

- Data analisada: 2026-11-02
- Gerado em: 2026-11-02T00:00:00
- Periodo de referencia: janela movel de 90 dias por metrica (dia-alvo excluido da propria baseline), 7d/30d para tendencia
- Fonte: `farol_daily_state` (RHR obtido de `daily`, respeitando a quebra de fonte de 2026-06-11 -- ver farol.metrics.from_daily)
- Atualizacao: esta analise le o SQLite local e nao consulta o Garmin. A ingestao Garmin e feita separadamente por `python -m farol.ingest`; clima de percurso tambem e uma consulta externa separada.

> **Leitura descritiva:** convergencia e alertas estatisticos indicam evidencias para revisar, nao diagnostico nem decisao automatica de treino.

Diagnostico detalhado. Para o resumo executivo de 1 pagina, ver `DAILY_STATE.md` (`python -m farol.state`). Gerado deterministicamente a partir dos mesmos dados e das mesmas regras de classificacao.

## 2. RESUMO EXECUTIVO

**Estado geral**: 3 de 3 dominios fora do padrao pessoal (CARGA, RECUPERACAO, SONO).

**Sinal relevante?** Sim.

**Dominios em atencao/alerta**: CARGA, RECUPERACAO, SONO.

**Convergencia entre dominios?** Sim -- 3 dominios simultaneamente fora do normal: CARGA, RECUPERACAO, SONO

**Hipotese principal**: Convergencia entre CARGA, RECUPERACAO, SONO: mais de um dominio fora do padrao ao mesmo tempo, incluindo RECUPERACAO -- reduz a chance de ser ruido isolado e pesa mais a favor de investigar deterioracao sistemica.

**Principal contra-sinal**: Nenhum dominio permanece dentro do padrao.

**Performance no dia**: output_preserved=yes, deterioration_detected=no.

## 3. CARGA

| Metrica | Hoje | Baseline 90d | z | Estado | Tendencia (7d vs 30d) |
|---------|------|---------------|---|--------|------------------------|
| TRIMP 7d | 302 | 299.7 | +0.10 | NORMAL | estavel |
| TRIMP 28d | 987 | 1102.97 | -1.69 | ALERTA | estavel |
| CTL | 55.8 | 55 | +0.78 | NORMAL | estavel |
| ATL | 57.4 | 57 | +0.31 | NORMAL | estavel |
| TSB | -4 | 1.93 | -1.60 | ALERTA | estavel |
| ATL/CTL | 1.06 | 1.04 | +0.67 | NORMAL | estavel |

TSB = CTL − ATL. Hoje: 55.8 − 57.4 = -4. CTL acumula a carga cronica (media movel de 42 dias) e ATL a carga aguda (media movel de 7 dias); ATL/CTL > 1 significa que a carga recente esta acima do que o corpo vem absorvendo em media -- fase de pico, nao necessariamente problema.

**TSB e a unica evidencia usada para o estado do dominio** (z=-1.6, confianca alta): ALERTA. CTL, ATL e ATL/CTL sao contexto de como esse TSB foi construido, nao evidencias adicionais -- os tres vem da mesma serie de TRIMP que gera o proprio TSB, e conta-los separadamente inflaria artificialmente o numero de sinais (MODEL_V1.0.md, dominio CARGA).

Ativo ha 3 dias consecutivos (desde 2026-10-31), tendencia piorando (janela de 7 dias).

## 4. RECUPERACAO

| Metrica | Hoje | Baseline 90d | z | Estado | Tendencia (7d vs 30d) |
|---------|------|---------------|---|--------|------------------------|
| HRV | 34 | 45.01 | -3.47 | ALERTA | estavel |
| FC repouso | 60 | 58 | +0.78 | BORDERLINE | estavel |
| FC noturna | 64 | 61.97 | +1.02 | BORDERLINE | estavel |
| Stress | 24 | 22.01 | +0.63 | BORDERLINE | estavel |
| Body Battery | 70 | 70.07 | -0.01 | NORMAL | estavel |
| Respiracao | 15.4 | 14.2 | +3.75 | ALERTA | estavel |
| SpO2 media | 96 | 95.85 | +1.00 | NORMAL | estavel |
| SpO2 minima | 92.4 | 93 | -1.43 | BORDERLINE | estavel |

**Estado do dominio: ALERTA** (confianca alta -- a mais baixa entre as metricas com dado; o dominio usa o pior status entre suas metricas, nao uma media). Estado puxado por: HRV, Respiracao (metricas em alerta).

Ativo ha 6 dias consecutivos (desde 2026-10-28), tendencia piorando (janela de 7 dias).

## 5. SONO

| Metrica | Hoje | Baseline 90d | z | Estado | Tendencia (7d vs 30d) |
|---------|------|---------------|---|--------|------------------------|
| Duracao do sono | 405 | 429.6 | -1.40 | BORDERLINE | estavel |
| Tempo acordado | 24 | 17.93 | +1.64 | ALERTA | estavel |
| Sono profundo | 86 | 89.97 | -0.66 | BORDERLINE | estavel |
| Sono REM | 81 | 84.97 | -0.66 | BORDERLINE | estavel |
| Sleep score | 74 | 79.93 | -1.60 | ALERTA | estavel |

**Estado do dominio: ALERTA** (confianca alta -- a mais baixa entre as metricas com dado; o dominio usa o pior status entre suas metricas, nao uma media). Estado puxado por: Tempo acordado, Sleep score (metricas em alerta).

Ativo ha 10 dias consecutivos (desde 2026-10-24), tendencia piorando (janela de 7 dias).

Sleep score e um resumo qualitativo, nao a unica medida de qualidade -- duracao, sono profundo e REM sao avaliados individualmente acima e podem sustentar o dominio mesmo quando o score sozinho esta fraco.

## 6. PERFORMANCE

Regra de leitura: caminhadas, aquecimento e recuperacoes nao entram nos tiros; a analise intervalada usa somente segmentos `INTERVAL_ACTIVE`.

- **output_preserved**: yes
- **deterioration_detected**: no

Combinado com o estado de CARGA (ALERTA): a capacidade de sustentar o pace/output do treino intervalado e a ausencia de degradacao tecnica entre tiros sao evidencia de que a carga atual esta sendo absorvida, nao de sobrecarga.

## 7. CONVERGENCIA

- Dominios em atencao/alerta: 3 (CARGA, RECUPERACAO, SONO)
- Dominios preservados: nenhum
- **Convergencia sinalizada**: 3 dominios simultaneamente fora do normal: CARGA, RECUPERACAO, SONO. Dois ou mais dominios fora do padrao ao mesmo tempo reduz a chance de ser ruido isolado em uma unica metrica.
- **Persistencia**: convergencia ativa ha 6 dias consecutivos (desde 2026-10-28), tendencia piorando.

## OBSERVACAO MANUAL: LESOES

Sem observacao manual registrada.

Este registro e separado das metricas Garmin: nao altera carga, recuperacao, sono, TSB ou convergencia.

## 8. DESCONTINUIDADES / QUALIDADE DOS DADOS

Nenhuma descontinuidade de medicao conhecida.

## 9. INTERPRETACAO

**EVIDENCIA** (o que os dados mostram):
- CARGA: ALERTA (confianca alta)
- RECUPERACAO: ALERTA (confianca alta)
- SONO: ALERTA (confianca alta)
- PERFORMANCE: output_preserved=yes, deterioration_detected=no

**INTERPRETACAO** (o que os sinais combinados sugerem):
- Convergencia entre CARGA, RECUPERACAO, SONO: mais de um dominio fora do padrao ao mesmo tempo, incluindo RECUPERACAO -- reduz a chance de ser ruido isolado e pesa mais a favor de investigar deterioracao sistemica.
- Contra-sinal considerado: Nenhum dominio permanece dentro do padrao.
- 3 dominios simultaneamente fora do normal: CARGA, RECUPERACAO, SONO.

**LIMITACAO** (o que nao pode ser concluido a partir destes dados):
- Nenhuma limitacao adicional alem da normal: esta analise compara o dia contra o padrao do proprio atleta, nao contra limiares fisiologicos absolutos, e convergencia entre dominios nao implica causalidade entre eles.

## 10. CONCLUSAO

- **Evidencia de deterioracao sistemica?** Sim -- dominio em ALERTA.
- **Evidencia de carga elevada?** Sim (TSB fora do padrao).
- **Recuperacao comprometida?** Sim.
- **Sono comprometido?** Sim.
- **Performance preservada?** Sim -- output preservado no treino intervalado do dia.
- **Existe convergencia?** Sim -- 3 dominios simultaneamente fora do normal: CARGA, RECUPERACAO, SONO.

Este documento e diagnostico: descreve o que os dados mostram e o que sugerem em conjunto. Nao prescreve treino (PROFILE.md, regras 7 e 9).

## 11. LEITURA (INTERPRETACAO EM LINGUAGEM)

Leitura retrospectiva derivada da classificacao acima (farol.interpretation) -- descreve, nao prescreve.

- **Estado fisiologico**: alerta -- dominio em ALERTA (CARGA, RECUPERACAO, SONO): resposta fora do padrao pessoal
- **Carga**: fora do padrao pessoal (alerta)
- **Recuperacao**: comprometida -- nucleo autonomico fora do padrao (HRV, FC repouso, FC noturna, Stress); campo(s) complementar(es) isolado(s) (Respiracao, SpO2 minima)
- **Sono**: comprometido -- dimensao afetada: duracao (Duracao do sono), duracao (Tempo acordado), arquitetura (Sono profundo), arquitetura (Sono REM), score (Sleep score)
- **Sinal dominante**: sinais em CARGA, RECUPERACAO, SONO: TSB, HRV, FC repouso, FC noturna, Stress, Respiracao, SpO2 minima, Duracao do sono, Tempo acordado, Sono profundo, Sono REM, Sleep score
- **Convergencia**: forte -- 3 dominios, 12 sinais no total (CARGA: 1 sinal, RECUPERACAO: 6 sinais, SONO: 5 sinais)
- **Performance**: preservada no treino intervalado do dia (sem degradacao tecnica)
- **Confianca**: confianca alta (classificacao dos dominios com sinal)
- **Conclusao**: ha evidencia de alteracao relevante: nucleo autonomico fora do padrao ou dominio em alerta

---

Sinais fora do padrao por dominio (mesma classificacao de farol.state):
- CARGA: TSB
- RECUPERACAO: HRV, FC repouso, FC noturna, Stress, Respiracao, SpO2 minima
- SONO: Duracao do sono, Tempo acordado, Sono profundo, Sono REM, Sleep score
