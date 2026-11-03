# Relatório de capacidades do Farol

**Revisado em:** 22/09/2026  
**Escopo:** capacidades presentes no código e nos comandos do pacote nesta data.  
**Fontes principais:** `README.md`, `pyproject.toml`, `docs/PROFILE.md`, `docs/MODEL_V1.1.md` e módulos de `farol/`.

## 1. Resumo

Farol é um sistema local de leitura diária e retrospectiva de dados de saúde e corrida provenientes principalmente do Garmin. Ele organiza os dados, compara cada indicador com o histórico do próprio atleta, calcula carga de treino, destaca padrões simultâneos e produz relatórios em texto, Markdown, JSON e HTML.

O produto tem duas frentes que se relacionam, mas não são a mesma coisa:

1. **Estado fisiológico observado:** recuperação, sono e carga, com classificações e tendências baseadas no padrão individual.
2. **Atividades e provas:** resumo de sessões, métricas de corrida, análises de eficiência, estimativas de desempenho e contexto de percurso ou prova.

Há ainda ferramentas de calibração, integridade dos dados e registros manuais. A maior parte das análises é retrospectiva ou executada sob demanda; o Farol não acompanha sensores em tempo real.

> **Como usar a leitura:** o Farol descreve dados observados em relação ao histórico individual. Um status ou uma convergência é um convite para revisar evidências e contexto, não uma ordem para treinar, descansar, competir ou buscar um diagnóstico.

### Guia rápido para ler os relatórios

Uma primeira leitura pode seguir esta ordem:

1. **Comece pelo RESUMO em `farol.today`.** Veja o índice de recuperação como síntese relativa ao padrão pessoal, se há treino registrado e se aparece algum sinal combinado. Não interprete a nota como probabilidade de estar saudável ou pronto para treinar.
2. **Confira a confiança e os dados disponíveis.** Confiança baixa, cobertura parcial ou “sem dado” significam que a comparação é limitada; não significam que o estado seja bom ou ruim.
3. **Procure as métricas que sustentam o status.** Veja valor atual, baseline, desvio e tendência. O mesmo rótulo pode ser sustentado por um indicador ou por vários; a análise detalhada identifica os campos determinantes e os contextuais.
4. **Leia carga e sono como contexto.** TSB ajuda a entender se a carga recente pode acompanhar sinais de recuperação. Os estágios e a regularidade do sono acrescentam contexto; um único valor não explica sozinho uma alteração.
5. **Se houver convergência, examine duração e força.** Ela começa quando pelo menos dois domínios (CARGA, RECUPERAÇÃO, SONO) estão fora do padrão no mesmo dia. Veja quais sinais a sustentam, a confiança, se persiste e se a tendência melhora ou piora. Dois domínios borderline com um sinal cada constituem evidência mais limitada que vários sinais em vários domínios.
6. **Considere a procedência.** Uma quebra de fonte ou mudança da definição do Garmin pode tornar períodos não comparáveis. O relatório de estado pode listar descontinuidades conhecidas; `farol.changepoint` só sugere candidatas para revisão.

Os rótulos `ok`, `atenção` e `alerta` indicam posição relativa à baseline e direção da métrica. “Alerta” é um rótulo estatístico do modelo, não um alarme médico. Se um achado persistente ou sintoma preocupa o atleta, o Farol não substitui avaliação profissional.

## 2. Princípios de leitura

- O padrão do próprio atleta é a referência principal. As métricas usam baseline móvel individual, normalmente de até 90 dias, e levam em conta a direção da métrica: por exemplo, FC de repouso mais alta tende a ser desfavorável, enquanto HRV mais alta tende a ser favorável.
- A confiança depende da quantidade de dados comparáveis. Uma lacuna de medição é tratada como dado ausente, não como zero ou como alerta.
- Mudanças conhecidas de origem ou definição dos dados são separadas para impedir que baselines misturem medições incompatíveis.
- Uma métrica isolada não caracteriza overtraining nem doença. O modelo considera agregação, convergência e evidência contrária.
- O Farol descreve estado e desempenho observados. Decisões de treino e periodização pertencem ao atleta e ao treinador; questões clínicas devem ser avaliadas por profissionais de saúde.

As regras de interpretação e o contexto individual estão documentados em `docs/PROFILE.md` e `docs/MODEL_V1.1.md`. O código central das baselines está em `farol/metrics.py`.

## 3. Capacidades de saúde, recuperação e sono

### 3.1 Indicadores diários

O fluxo Garmin coleta dados diários de saúde, sono, HRV e prontidão, quando disponíveis. Entre os campos usados ou exibidos estão:

- **Recuperação/autonômicos:** HRV média, FC de repouso, FC noturna, respiração média, SpO₂ média e mínima, stress e Body Battery.
- **Sono:** score, duração total, tempo acordado/despertares, sono profundo, sono leve, sono REM e horários de início/fim.
- **Campos adicionais do Garmin:** Training Readiness, Training Status, Recovery Time e VO₂max são coletados e armazenados quando disponíveis. O Training Readiness é comparado com o índice do Farol na calibração; o VO₂max aparece como métrica do Garmin. Esses valores não são todos evidências dos domínios fisiológicos.
- **Regularidade do sono:** desvio-padrão circular do horário de início do sono numa janela de 14 dias. É exibido como contexto, não como classificação diária.

Nem todo indicador tem cobertura diária. O relatório sinaliza ausência de dado e reduz a confiança quando a cobertura não permite comparação suficiente.

### 3.2 Índice de recuperação

O relatório `farol.today` calcula um índice de 0 a 10 a partir de métricas normalizadas contra o histórico. Os pesos padrão contemplam score do sono, HRV, FC de repouso e TSB; podem ser configurados no perfil. Quando uma métrica não está disponível, seu peso é removido e os demais são renormalizados, em vez de tratar a ausência como valor neutro.

O índice recebe também uma descrição por faixas — acima, dentro, abaixo ou bem abaixo do normal pessoal — e uma classificação compatível com as faixas da primitiva de métricas. Ele não é uma medida clínica nem uma recomendação para treinar ou descansar.

### 3.3 Domínios do estado diário

`farol.state.gather()` é a fonte compartilhada de classificação para o resumo e para a análise detalhada. Ela reúne três domínios:

- **CARGA:** reúne TRIMP, CTL, ATL, TSB e relações de carga. O status do domínio é determinado pelo TSB; as demais métricas são contexto da mesma série de carga, não evidências independentes adicionais.
- **RECUPERAÇÃO:** considera HRV, FC de repouso e noturna, stress, Body Battery máxima, respiração e SpO₂. O pior status entre os indicadores com dado representa o domínio e a confiança considera os campos disponíveis.
- **SONO:** considera duração, tempo acordado, sono profundo, REM e score do sono. A regularidade do horário fica fora da classificação para não prolongar artificialmente uma sequência por causa da janela móvel sobreposta.

Para cada domínio, o sistema informa estado, confiança, métricas, evidências, indicadores determinantes/contextuais, streak e tendência. `farol.longitudinal` também consegue resumir episódios históricos num intervalo, com datas de início e fim.

### 3.4 Convergência e interpretação

O modelo sinaliza convergência quando dois ou mais domínios estão fora do padrão ao mesmo tempo. O relatório detalhado separa dados observados, interpretação e limitações; a camada `farol.interpretation` traduz a classificação já existente em linguagem natural e qualifica a força do sinal. Na leitura em linguagem, o núcleo autonômico só é considerado comprometido quando **dois ou mais** de HRV, FC de repouso, FC noturna e stress estão fora do padrão; um único campo é descrito como sinal isolado, nunca como deterioração sistêmica.

No relatório diário existe ainda uma contagem mais fina de indicadores individuais simultaneamente fora do padrão. Essa contagem não deve ser confundida com a convergência entre domínios: dois campos de recuperação fora do padrão são dois sinais, mas continuam sendo um domínio.

Uma assinatura de vários sinais agudos fora do padrão, sem evidência de carga de treino elevada, pode produzir a expressão **“padrão compatível com resposta imunológica ativa”**. É uma hipótese observacional, dependente de dados suficientes; não identifica doença e não confirma uma causa. O código exige três dos cinco sinais agudos definidos pelo modelo e deixa de emitir essa leitura se não houver TSB suficiente para avaliar a explicação de treino.

### 3.5 Performance e sinais de treino intervalado

O domínio PERFORMANCE guarda `output_preserved` e `deterioration_detected`. Esses campos são avaliados apenas quando há treino intervalado reconhecido, com ao menos dois tiros comparáveis. Caminhadas, aquecimento, recuperações e corridas contínuas não são tratados como repetições de trabalho.

Esses resultados descrevem a sessão em relação às sessões comparáveis do próprio atleta; não classificam a saúde geral nem constituem prescrição.

### 3.6 Registro manual de lesões

`farol.injuries` mantém um log local separado dos dados fisiológicos. O atleta pode registrar data, área/sintoma, dor de 0 a 10, inchaço ou roxo, tênis, capacidade de caminhar/correr, piora e nota livre.

O relatório diário e o dashboard exibem a observação mais recente até a data consultada. O alerta decorre apenas do que foi marcado — piora ou incapacidade funcional. O registro não altera recuperação, sono, carga, TSB, convergência ou previsão de prova e não é um diagnóstico.

### 3.7 Calibração automática

`farol.calibration_auto` compara automaticamente, até cada data analisada, o índice de recuperação do Farol e o Training Readiness do Garmin. Os dois lados usam suas próprias baselines móveis pessoais. O relatório mostra cobertura, concordância, matriz de categorias, diferenças de direção e kappa descritivo; a sincronização o regenera e o dashboard usa a mesma fonte.

O Garmin é um segundo juiz, não uma verdade clínica. A saída não infere percepção do atleta, não faz afirmação causal e não altera pesos, limiares, baselines ou perfil. `farol.calibration` e `farol.calibration_review` permanecem como registros subjetivos opcionais e legados.

### 3.8 Análises exploratórias de saúde

- **Correlações defasadas (`farol.correlations`):** analisa sete pares previamente escolhidos, como stress de um dia e sono do dia seguinte. Usa desvios contra baseline, exige ao menos 30 pares e mostra `r` junto de `n`. Não calcula p-valores nem afirma causalidade.
- **Quebras de medição (`farol.changepoint`):** aplica CUSUM para estimar um ponto de mudança de nível em campos diários. A saída serve para revisão humana; uma mudança pode refletir fonte, definição, contexto ou alteração fisiológica real. Não altera automaticamente baselines ou classificações.

## 4. Capacidades de atividade, treino e desempenho

### 4.1 Sincronização e dados de atividade

`farol.ingest` busca dados recentes do Garmin, atualiza a planilha, recarrega o SQLite a partir dela, sincroniza resumos de tiros intervalados e recalcula a carga e o estado diário derivados. A planilha é a fonte de verdade; o banco é reconstruível.

O relatório diário funciona offline depois que os dados e os resumos de tiros já foram armazenados. A análise sob demanda de tiros faz uma consulta adicional ao Garmin. O comando de sincronização altera a planilha e o banco locais e precisa ser executado conscientemente.

### 4.2 Resumo de sessões

Para cada atividade registrada no dia, `farol.today` pode apresentar tipo, nome, duração, distância, pace, FC média/máxima, zona de FC, TRIMP e exigência calculada. Cadência, tempo de contato com o solo e oscilação vertical são mostrados quando os sensores forneceram esses valores.

As zonas de FC usam os limites ancorados no limiar de lactato configurado no perfil, quando disponíveis. No estado atual, o Farol rotula a FC média da atividade; não produz uma distribuição completa do tempo por zona.

### 4.3 Tiros e intervalados

O módulo `farol.splits` separa segmentos `INTERVAL_ACTIVE` de aquecimento, recuperação, caminhada e parada. Para os tiros disponíveis, pode calcular distância e pace de cada repetição, FC média/máxima, cadência, dinâmica de corrida e potência. Também resume médias, consistência do pace (CV) e deriva de FC do primeiro ao último tiro.

O comando aceita um identificador de atividade; sem ele, usa a corrida mais recente do banco. A coleta extra depende de acesso ao Garmin. No fluxo de ingestão, o resumo fica armazenado com estado de coleta explícito (sucesso, vazio, erro ou pendente), permitindo que o relatório diário use os dados salvos.

### 4.4 Carga e monotonia

O TRIMP por sessão alimenta as séries CTL (carga crônica), ATL (carga aguda) e TSB (balanço entre elas). O resumo semanal mostra quilometragem e número de corridas Garmin, TRIMP e monotonia de Foster; apresenta oito semanas no relatório diário e no dashboard.

Monotonia e strain são derivados da variação da carga diária, incluindo dias sem treino. O relatório pode destacar monotonia elevada como marcador descritivo de carga pouco variada; isso não é uma previsão individual de lesão nem uma recomendação de como treinar.

Há uma particularidade de procedência: a quilometragem semanal considera corridas Garmin atuais. Atividades legadas podem contribuir com duração e FC para TRIMP, mas sua distância foi anulada porque a unidade histórica não é confiável.

### 4.5 Eficiência aeróbica e mecânica

`farol.analysis` estima relações entre indicador e pace em duas partes do histórico e compara a previsão num pace de referência fixo. Assim, correr mais rápido por si só não melhora artificialmente o indicador comparado.

As análises disponíveis são:

- **Eficiência aeróbica contínua:** FC estimada num pace fixo entre corridas contínuas.
- **Eficiência em tiros:** FC estimada num pace fixo a partir de repetições intervaladas.
- **Mecânica contínua e mecânica em tiros:** compara cadência, tempo de contato com o solo e oscilação vertical, quando há medições.

Essas análises históricas são exibidas quinzenalmente no relatório de terminal (dias 1 e 15) para reduzir leituras excessivas de indicadores que mudam pouco diariamente.

### 4.6 Decoupling aeróbico numa sessão

`farol.decoupling` compara a relação entre pace e FC na primeira e na segunda metade de uma corrida contínua, usando laps reais da sessão. É uma leitura dentro de uma atividade específica; difere da eficiência aeróbica, que compara corridas ao longo do tempo. O percentual pode ser mostrado sem um veredito validado para o histórico individual.

### 4.7 Previsão de prova

`farol.race_prediction` usa provas observadas declaradas em `data/profile.yaml` e registradas na tabela de atividades. Aplica três modelos independentes — Riegel, Cameron e VDOT (Daniels) — à prova de referência mais recente e mostra tempos, paces e faixa entre modelos.

O relatório separa o que foi observado do que é estimativa e do que ainda precisa ser validado contra o resultado real. Também mostra TSB disponível como contexto. A faixa representa variação entre os modelos, não uma garantia estatística de resultado. Se não há prova de referência no banco, não inventa uma estimativa.

**Como interpretar a faixa:** cada ponta é o menor ou maior tempo entre os três modelos. Uma faixa estreita indica que esses modelos concordaram mais neste caso; uma faixa larga indica maior divergência metodológica. Ela não é um intervalo de confiança, não atribui probabilidade ao resultado e não garante que a prova ficará dentro dela. A estimativa parte da prova observada mais recente, não incorpora automaticamente percurso, clima, sintomas, tolerância nutricional ou uma previsão da forma futura. Os dados externos de percurso e clima são contexto separado.

### 4.8 Perfil de percurso e condições externas

- **`farol.course`:** lê um arquivo GPX e calcula distância do traçado, coordenadas de início e chegada, faixa de elevação e ganho/perda brutos e filtrados. A elevação filtrada é uma estimativa derivada dos pontos GPX, não a altimetria oficial.
- **`farol.race_conditions`:** amostra o percurso, consulta elevação estimada e busca clima horário no início, meio e fim da duração prevista. Usa previsão para datas futuras e arquivo histórico para datas passadas. Depende de conexão e do serviço externo Open-Meteo; os valores servem como contexto, não como sinais do corpo.

### 4.9 Organização de nutrição para prova

`farol.nutrition` gera uma agenda por duração prevista: gel pré-largada, intervalos entre géis, carboidrato total e por hora, um gel cafeinado conforme configuração e lembrete de água. O dashboard permite ajustar duração, horário, carboidrato por gel, intervalo e parâmetros de cafeína.

A agenda deriva dos valores configurados no perfil ou editados no painel. Ela não calcula uma estratégia personalizada por peso, clima, tolerância gastrointestinal, condição clínica ou teste de ingestão; recomenda usar somente produtos e estratégias já tolerados. A dose de café não é estimada.

### 4.10 Registro pós-prova

`farol.race_log` guarda contexto declarado manualmente: percurso, clima e temperatura percebidos, água, nutrição realmente consumida, cafeína, RPE, sintomas gastrointestinais, dor/inchaço no pé, tênis e notas. Um registro na mesma data pode ser substituído sem duplicação.

Esse histórico fica separado das métricas do Garmin e não altera TSB, carga, recuperação, classificação ou previsão. Ele ajuda a contextualizar e validar uma prova depois do evento.

## 5. Relatórios, interfaces e formatos

- **`farol.today`:** estado do dia, treino realizado, carga, recuperação, sono, padrões combinados e, quando aplicável, prova-alvo e índice de recuperação.
- **`farol.state`:** resumo de uma página por data, em Markdown; inclui domínio, confiança, streak, tendência, performance, descontinuidades e observação manual de lesões.
- **`farol.state --json`:** mesmo estado classificado em JSON estruturado, com status global e código de saída compatível com automação/consumidores externos.
- **`farol.daily-analysis`:** análise textual mais extensa, com evidências por indicador, convergência e limites de interpretação.
- **`farol.dashboard`:** dashboard HTML estático local, com carga, gráficos recentes, recuperação, sono, previsão de prova, correlações, quebras de medição, calibração, lesões, editor nutricional e registro pós-prova. Não requer servidor próprio.
- **Relatórios especializados:** calibração diária e revisão, correlações, changepoints, previsão de prova e reconciliação de fontes.

No terminal, `farol.today` mostra sparklines recentes, setas de tendência e um quadro de domínio com estado, confiança, streak e direção de 7 dias. `farol.state` apresenta símbolos de estado, confiança e streak; `farol.state --json` fornece campos de tendência e persistência para consumidores programáticos. O dashboard oferece gráficos mais ricos. Ainda não há no terminal um gráfico longitudinal dedicado ou uma barra visual de confiança.

## 6. Integridade e procedência dos dados

O fluxo principal é:

```text
Garmin → planilha data/garmin_health.xlsx → SQLite data/farol.db → relatórios
```

As tabelas diárias, atividades, HRV e carga guardam dados importados ou derivados. `farol.load` reconstrói o banco a partir da planilha. `farol.reconcile` compara em modo somente leitura a planilha e o banco, recalcula materializações numa cópia em memória e produz JSON de diferenças, incluindo origem esperada, estado da diferença e se ela pode ser reconstruída.

O modelo também preserva separações necessárias entre histórico legado e sincronização atual, além de quebras documentadas em campos que mudaram de fonte ou definição. Baselines que não têm dados comparáveis suficientes ficam sem classificação confiável em vez de misturar períodos incompatíveis.

### Dependências de rede e funcionamento local

| Fluxo | Precisa de rede? | Efeito principal |
|---|---|---|
| `farol.today`, `farol.state`, `farol.daily_analysis` e `farol.dashboard` | Não, se o banco local já estiver pronto | Leem dados locais e geram relatório/HTML. O dashboard é um arquivo local, sem servidor. |
| `farol.race_prediction`, correlações, calibração, revisão e changepoint | Não, com os dados locais disponíveis | Calculam ou exibem análises a partir do banco e/ou logs locais. |
| `farol.course` | Não | Lê o GPX local e resume o percurso. |
| `farol.injuries` e `farol.race_log` | Não | Leem ou escrevem seus logs locais em JSON. |
| `farol.load` e `farol.reconcile` | Não | Usam a planilha e o banco locais; reconciliação é somente leitura. |
| `farol.ingest` | Sim, Garmin | Consulta Garmin e atualiza planilha, banco, carga e estado derivado. |
| `farol.splits` e `farol.decoupling` | Sim, Garmin para buscar os segmentos/laps | Fazem consulta sob demanda de detalhes de atividade. |
| `farol.race_conditions` | Sim, Open-Meteo | Consulta elevação e previsão ou arquivo climático para pontos do GPX. |

Assim, “offline-first” descreve a leitura e os relatórios quando o histórico já foi sincronizado; não descreve a atualização de dados Garmin nem as consultas climáticas externas. Em caso de falha de rede na ingestão, o relatório local continua dependendo do último conjunto de dados armazenado.

## 7. Comandos disponíveis

Os nomes instaláveis estão definidos em `pyproject.toml`. Também podem ser chamados como módulos Python:

| Finalidade | Comando de módulo |
|---|---|
| Sincronizar Garmin e recalcular dados | `python -m farol.ingest --days 7` |
| Recarregar banco pela planilha | `python -m farol.load` |
| Relatório diário | `python -m farol.today` |
| Estado em Markdown ou JSON | `python -m farol.state [--date AAAA-MM-DD] [--json]` |
| Análise diária detalhada | `python -m farol.daily_analysis [--date AAAA-MM-DD]` |
| Dashboard | `python -m farol.dashboard [--date AAAA-MM-DD]` |
| Analisar tiros de uma corrida | `python -m farol.splits [--activity-id ID]` |
| Decoupling aeróbico | `python -m farol.decoupling [--activity-id ID]` |
| Gerar previsão de prova | `python -m farol.race_prediction` |
| Perfil de GPX | `python -m farol.course percurso.gpx` |
| Clima e elevação para percurso | `python -m farol.race_conditions percurso.gpx --start 2026-09-19T18:00-03:00 --duration-min 130` |
| Acrescentar calibração do dia | `python -m farol.calibration` |
| Revisar calibração manual | `python -m farol.calibration_review` |
| Ver correlações selecionadas | `python -m farol.correlations` |
| Estimar quebra de medição | `python -m farol.changepoint` |
| Registrar observação de lesão | `python -m farol.injuries add --area "local" --pain 0` |
| Registrar contexto pós-prova | `python -m farol.race_log add --date AAAA-MM-DD` |
| Comparar planilha e banco | `python -m farol.reconcile` |

Os comandos de ingestão, análise de tiros e consulta de condições externas têm efeitos ou dependências diferentes: ingestão altera dados locais e acessa o Garmin; tiros sob demanda consulta o Garmin; condições de percurso consultam um serviço meteorológico. Os demais relatórios normalmente leem os dados já armazenados, embora gravem o arquivo de saída indicado pelo comando.

## 8. O que não faz

O escopo atual não inclui:

- diagnóstico médico, tratamento ou avaliação automática de lesão;
- recomendação de treino, periodização, prescrição de ritmo ou decisão de prontidão;
- monitoramento contínuo ou alertas em tempo real;
- integração de ingestão com Strava ou Intervals.icu;
- distribuição completa do tempo de treino por zona de FC;
- cálculo farmacológico ou ajuste de métricas por medicamentos;
- confirmação automática de que uma quebra de dados é fisiológica ou técnica;
- previsão garantida de resultado de prova ou estimativa baseada em prova não observada.

## 9. Nota sobre a documentação

Na revisão, o código e os entry points de `pyproject.toml` mostraram capacidades que o `README.md` não descreve integralmente, em especial os módulos de lesões, nutrição, registro pós-prova, percurso, condições externas e reconciliação. Este documento complementa a visão geral; o README continua sendo a referência rápida de instalação e uso diário.
