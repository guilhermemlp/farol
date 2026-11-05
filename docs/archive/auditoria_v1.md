# Auditoria Técnica e Científica — Farol v1.1

**Data da auditoria**: 2026-09-04
**Escopo**: `farol/` (pacote Python), `docs/`, `tests/`, `data/garmin_health.xlsx`, `pyproject.toml`, README
**Método**: leitura completa do código-fonte (24 módulos), schema SQL, 4 documentos de modelo, suíte de testes (258 testes — **todos passando**, 13,8 s), análise do histórico git (26 commits), e pesquisa de mercado/literatura (TrainingPeaks, Strava, Garmin, WHOOP, Oura, Intervals.icu, Runalyze; Plews & Buchheit, Foster, Banister, Phillips SRI, Windred/SLEEP 2024, Gabbett ACWR, detecção de doença via wearable).
**Papel**: arquiteto de software sênior especializado em ciência de dados aplicada a esporte de corrida.

---

## Sumário executivo

O Farol é um dos projetos pessoais de análise de treino mais disciplinados que já auditei. Em um universo onde a maioria das ferramentas caseiras copia limiares fixos de artigos ("HRV < 20 = mau") ou replica dashboards vazios, o Farol acerta o mais difícil: **a epistemologia do dado** — baseline individual em vez de populacional, convergência de sinais em vez de métrica única, autocorrelação respeitada, descontinuidades de medição tratadas como cidadãs de primeira classe, e um loop de calibração com o humano no centro. Vários achados dele (a quebra de fonte do RHR em 2026-06-11, a mudança de definição do `awakenings` em 2026-08-01, o `sleep_regularity_min` fora do domínio SONO por autocorrelação da janela) são erros que produtos comerciais cometem o tempo todo — e que aqui foram **encontrados e documentados com evidência**.

A auditoria encontrou poucos defeitos e muitas oportunidades de evolução. As recomendações de maior retorno: (1) predição de prova (a meia maratona é 19/09/2026 — 15 dias), (2) LnRMSSD em vez de HRV bruto em ms, (3) auto-calibração dos pesos do índice quando o log de calibração acumular ≥14 entradas, (4) aproveitar sinais já capturados e nunca usados (Training Effect, potência, intensity minutes), (5) CI automatizado e alertas.

### Notas por dimensão

| Dimensão | Nota | Comentário curto |
|---|---|---|
| Metodologia científica | **9,0/10** | Baseline pessoal, convergência, autocorrelação, calibração com dados reais |
| Arquitetura e código | **9,0/10** | Camadas limpas, primitiva `Metric`, idempotência, zero números mágicos |
| Qualidade de dados | **8,5/10** | Proveniência rastreada, quebras documentadas; 1 inconsistência (sleep_score) |
| Testes | **9,0/10** | 258 testes, funções puras, golden sample; sem CI |
| Documentação | **9,5/10** | MODEL_V1.0→V1.1 congelados, PROFILE, CALIBRACAO — raro neste nível |
| Produto/UX | **6,0/10** | Só terminal/Markdown; sem alerta, sem gráfico, sem export |
| Automação/CI/operação | **4,0/10** | Sem CI, sem agendamento, sem código de saída de alerta |
| Segurança/privacidade | **8,0/10** | 100% local; senha em `.env` em texto plano; API não-oficial |
| Maturidade vs. mercado | **6,5/10** | Pensa melhor que a maioria, entrega menos: sem predição, sem prescrição, sem painel |
| **Média ponderada** | **~7,8/10** | Excelente fundação; a evolução é de produto, não de base |

---

## 1. O que é o Farol

**Pipeline**: `Garmin Connect → garminconnect (API não-oficial) → garmin_health.xlsx (planilha, fonte de verdade) → SQLite (recarregado da planilha) → análises → relatórios em Markdown/terminal`.

Cinco domínios conceituais (MODEL_V1.1): **CARGA** (TRIMP, CTL/ATL/TSB Banister, monotonia de Foster), **RECUPERAÇÃO** (9 campos: HRV, RHR, FC noturna, stress, Body Battery, respiração, SpO₂), **SONO** (duração, arquitetura, score; regularidade circular informativa), **PERFORMANCE** (output_preserved, deterioration_detected, tiros de intervalado), **OBSERVAÇÃO** (descontinuidades de medição). Tudo comparado contra a **janela móvel de 90 dias do próprio atleta** via z-score, com confiança derivada da cobertura real de dados.

Filosofia não-negociável: **o Farol descreve, não prescreve** (PROFILE.md, regras 7 e 9); **nenhuma métrica isolada determina overtraining** (regra 8); **o normal é o seu próprio padrão, não a população** (regra 10).

---

## 2. O que o projeto já faz excepcionalmente bem

### 2.1. Baseline individual como fundamento, não como enfeite (regra 10)

Toda métrica sai de `farol/metrics.py::build_metric` já com média 7d/30d/90d, desvio, z-score, tendência e confiança — e o dia-alvo **nunca entra na própria baseline**. Isso é exatamente o que separa ferramentas sérias de brinquedos: o Garmin, o WHOOP e o Oura calibram os seus índices individualmente por construção; o Farol faz o mesmo com total transparência de fórmula. A regra do PROFILE (FC elevada é característica observada deste atleta, não fadiga por si só) é uma lição de como o contexto clínico individual deve dirigir a interpretação — e está implementada de fato, não só documentada.

### 2.2. Convergência de sinais (regra 8) — alinhada com a clínica de overtraining

"2+ domínios fora do normal ao mesmo tempo" não é um capricho: a literatura sobre síndrome de overtraining (Kreher & Schwartz, 2016) é explícita que **nenhum biomarcador isolado define overreaching** — a avaliação exige múltiplos indicadores convergindo. O Farol institucionaliza isso em `state.convergence()` e `today.detect_patterns()`, e ainda protege contra contagem dupla: CTL/ATL/TSB/ATL-CTL derivam todos da mesma série de TRIMP e são tratados como **1 evidência** (MODEL_V1.0, domínio CARGA). Poucas ferramentas comerciais têm essa disciplina anti-dupla-contagem.

### 2.3. `signature_doenca()` — distinção doença vs. fadiga de treino, calibrada com dados reais

A assinatura "3+ de {HRV, RHR, FC noturna, respiração, SpO₂, Body Battery mín.} fora do padrão **sem** TSB carregado" tem base direta na literatura de detecção de doença via wearable — o estudo DETECT da Scripps (Radin et al., 2020) mostrou 63% de detecção pré-sintomática de COVID-19 a partir de RHR/HRV, e o estudo da Garmin (Miller et al., 2021) encontrou RHR elevado e HRV reduzido até 5,5 dias antes dos sintomas. O diferencial do Farol é que o limiar **não foi chutado**: 2-de-6 sinais disparava em 17% dos dias (frequência incompatível com evento raro), 3-de-6 caiu para 8% — calibrado contra 214 dias de histórico real antes de virar código. Isso é ciência de dados aplicada do jeito certo, e a linguagem "compatível com" (nunca "é") respeita o guarda-corpo clínico do usuário.

### 2.4. Autocorrelação levada a sério — o pecado mais comum de séries fisiológicas

- **Sem p-valor** em correlações de dados diários (`correlations.py`): p-valor ingênuo em série autocorrelacionada superestima confiança; o Farol reporta `r` **sempre com `n`** e exige ≥30 pares.
- **Correlação sobre desvios** (`delta_vs_baseline`), não valores brutos — remove a tendência lenta compartilhada que infla `r` sem causalidade.
- **`sleep_regularity_min` fora do domínio SONO**: a janela de 14 dias tem 13 pontos compartilhados com o dia anterior; alimentar streak/convergência com ela inflou um episódio real de 7 para 15 dias. A decisão foi tomada com evidência (teste `test_caso_real_2026_08_21_a_2026_09_03`), não por intuição. A maioria dos produtos comerciais cometeria esse erro — o Farol o **documentou como descoberta**.
- **Mesmo tratamento para `hrv_cv`** (janela 7d) e **VO2max do Garmin** (sinal lento, valor repetido) — ambos fora da convergência com justificativa explícita.

### 2.5. Descontinuidades de medição como cidadãs de primeira classe

O RHR dá um degrau de +5,9 bpm em 11/06/2026 (troca de pipeline) e o `awakenings` muda 19× em 01/08/2026 (mudança de definição da API). O Farol: (a) rastreia `source` por linha, (b) **nunca cruza baselines entre fontes** (`respect_source=True`), (c) registra as quebras em `measurement_breakpoints`, (d) implementou `farol/changepoint.py` (CUSUM, Page 1954) para achar a *próxima* quebra antes de doer — validado contra as duas quebras conhecidas (achou as duas, a poucos dias das datas reais; a janela local de 14 dias perdia o RHR porque o shift de +5,9 bpm era pequeno demais para o poder estatístico de janela curta). E, disciplina rara: **estimativa para revisão humana, nunca classificação automática** — porque uma mudança abrupta de nível pode ser quebra de medição *ou* mudança fisiológica real.

### 2.6. Decoupling aeróbico (Pa:Hr) implementado corretamente

O `farol/decoupling.py` faz o que o TrainingPeaks/Uphill Athlete descrevem: Efficiency Factor = velocidade/FC **ponderado por distância**, divisão por **metade da distância acumulada** (não por contagem de laps), exclusão do lap final parcial (<200 m), e reporta a variação % entre metades. Detalhes que a maioria das implementações amadoras erra: laps de tamanho variável, lap final parcial, ponderação por distância. O aviso honesto "<5% é citado na literatura, **não validado no seu histórico**" segue a mesma ética de calibração do resto do projeto.

### 2.7. Eficiência de corrida a pace fixo — superior à média do mercado amador

Correr mais rápido melhora qualquer indicador bruto (bpm/spm/ms/cm) sem ganho real de forma — o Farol regride indicador ~ pace e **lê a reta num pace de referência fixo**, comparando 1ª vs 2ª metade do histórico com o **erro padrão da previsão** como barra de significância ("sem mudança detectável dentro de ±2,1 bpm" em vez de comemorar ruído). Isso é metodologia de nível de artigo (running economy), e a distinção contínua vs. tiro-a-tiro (trabalho sem aquecimento/recuperação) resolve um confundimento real.

### 2.8. TRIMP com âncoras reais, zonas por LTHR

- `trimp()` usa **HRmax real validada em prova (192 bpm)** e **RHR do dia**, não HRmax estimada por idade (erro clássico; fórmulas de idade têm erro típico de ±10-20 bpm).
- `zones.py` usa **LTHR do Garmin (método de limiar de lactato)** quando disponível, com fallback Karvonen — alinhado com a prática de alta performance (zonas por limiar, não por % de FCmax).
- Monotonia/strain de Foster (1998): monotonia > 2,0 como marcador clássico de treino repetitivo, com dias de descanso contando como 0 no cálculo (são eles que criam a variação que protege).

### 2.9. Sono: regularidade circular com base na literatura atual

`sleep_regularity.py` usa **desvio-padrão circular** (o horário cruza meia-noite: 23h50 e 00h10 são 20 min, não 1.420) — proxy do Sleep Regularity Index (Phillips et al., 2017). A literatura é forte e recente: o estudo do UK Biobank publicado na SLEEP (Windred et al., 2024) mostrou que regularidade de sono prediz mortalidade por todas as causas (20-48% menor) e mortalidade cardiometabólica (22-57% menor) **independente da duração**. O Farol cita isso corretamente e ainda respeita a cobertura parcial (~40%) com NULL → confiança degradada, nunca erro.

### 2.10. HRV CV (7d) como complemento da média — Plews & Buchheit

O CV do HRV de 7 dias **colapsando** (ficando artificialmente estável) é indicador precoce de overreaching não-funcional, às vezes **antes** da média cair (Plews et al., 2013; Buchheit, 2014). O Farol mostra CV_7d vs CV_30d ao lado da média — e, corretamente, **sem bucket automático**, porque não existe limiar validado de "quanto é colapso" nem na literatura nem no histórico do usuário. É o tipo de humildade estatística que falta em produto comercial.

### 2.11. Loop de calibração fechado — diferencial real sobre apps black-box

`calibration.py` gera a entrada diária; `calibration_review.py` mede concordância entre o índice e a percepção real preenchida à mão — **só com ≥14 entradas** (mesmo número de CALIBRACAO.md). E há um **segundo juiz**: o `training_readiness_score` do próprio Garmin, classificado pela mesma janela pessoal de 90 dias (nunca o limiar genérico do Garmin — que repetiria o erro que o resto do Farol evita). A pergunta "o Farol bate mais com a realidade quando concorda com o Garmin?" é uma hipótese testável e honesta. Nenhum dos apps de mercado expõe esse loop de forma auditável.

### 2.12. Privacidade radical e simplicidade operacional

Sem servidor, sem fila, sem nuvem: dados 100% locais (SQLite + xlsx), relatório diário offline em ~0,2 s (splits buscados uma vez no ingest, não a cada execução), planilha como fonte de verdade editável à mão com reload idempotente (`ON CONFLICT DO UPDATE` preservando valor antigo quando o novo é NULL). Isso é uma escolha de arquitetura legítima e defensável — e um diferencial de privacidade contra WHOOP/Oura/Garmin Connect, que vivem da sua biometria na nuvem.

### 2.13. Documentação de modelo versionada e congelada

MODEL_V1.0 congelado como golden sample, MODEL_V1.1 sucedendo com delta explícito, decisões registradas com o *porquê* e com o *achado que as motivou* (ex.: por que `body_battery_min` saiu do domínio: CV ~74% dia a dia, marca atencao em 55% dos dias contra 16-29% dos demais campos — validado rodando o pipeline real). Pouquíssimos projetos, mesmo comerciais, documentam decisões de modelo com esse rigor de rastreabilidade.

### 2.14. Engenharia de software sólida

- 258 testes passando, maioria em SQLite em memória, funções puras testáveis (`build_metric`, `_split_and_fit`, CUSUM, SD circular), golden sample contra o banco real.
- Idempotência em todo lugar (ingest, load, recompute de CTL/ATL/TSB com seed).
- Camadas com inversão de dependência respeitada (`daily_state_builder` fora de `db.py` de propósito; `state.gather()` como fonte única de classificação para DAILY_STATE e DAILY_ANALYSIS).
- Correções de bugs documentadas com o bug (`_media_dos_presentes`: campo ausente devolvia 0.0; agora None).
- Convenções: `NO_COLOR`, `isatty()` para cor, fuso via env var com justificativa de dependência (sem zoneinfo no Windows).

---

## 3. Benchmark de mercado

| Capacidade | **Farol** | TrainingPeaks | Strava | Garmin | WHOOP | Oura | Intervals.icu |
|---|---|---|---|---|---|---|---|
| Carga de treino | TRIMP + CTL/ATL/TSB (Banister, τ 42/7) | TSS + PMC (Coggan) | Relative Effort + Fitness/Freshness | Acute Load proprietário | Strain (proprietário) | Activity Score | TSS/PMC completo |
| Baseline | **Individual, 90d móvel, z-score** | Individual (longo prazo) | Individual (baseline própria) | Individual (HRV status) | Individual (30d) | Individual | Individual |
| Recuperação | Índice ponderado (HRV/sono/RHR/TSB) + 9 campos | — | — | Body Battery + Training Readiness | Recovery (HRV/RHR/sono/resp.) | Readiness | — |
| HRV | Média + **CV 7d/30d** (Plews & Buchheit) | — | — | Status 7d balançado | CV + LnRMSSD | Sim | Sim |
| Decoupling Pa:Hr | **Sim, correto (EF ponderado)** | Sim (EF, NGP) | — | — | — | — | Sim |
| Eficiência a pace fixo (tendência) | **Sim (regressão com erro padrão)** | Parcial (EF por sessão) | — | Parcial (pace pro) | — | — | Parcial |
| Regularidade de sono | SD circular 14d (informativo) | — | — | Parcial | Sim | Sim (SRI) | — |
| Predição de prova | **Não** | Sim (métricas de corrida) | Sim (tempo estimado) | Sim (race predictor) | Parcial | — | Sim |
| Prescrição/planos | **Não (de propósito)** | Sim (planos) | Sim (treinos) | Sim (Daily Suggested) | Sim | Sim | Parcial |
| Correlações entre domínios | **Sim (lagged, desvios)** | — | — | — | Insights (básico) | Insights | Parcial |
| Loop de calibração auditável | **Sim (humano + 2º juiz)** | — | — | — | — | — | — |
| Tratamento de quebra de medição | **Sim (CUSUM + split)** | — | — | — | — | — | — |
| Privacidade | **100% local** | Nuvem | Nuvem | Nuvem | Nuvem | Nuvem | Nuvem |
| Custo | R$ 0 (seu tempo) | US$ 20/mês (atleta) | US$ 80/ano | Hardware | US$ 30/mês | US$ 6-10/mês | US$ ~30/ano |
| Automação | CLI manual (agendar) | API/automática | Automática | Automática | Automática | Automática | Automática |

**Leitura**: o Farol compete com os líderes no *pensamento* (baseline individual, convergência, decoupling, calibração) e perde feio na *entrega* (sem predição de prova, sem prescrição, sem alerta, sem gráfico, sem agendamento). É um "motor de F1 sem carroceria": a parte difícil já está resolvida; falta a camada de produto.

---

## 4. Base científica das métricas — mapeamento e veredito

| Métrica | Método usado | Referência | Como o mercado faz | Veredito |
|---|---|---|---|---|
| TRIMP | `duração × ΔFCreserva × 0,64·e^1,92r`, HRmax real | Banister et al., 1975; Mortensen et al. | TSS (Coggan) com potência/NGP | ✅ Correta e honesta para corrida sem potência; ver F3/F6 |
| CTL/ATL/TSB | EMA com τ=42/7 | Coggan/TrainingPeaks PMC | Idem (TSS no lugar de TRIMP) | ✅ Padrão de mercado |
| Monotonia/Strain | média/sd semanal; strain = total × monotonia | Foster, 1998 (Med. Sci. Sports Exerc.) | AthleteMonitoring, TrainingPeaks (parcial) | ✅ Fiel ao original |
| ACWR (ATL/CTL) | razão de EMAs | Gabbett, 2016 | Padrão no esporte coletivo | ⚠️ Ver crítica (seção 5.3) |
| HRV (média + z) | média móvel 90d, ms brutos | Plews & Buchheit | WHOOP/Oura/Garmin | ⚠️ Melhor com LnRMSSD (F2) |
| CV do HRV 7d | CV da janela móvel | Plews et al., 2013; Buchheit, 2014 | WHOOP (LnRMSSD CV) | ✅ Concepção correta, sem bucket (correto) |
| Decoupling Pa:Hr | EF = velocidade/FC ponderado por distância, metades por distância | TrainingPeaks, Uphill Athlete | TrainingPeaks, Intervals.icu | ✅ Implementação de referência |
| Eficiência (FC~pace) | Regressão linear lida a pace fixo, 1ª vs 2ª metade, erro padrão | Economia de corrida (Conley & Krahenbuhl) | Poucos fazem | ✅ Superior ao mercado amador |
| Regularidade de sono | SD circular de sleep_start, 14d | Phillips et al., 2017 (SRI); Windred et al., 2024 (SLEEP) | Oura (SRI completo) | ✅ Proxy honesto (não tem série minuto-a-minuto) |
| Detecção doença | 3+ sinais agudos fora do padrão sem TSB carregado | Radin et al., 2020 (DETECT); Miller et al., 2021 (Garmin) | Garmin Body Battery/Readiness | ✅ Calibrado no próprio histórico (17%→8%) |
| Zonas de FC | LTHR do Garmin (floors) | Método de limiar de lactato | Garmin, TrainingPeaks | ✅ Melhor que Karvonen genérico |
| VO2max | Garmin, só tendência (nunca âncora) | — | Garmin (estimativa de relógio) | ✅ Uso correto (não comparável ao exame, 53,8) |
| CUSUM (changepoint) | Soma cumulativa dos desvios padronizados | Page, 1954 | Raro | ✅ Validado nas 2 quebras conhecidas |

---

## 5. Auditoria técnica detalhada

### 5.1. Arquitetura e dados

**Fluxo**: Garmin → xlsx (fonte de verdade) → SQLite → análises → MD. **Força**: a planilha como fonte de verdade dá correção manual auditável e reaproveita o histórico do pipeline anterior. **Custo**: `openpyxl._find_row` varre a planilha inteira por upsert → O(n²) por sincronização; hoje irrelevante (poucos milhares de linhas), mas é o primeiro gargalo se o histórico crescer. Alternativa futura: manter xlsx como artefato de export e SQLite como canônico (ou CSV versionado), sem mudar a UX de edição manual.

**`errors` column nunca é populada** (`garmin_client._safe` engole exceções e devolve `None` em silêncio). Um endpoint que falhar produz um dia com dado parcial sem nenhum registro — a própria arquitetura de `measurement_breakpoints` existe porque o Garmin muda as coisas; um campo de cobertura/erro por dia fecharia essa lacuna (F9).

### 5.2. Qualidade de dados — achados

| # | Achado | Severidade | Onde |
|---|---|---|---|
| D1 | **`sleep_score` documentado com descontinuidade em 2026-08-01 no schema, mas ausente de `measurement_breakpoints` e sem split de baseline no código** (só `rhr` divide por fonte; `awakenings` é evitado usando `awake_minutes`). Ou a quebra do sleep score deve entrar no registro + política `split_by_date`, ou a nota do schema deve sair — hoje documentação e execução divergem. | **ALTA** (integridade da interpretação de SONO) | `schema.sql:158`, `daily_state_builder.py:_MEASUREMENT_BREAKPOINTS`, `state.py:_metric` |
| D2 | HRV em **ms brutos** (`lastNightAvg`), não LnRMSSD. A literatura de monitoramento diário (Plews & Buchheit) usa o log natural do RMSSD — distribuição menos assimétrica, CV mais estável, z-scores mais bem-comportados. | MÉDIA | `garmin_client.py`, `metrics.py` |
| D3 | Distância legada em unidade desconhecida — corretamente anulada no load, mas isso reduz as janelas de eficiência a ~3 meses (`source='garmin'`). Aceito e documentado; manter. | Baixa (aceita) | `sheets.py`, README |
| D4 | Cobertura parcial (~40% HRV/sono, ~60% sono) tratada com confiança — correto; nunca interpolar (o Farol não interpola; manter assim). | — | `metrics.py` |

### 5.3. Metodologia — críticas e ressalvas científicas

1. **ATL/CTL (ACWR) tem literatura mista**: o "sweet spot" 0,8–1,3 de Gabbett (2016) foi criticado por problemas metodológicos (Impellizzeri et al.; matemática de razão com denominador pequeno; autocorrelação estrutural entre numerador e denominador). O Farol usa TSB como único sinal de CARGA (bom — é o menos ruidoso), mas o `atl_ctl_ratio` é exibido sem z-score. **Recomendação**: manter exibido como contexto, nunca como sinal — o Farol já faz isso; formalizar no doc.
2. **Z-scores assumem normalidade** da janela de 90 dias. Campos com skew (Body Battery mín., por exemplo) podem classificar mal. A saída do próprio Farol (`body_battery_min` com CV 74% fora do domínio) é a prova viva disso. **Evolução**: percentis empíricos no lugar de sd para campos assimétricos, ou manter z mas documentar a premissa (F18).
3. **Segundo juiz parcialmente circular**: o `training_readiness_score` do Garmin deriva dos mesmos sinais (HRV, sono, carga) que o índice do Farol — concordância entre os dois é esperada em parte por construção, e os dois podem errar juntos. O desenho é bom (comparar metodologias é útil), mas a pergunta forte continua sendo a percepção real.
4. **`score_from` e as âncoras z=0→6,5, −0,5→6,0, −1,5→3,5** são arbitrárias e documentadas como tal (CALIBRACAO.md). Correto enquanto o loop de calibração não tiver dado; a auto-calibração (F4) resolve.
5. **Tendência de 7d vs 30d em unidades de sd** (`_TREND_SD = 0.35`) e `longitudinal.trend` (primeiro vs último ponto com dado na janela de 7d) são simplificações deliberadas e documentadas. Opções mais robustas: Theil-Sen ou EWMA — vale avaliar quando houver mais história, sem pressa.

### 5.4. Testes

258 testes passando (README diz 209 — **documentação desatualizada**). Cobertura boa de TRIMP, CTL/ATL/TSB, `Metric`, zonas, splits, decoupling, correlação, CUSUM, regularidade, longitudinal, ETL da planilha. **Lacunas**:
- **Sem CI** (nenhum `.github/`): a suíte só roda se alguém rodar. Primeiro item de automação.
- Sem testes de propriedade para funções estatísticas (ex.: invariantes do CUSUM, SD circular com dados conhecidos — há alguns, mas propriedades aleatórias adicionariam robustez).
- `tests/test_db.py`/`test_integration.py` dependem do `data/farol.db` real? Verificado: golden sample roda idempotente contra o banco real — bom, mas o banco real não está versionado; se um CI futuro rodar em ambiente limpo, precisará de fixture.

### 5.5. Segurança e privacidade

- **Positivo**: 100% local, token do Garmin em `.garmin_token/` (não versionado), `.env` ignorado.
- **Ressalvas**: senha do Garmin em texto plano no `.env` (aceitável para ferramenta pessoal; documentar que é o trade-off); biblioteca `garminconnect` é **não-oficial** (manutenção externa, quebra de API possível — o próprio histórico do `awakenings` prova isso; mitigação existente: source tagging + changepoint + `_safe`). Recomendo **pin rígido da versão** (já há `<0.4`) e olho nas releases upstream.
- O `gps` é coletado? `gps: None` no extractor — bom, **não** baixa trilha GPS; mantém a pegada de dados mínima.

### 5.6. Operação e produto

- Só CLI: nada de alerta, notificação, gráfico, web, export JSON/CSV, API. Tudo deliberado (README lista como fora de escopo), mas é onde o valor fica preso: um `farol.today` que ninguém roda não alerta ninguém.
- **Sem código de saída de alerta**: um agendador não sabe se hoje é dia vermelho. `today.py`/`state.py` não expõem status via exit code — item barato e de alto impacto (F11).
- `python -m farol.calibration` exige `ingest --days 2` na rotina — ok.

---

## 6. Achados e oportunidades de evolução (priorizados)

Legenda: **P0** = faz diferença em dias/semanas; **P1** = meses; **P2** = evolução contínua. Esforço: S/M/L. Impacto: ciência (precisão da leitura), produto (entrega), operação (confiabilidade).

| # | Oportunidade | Prioridade | Esforço | Impacto | Base/Justificativa |
|---|---|---|---|---|---|
| F1 | **Predição de prova** (VDOT de Jack Daniels a partir de provas recentes + modelo de Cameron para 21,1 km; ou regressão de pace ~ distância do próprio histórico). A meia é 19/09 — 15 dias; mostrar pace-alvo e TSB recomendado para o dia (coach-alvo: TSB positivo no race day) transforma o Farol de descritivo em ferramenta de decisão, **sem prescrever treino** (mantém regra 9: só estima, não manda). | P0 | M | Ciência+Produto | Daniels VDOT; Cameron; TrainingPeaks/Garmin race predictor |
| F2 | **LnRMSSD**: adicionar transformação log ao HRV (coluna derivada ou na análise) para média, z e CV — alinhar com Plews & Buchheit e com WHOOP. Pelo menos documentar a decisão de usar ms bruto. | P1 | S | Ciência | Plews & Buchheit 2013/2014 |
| F3 | **Auto-calibração dos pesos do `recovery_index`** quando `CALIBRACAO_LOG` tiver ≥14 entradas: regressão/logístico da percepção real sobre as notas por domínio → pesos ótimos por atleta, com fallback para os padrão. O loop de calibração já existe; falta o ajuste automático. | P1 | M | Ciência+Produto | Próprio `calibration_review.py` |
| F4 | **Aproveitar sinais capturados e nunca usados**: `training_effect_aerobic/anaerobic` (escala 1-5 do Garmin, validada), `avg_power/max_power`, `intensity_minutes`, `stride_length` — hoje extraídos no schema/planilha e **mortos**. TE aeróbico pode enriquecer CARGA sem nova extração; potência de corrida abre TSS/NGP-like e decoupling Pw:Hr. | P1 | S/M | Ciência+Produto | Coggan (TSS); Garmin Training Effect |
| F5 | **Registrar `sleep_score` como breakpoint** (ou remover a nota do schema): decidir explicitamente a política `split_by_date` e aplicá-la na baseline, como já é feito para RHR. | P1 | S | Qualidade | D1 acima |
| F6 | **Peso de treinos não-corrida (força/pilates) na carga**: TRIMP por FC subestima força. Opção: correção por duração/tipo ou session-RPE manual (Foster) opcional por atividade. | P1 | M | Ciência | Foster 1998 (session-RPE) |
| F7 | **Capturar temperatura** dos laps (`averageTemperature` já existe na API — o próprio `decoupling.py` documenta isso em aberto). Temperatura é confundidor clássico de deriva de FC (estresse térmico aumenta drift) — usar como covariável ou bandeira no decoupling/eficiência. | P1 | S | Ciência | Literatura de cardiac drift térmico |
| F8 | **CI (GitHub Actions)**: rodar `unittest` + `ruff` em todo push; adicionar `[tool.ruff]` no pyproject (o `.ruff_cache` existe, config não). | P1 | S | Operação | Boas práticas |
| F9 | **Cobertura/erro por dia**: popular `daily.errors` (ou coluna derivada) quando um endpoint falha no `_safe`, e expor aviso no relatório ("hoje veio sem SpO₂: endpoint falhou") — hoje a falha é silenciosa. | P1 | S | Qualidade | Resiliência da API não-oficial |
| F10 | **Saída estruturada + exit code**: `farol.state --json` e exit codes (0 ok, 1 atenção, 2 alerta) para agendador/notificação; base para o Task Scheduler já documentado em CALIBRACAO.md. | P1 | S | Produto | Operação |
| F11 | **Simetria corporal (lesão)**: capturar `ground_contact_time_balance` (esquerda/direita) e vertical ratio do Garmin — assimetria é o sinal de lesão mais barato disponível e não é capturado. | P2 | M | Ciência | Garmin Running Dynamics; literatura de assimetria |
| F12 | **Eficiência de sono** (total/(total+awake)) e **regularidade de horário de acordar** (sleep_end) — deriváveis do que já existe, complementam SONO sem nova extração. | P2 | S | Produto | SRI; prática clínica do sono |
| F13 | **Percentis empíricos** para campos assimétricos no lugar de z puro (Body Battery min. etc.) — ou documentar a premissa de normalidade por campo. | P2 | M | Ciência | D2/5.3.2 |
| F14 | **Alertas via percepção cruzada**: quando `signature_doenca` disparar, sugerir (não impor) registrar sintoma no CALIBRACAO_LOG — fecha o loop doença com dado real. | P2 | S | Produto | Radin 2020; próprio desenho |
| F15 | **CUSUM múltiplos pontos** (PELT/BinSeg) para séries com mais de uma quebra; hoje é um único candidato por campo (documentado). | P2 | M | Ciência | Killick et al. PELT |
| F16 | **Export JSON/CSV + MCP server** opcional: permitir que outras ferramentas (e agentes de IA) consultem `farol_daily_state` sem tocar no SQLite — o README lista MCP como fora de escopo; vale reabrir depois de F1-F4. | P2 | M | Produto | Ecossistema |
| F17 | **Multi-atleta (`athlete_id`)**: a decisão arquitetural mais cara do futuro. Hoje tudo é 1-para-1; se o Farol virar produto/ferramenta para mais pessoas, o schema inteiro migra. Não fazer agora; **registrar a decisão** (ADR) para não pintar em canto. | P3 | L | — | Governança |
| F18 | **README desatualizado**: 209 → 258 testes; incluir o `parecer técnico 2026-09-04` (referenciado em MODEL_V1.1 mas fora do repo) em `docs/` para rastreabilidade. | P3 | S | Docs | — |

---

## 7. Riscos

| Risco | Prob. | Impacto | Mitigação existente | Mitigação recomendada |
|---|---|---|---|---|
| API não-oficial do Garmin muda (já aconteceu: `awakenings`, sleepScores aninhado) | Alta | Médio | `_safe`, source tagging, CUSUM, breakpoints | F9 (erro/cobertura por dia); pin de versão; monitorar releases |
| Thresholds heurísticos (-0,5/-1,5 sd; 3-de-6) sem validação externa | Média | Médio | Loop de calibração + CALIBRACAO.md | F3 (auto-calibração); F13 (percentis) |
| Janela curta de dados pós-11/06 (eficiência usa só ~3 meses) | Média | Médio | Verdicts com erro padrão; linguagem cautelosa | Deixar janela crescer; F7 (temperatura) |
| Single-point failure: `farol.today` não roda → ninguém vê alerta | Alta | Alto | CALIBRACAO.md sugere Task Scheduler | F10 (exit codes + agendamento); notificação opcional |
| Confundimento doença vs. fadiga (TSB proxy imperfeito) | Baixa | Alto (clínico) | "Compatível com", nunca diagnóstico; TSB como pré-requisito | Manter guarda-corpo; F14 (registrar sintomas) |
| Excel binário versionado → conflitos de merge/lixo no git | Baixa | Baixo | xlsx é a fonte de verdade; banco recarregável | Aceitar ou migrar canônico para SQLite+export (F-futuro) |

---

## 8. Roadmap recomendado

**Fase A — Imediata (antes da meia de 19/09)**: F1 (predição de prova — o mais valioso agora), F10 (exit codes), F5 (sleep_score), F8 (CI). Tudo em dias.
**Fase B — 1-2 meses**: F2 (LnRMSSD), F4 (sinais mortos: TE, potência), F9 (erro/cobertura), F7 (temperatura).
**Fase C — trimestre**: F3 (auto-calibração), F6 (carga de força), F12 (eficiência de sono), F13 (percentis), F15 (CUSUM múltiplo).
**Fase D — decisão de produto**: F17 (multi-atleta?) + F16 (export/MCP) + painel web ou visualização (o sparkline de terminal é charmoso, mas a lacuna de dashboard é a maior diferença de percepção de valor vs. mercado).

---

## 9. Conclusão

O Farol não é um "app de treino caseiro"; é um **laboratório de ciência de dados de fisiologia do esporte com disciplina de engenharia de software**. A arquitetura de decisões — baseline pessoal, convergência, autocorrelação, descontinuidades, calibração com humano no loop — está no nível (e em pontos acima) do que WHOOP, Oura e TrainingPeaks fazem, com a vantagem de ser 100% auditável e local. Os 258 testes, os modelos congelados e o histórico de decisões documentadas tornam este projeto **mantível e evoluível** — a maioria esmagadora dos projetos pessoais deste tipo morre pela ausência exatamente dessas três coisas.

Onde o Farol perde para o mercado não é em inteligência, é em **entrega**: não há predição de prova, alerta, agendamento, dashboard ou export. A boa notícia: a fundação é tão sólida que cada item da Fase A é barato. A meia maratona de 19/09/2026 é a melhor oportunidade de validação que este projeto poderia ter — a predição de prova calibrada no próprio histórico, comparada contra o resultado real, seria o teste derradeiro do modelo.

---

## 10. Referências

**Carga e performance**
- Banister, E.W. et al. (1975). A systems model of training for athletic performance. *Aust. J. Sports Med.* — base do TRIMP.
- Coggan, A. TSS/CTL/ATL/TSB (Performance Management Chart), TrainingPeaks: https://www.trainingpeaks.com/learn/articles/the-science-of-the-performance-manager/ e https://www.trainingpeaks.com/learn/articles/what-is-the-performance-management-chart/
- Foster, C. (1998). Monitoring training in athletes with reference to overtraining syndrome. *Med. Sci. Sports Exerc.* 30(7):1164-8. https://pubmed.ncbi.nlm.nih.gov/9662690/
- Gabbett, T. (2016). The training-injury prevention paradox. *Br. J. Sports Med.* — ACWR. Críticas: Impellizzeri et al.; https://www.globalperformanceinsights.com/post/has-the-acute-chronic-workload-ratio-been-debunked ; https://www.scienceforsport.com/acutechronic-workload-ratio/
- Strava Fitness & Freshness / Relative Effort: https://support.strava.com/en-us/articles/15402032-fitness-freshness ; https://support.strava.com/en-us/articles/15401794-relative-effort

**HRV e recuperação**
- Plews, D., Laursen, P., Stanley, J., Kilding, A., Buchheit, M. (2013). Training adaptation and heart rate variability in elite endurance athletes: opening the door to effective monitoring. *Sports Med.*
- Plews et al. (2013). Heart rate variability in elite triathletes, is variation in variability the key to effective training? *Eur. J. Appl. Physiol.* https://pubmed.ncbi.nlm.nih.gov/22367011/
- Buchheit, M. (2014). Monitoring training status with HR measures: do all roads lead to Rome? *Front. Physiol.* https://www.frontiersin.org/journals/physiology/articles/10.3389/fphys.2014.00073/full
- WHOOP Recovery methodology: https://www.whoop.com/us/en/thelocker/how-does-whoop-recovery-work-101/ ; HRV vs Oura (Sensai, 2026): https://www.sensai.fit/blog/garmin-body-battery-vs-whoop-recovery-vs-oura-readiness-how-calculated-2026
- Oura Readiness Score: https://support.ouraring.com/hc/en-us/articles/360025589793-Readiness-Score
- Garmin Body Battery / Training Readiness: https://www.garmin.com/en-US/garmin-technology/health-science/body-battery/ ; https://www.garmin.com/en-US/garmin-technology/running-science/physiological-measurements/training-readiness/

**Decoupling e economia de corrida**
- TrainingPeaks, Aerobic Decoupling (Pa:Hr) e Efficiency Factor: https://help.trainingpeaks.com/hc/en-us/articles/204071724-Aerobic-Decoupling-Pw-Hr-and-Pa-HR-and-Efficiency-Factor-EF ; https://www.trainingpeaks.com/coach-blog/aerobic-endurance-and-decoupling/ ; Uphill Athlete: https://uphillathlete.com/aerobic-training/durability-training-efficiency-factor-and-decoupling-for-endurance-athletes/
- Daniels, J. *Daniels' Running Formula* (VDOT). VDOT e predição de prova: https://vdoto2.com/calculator ; Cameron model via fellrnr: https://fellrnr.com/wiki/VDOT_Results

**Sono**
- Phillips, A.J.K. et al. (2017). Irregular sleep/wake patterns are associated with poorer academic performance and delayed circadian and sleep/wake timing. *Sci. Rep.* (SRI). https://www.nature.com/articles/s41598-017-03171-4
- Windred, D.P. et al. (2024). Sleep regularity is a stronger predictor of mortality risk than sleep duration: a prospective cohort study. *SLEEP*. https://pmc.ncbi.nlm.nih.gov/articles/PMC10782501/ ; https://academic.oup.com/sleep/article/47/1/zsad285/7344663

**Detecção de doença via wearable**
- Radin, J.M. et al. (2020). Assessment of Prolonged Physiological and Behavioral Changes Associated With COVID-19 Infection Using a Wearable. *JAMA Netw. Open* (Scripps DETECT). https://pmc.ncbi.nlm.nih.gov/articles/PMC9020268/
- Miller, D.J. et al. (2021). Analyzing changes in resting heart rate and HRV to detect COVID-19 (Garmin). *Open Heart*.

**Outros**
- Page, E.S. (1954). Continuous inspection schemes (CUSUM). *Biometrika*.
- Kreher, J.B., Schwartz, J.B. (2016). Overtraining syndrome. *OAJSM*. https://www.dovepress.com/diagnosis-and-prevention-of-overtraining-syndrome--an-opinion-on-educa-peer-reviewed-fulltext-article-OAJSM

---

*Documento gerado a partir de auditoria completa do repositório (leitura de 24 módulos, 4 documentos de modelo, 258 testes executados, 26 commits) e pesquisa de literatura/market intelligence em 2026-09-04. Toda métrica citada como "do usuário" vem de PROFILE.md e dos relatórios gerados pelo próprio Farol.*