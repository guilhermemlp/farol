# Calibração

O relatório compara cada métrica contra a sua própria janela móvel. A
calibração operacional é automática: após cada `farol.ingest`, o Farol gera
`reports/CALIBRACAO_AUTOMATICA.md` e o dashboard mostra a mesma leitura.

Ela compara o índice de recuperação do Farol ao Training Readiness do Garmin,
apenas nos dias em que os dois têm baseline pessoal suficiente. Mostra
cobertura, concordância bruta, matriz de categorias e kappa descritivo. A
comparação é sempre retrospectiva até a data analisada e não exige
preenchimento diário.

O Garmin é um segundo juiz, não uma verdade clínica. As séries diárias são
autocorrelacionadas; os números descrevem concordância, não provam validade
causal. A rotina não altera `profile.yaml`, pesos, limiares ou baselines.

## Registro subjetivo opcional (legado)

`CALIBRACAO_LOG.txt` e `farol.calibration_review` continuam disponíveis para
quem quiser confrontar o relatório com percepção pessoal. Esse registro é
exploratório e não é requisito para a calibração automática nem altera o
modelo por si só.

## O que é ajustável, em ordem de probabilidade

### 1. Os pesos do índice — `profile.yaml`

```yaml
recovery_index:
  weights:
    sleep_score: 0.30
    hrv_avg: 0.30
    rhr: 0.20
    tsb: 0.20
```

Qualquer ajuste exige decisão explícita, documentação e testes. A calibração
automática não sugere nem aplica novos pesos.

### 2. A sensibilidade dos z-scores — `farol/metrics.py`

```python
_Z_ATENCAO = -0.5   # a partir daqui, "atencao"
_Z_ALERTA  = -1.5   # a partir daqui, "alerta"
```

Mexa aqui só se o relatório marcar `atencao` em dias que você considera normais
(afaste para −0,75) ou passar batido por dias ruins (aproxime para −0,35).
Lembre que 0,5 desvio é a fronteira usual entre variação do dia e mudança real.

### 3. As âncoras da nota — `farol/today.py::score_from`

```python
# z=0 -> 6.5   z=-0.5 -> 6.0   z=-1.5 -> 3.5
```

Os pontos de quebra são os mesmos de `_Z_ATENCAO` e `_Z_ALERTA`, de propósito:
se o índice e o status contarem histórias diferentes, um dos dois está errado.
Se mudar os limiares acima, mude aqui junto.

Depois de qualquer ajuste, rode a suíte:

```bash
python -m unittest discover -s tests
```

## O que NÃO ajustar

As baselines. Elas são calculadas do banco e se atualizam sozinhas — foi
exatamente o que consertou o problema anterior, em que `profile.yaml` dizia que
sua FC de repouso normal era 72,2 quando já estava em 76,1 havia meses.

O bloco `baseline:` do `profile.yaml` não é lido por código nenhum — sobrou como
referência histórica. Seu único consumidor era o `farol.signal`, removido junto.

## Critério de conclusão

> A concordância Farol × Garmin está visível e auditável após cada
> sincronização, sem modificar o modelo automaticamente.

## Quando algo não funciona

| sintoma | causa provável |
|---|---|
| `Nenhum dado para <data>` | `farol.ingest` ainda não rodou hoje |
| métrica com `confianca baixa` | poucos dias com aquele campo na janela de 90 dias; HRV e sono só existem em ~40% dos dias |
| `z` ausente (`sem dado`) | menos de 8 pontos na janela — o desvio seria instável demais para ancorar um z |
| login do Garmin falha | apague `.garmin_token/` e rode de novo; a senha do `.env` cria uma sessão nova |
| índice muito diferente do esperado após editar pesos | os pesos precisam somar 1,0 |

## Nota histórica (2026-09-03) — comando e formato do log legado

Este documento foi escrito antes do MODEL_V1.0 ser congelado e antes de
`farol/longitudinal.py` existir, quando `farol.today` (o índice único) era o
único retrato do dia. As regras acima (rotina, critério de conclusão,
diagnóstico de sintomas) continuam valendo sem alteração — esta nota só
registra a divergência entre o formato de linha única descrito acima e o que
`CALIBRACAO_LOG.txt` de fato usa hoje.

`farol/calibration.py` (`python -m farol.calibration`, rodado depois de
`farol.ingest --days 2`) gera a entrada do dia em `CALIBRACAO_LOG.txt`.
Em vez de uma linha só com o índice, cada entrada é um bloco com o índice
**e** o estado por domínio (CARGA/RECUPERAÇÃO/SONO/PERFORMANCE/CONVERGÊNCIA
de `farol.state.gather`) **e** os sinais longitudinais (streak de
`farol.longitudinal`) — o retrato completo que `DAILY_STATE.md` já produz,
não só o índice. O princípio central desta página continua intacto: nada é
preenchido a partir da percepção real, e **BATEU COM A REALIDADE?** — o
equivalente à terceira coluna do formato original — segue sendo a única
resposta que importa para uma futura decisão sobre o modelo, preenchida à
mão, só depois das ~2 semanas de observação.
