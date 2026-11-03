# Farol — guia para agentes

## Propósito e limites

Farol é um sistema local de leitura diária de recuperação e carga de corrida a
partir de dados Garmin. Ele **descreve** o estado contra o histórico individual;
não prescreve treino, periodização, tratamento nem faz diagnóstico.

- Preserve a separação entre estado fisiológico e recomendação do treinador.
- Nunca transforme um padrão de wearable em diagnóstico. Caso um texto mencione
  um possível problema de saúde, use linguagem de compatibilidade/observação e
  indique avaliação médica quando apropriado.
- Use o padrão individual e as baselines móveis do projeto; não introduza
  limiares populacionais ou fisiológicos fixos sem uma decisão explícita,
  documentação e testes.
- Uma métrica isolada não caracteriza overtraining: a leitura deve exigir
  convergência e considerar evidência contrária.

Leia `docs/PROFILE.md` antes de alterar interpretação, textos de saúde, zonas
de FC, baselines ou o índice de recuperação. Leia `docs/MODEL_V1.1.md` antes
de mudar classificação, convergência, campos de `farol_daily_state` ou métricas.
`MODEL_V1.1.md` está em vigor; `MODEL_V1.0.md` é histórico.

## Dados e privacidade

Fluxo de dados:

```text
Garmin -> data/garmin_health.xlsx -> data/farol.db -> reports/
```

- `data/garmin_health.xlsx` é a fonte de verdade. O SQLite é reconstruível a
  partir dela; não trate o banco como origem de uma correção manual.
- `reports/` contém saídas geradas. Regenere-as pelos comandos do pacote em vez
  de editar o conteúdo manualmente.
- `.env`, `.garmin_token/`, `data/farol.db`, `data/profile.yaml`, o arquivo XLSX
  e relatórios podem conter dados pessoais ou credenciais. Nunca os exponha,
  copie para logs, inclua em commits ou use dados reais em exemplos públicos.
- Não execute sincronização Garmin, não apague tokens nem altere planilha/banco
  pessoal sem o pedido explícito do usuário. Para diagnóstico e testes, prefira
  bancos em memória, arquivos temporários ou conexões somente-leitura.
- O histórico tem duas procedências que não podem ser misturadas em uma
  baseline: legado até 2026-06-10 e sincronização atual desde 2026-06-11.
  `farol.metrics.DATE_SPLITS` também protege quebras conhecidas de medição.

## Arquitetura

- `farol/config.py`: caminhos, variáveis de ambiente e perfil.
- `farol/db.py` e `farol/schema.sql`: conexão, schema e migrações. Use
  `connect_readonly()` para o banco pessoal quando só for ler; apenas fluxos
  explícitos de escrita devem usar `connect_write()`.
- `farol/sheets.py`, `farol/load.py`, `farol/ingest.py`: entrada, planilha e
  reconstrução do banco.
- `farol/metrics.py`: primitiva `Metric`, baseline móvel e z-score. É o local
  apropriado para semântica estatística compartilhada.
- `farol/training_load.py`, `farol/analysis.py`, `farol/recovery.py` e
  `farol/zones.py`: regras de domínio. `recovery.py` é a única fonte dos pesos
  padrão do índice.
- `farol/daily_state_builder.py`: constrói a tabela derivada
  `farol_daily_state`; mantenha-o fora de `db.py`.
- `farol/state.py::gather()`: fonte única da classificação por domínio usada
  por `state`, `daily_analysis` e a interpretação.
- `farol/interpretation.py`: apenas traduz classificação existente em texto;
  não deve recalcular métricas nem criar limiares.
- `farol/today.py`: relatório diário; `farol/dashboard.py`: visualização que
  deve chamar funções públicas existentes, sem duplicar regras de domínio.
- `farol/longitudinal.py`, `correlations.py`, `changepoint.py`,
  `race_prediction.py` e `decoupling.py`: análises descritivas sob demanda.

Ao adicionar uma métrica, deixe explícitos: procedência, direção fisiológica,
baseline aplicável, cobertura/`NULL`, se participa de domínio, convergência e
índice, e os testes. Campos com janela autocorrelacionada não devem inflar
streak ou convergência.

## Convenções de implementação

- Python 3.11+; dependências e comandos de console estão em `pyproject.toml`.
- Preserve o estilo existente e mensagens/relatórios em português.
- Mudanças de schema devem ser idempotentes: atualizar `schema.sql`, as
  migrações de `db.ensure_schema()` quando necessário, e testes de migração.
- Trate `NULL` como cobertura ausente, não como zero, alerta ou erro.
- A classificação deve respeitar a direção da métrica (por exemplo, menor pode
  ser melhor) e a procedência; valores brutos não devem atravessar fontes ou
  quebras de medição.
- Correlações usam desvios contra a baseline, devem informar `n` e não devem
  apresentar causalidade ou p-valores ingênuos para séries autocorrelacionadas.
- Predição de prova é estimativa com incerteza, nunca recomendação de ritmo ou
  plano de treino.
- Não desfaça alterações preexistentes do usuário nem atualize dependências,
  credenciais ou dados pessoais fora do escopo pedido.

## Trabalho e validação

Antes de editar, leia os módulos e testes diretamente afetados. Acrescente ou
ajuste testes para cada mudança de comportamento; em especial, proteja contra
vazamento temporal, mistura de fonte e escrita acidental no banco pessoal.

Comandos usuais no PowerShell:

```powershell
.venv\Scripts\python -m unittest discover -s tests
.venv\Scripts\python -m ruff check farol tests
```

Se o ambiente virtual não estiver disponível, use `python` somente depois de
confirmar que ele aponta para Python 3.11+ com as dependências instaladas.
Os testes golden que dependem do banco pessoal podem ser pulados em ambiente
limpo; isso é esperado. Nunca contorne essa proteção para modificar
`data/farol.db` durante a suíte.

Para operações manuais autorizadas, os comandos principais são:

```powershell
python -m farol.ingest --days 7  # chama Garmin; somente com autorização
python -m farol.load             # reconstroi SQLite a partir da planilha
python -m farol.today
python -m farol.state
python -m farol.dashboard
```

Consulte `README.md` para a lista completa de comandos e `docs/HANDOFF_2026-09-04.md`
para contexto histórico de uma entrega anterior; valide o estado atual no código e
nos testes, pois o handoff não substitui a inspeção do repositório.

## Definição de pronto

Para uma alteração de código: mudança mínima e documentada quando altera um
contrato, testes relevantes verdes, lint sem novos problemas e nenhuma
alteração não intencional de dados pessoais, planilha, banco ou relatório.
