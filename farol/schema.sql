-- O banco espelha a planilha: toda coluna extraída do Garmin que vai para
-- garmin_health.xlsx tem uma coluna aqui. Ver farol/sheets.py::_HEADERS.
--
-- Dois nomes divergem do cabeçalho da planilha, por compatibilidade com
-- código que já existia quando a tabela era menor:
--   daily.rhr               <- coluna "resting_hr" da aba Daily Health
--   activities.duration_min <- coluna "duration_minutes" da aba Activities

-- daily = aba "Daily Health" + aba "Sleep", unidas por data.
CREATE TABLE IF NOT EXISTS daily (
    date TEXT PRIMARY KEY,

    -- Daily Health
    steps INTEGER,
    floors INTEGER,
    calories_total INTEGER,
    calories_active INTEGER,
    rhr INTEGER,
    max_hr INTEGER,
    avg_hr INTEGER,
    stress_avg INTEGER,
    stress_duration_minutes REAL,
    body_battery_max INTEGER,
    body_battery_min INTEGER,
    spo2_avg REAL,
    spo2_min REAL,
    respiration_avg REAL,
    intensity_minutes INTEGER,
    hydration_ml REAL,
    hrv_avg REAL,
    hrv_status TEXT,
    training_readiness_score INTEGER,
    training_status TEXT,
    vo2max REAL,
    recovery_time_minutes REAL,
    errors TEXT,

    -- Sleep
    sleep_start TEXT,
    sleep_end TEXT,
    total_sleep_minutes REAL,
    deep_sleep_minutes REAL,
    light_sleep_minutes REAL,
    rem_sleep_minutes REAL,
    awake_minutes REAL,
    awakenings INTEGER,
    sleep_score INTEGER,
    sleep_quality TEXT,
    avg_sleep_stress REAL,
    night_hr_avg INTEGER,
    spo2_avg_sleep REAL,
    spo2_min_sleep REAL,
    spo2_max_sleep REAL,

    -- 'garmin' | 'legado'. O histórico anterior a 11/06/2026 veio do pipeline
    -- do analise-garmin e NÃO é intercambiável com o do sync atual: o RHR dá um
    -- degrau de +5,9 bpm exatamente na fronteira (70,2 antes / 76,1 depois, sd
    -- ~3,5 dos dois lados) enquanto a FC noturna atravessa estável (~84). Dois
    -- campos do mesmo dia se comportando diferente na mesma data indica troca
    -- de mapeamento, não mudança fisiológica — por isso baseline de RHR não
    -- deve cruzar fontes. Ver farol/metrics.py.
    source TEXT
);

-- activities = aba "Activities", com trimp calculado localmente.
CREATE TABLE IF NOT EXISTS activities (
    activity_id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    type TEXT,
    name TEXT,
    start_time TEXT,
    duration_min REAL,
    distance_km REAL,
    avg_pace_min_km REAL,
    avg_speed_kmh REAL,
    max_speed_kmh REAL,
    calories INTEGER,
    training_effect_aerobic REAL,
    training_effect_anaerobic REAL,
    avg_hr INTEGER,
    max_hr INTEGER,
    avg_cadence REAL,
    stride_length REAL,
    ground_contact_time REAL,
    vertical_oscillation REAL,
    elevation_gain_m REAL,
    elevation_loss_m REAL,
    avg_power REAL,
    max_power REAL,
    gps TEXT,
    moving_minutes REAL,
    vertical_ratio REAL,
    strava_activity_id TEXT,
    historical_source TEXT,
    running_dynamics_source TEXT,
    rpe REAL,
    shoe TEXT,
    air_temperature_c REAL,
    relative_humidity_pct REAL,
    gap_pace_min_km REAL,
    historical_only INTEGER,
    splits TEXT,
    -- Estado da coleta de typed splits. NULL só existe em bancos anteriores à
    -- migração; o código o interpreta sem adivinhar o histórico.
    splits_status TEXT,
    trimp REAL,

    -- 'garmin' (activity_id > 0) | 'legado' (id negativo, vindo da planilha).
    -- Distância/pace legados sem historical_source verificado são anulados.
    -- Valores reconciliados do Strava permanecem em source='legado': não
    -- entram nas baselines do Garmin nem na classificação diária.
    source TEXT
);

CREATE INDEX IF NOT EXISTS idx_activities_date ON activities(date);
CREATE INDEX IF NOT EXISTS idx_activities_type_date ON activities(type, date);

-- hrv = aba "HRV". hrv_avg/hrv_status repetem daily de propósito: a aba é a
-- fonte, daily é a junção de leitura. `raw` guarda a resposta da API inteira.
CREATE TABLE IF NOT EXISTS hrv (
    date TEXT PRIMARY KEY,
    hrv_avg REAL,
    hrv_status TEXT,
    weekly_avg REAL,
    last_night_5min_high REAL,
    baseline TEXT,
    raw TEXT
);

CREATE TABLE IF NOT EXISTS training_load (
    date TEXT PRIMARY KEY,
    trimp_day REAL,
    ctl REAL,
    atl REAL,
    tsb REAL
);

-- farol_daily_state = Structured daily athlete state data across 5 domains
-- Based on MODEL_V1.0.md; 18 campos originais (congelados a partir de
-- relatorio_2026-09-02-v1.0.md) + 5 campos acrescentados em 2026-09 (parecer
-- técnico de 2026-09-04): respiration_avg, spo2_avg, spo2_min,
-- body_battery_min (já coletados pelo Garmin, nunca expostos na análise) e
-- sleep_regularity_min (derivado de sleep_start, ver farol/sleep_regularity.py).
CREATE TABLE IF NOT EXISTS farol_daily_state (
    date TEXT PRIMARY KEY,

    -- DOMÍNIO CARGA (6 campos)
    trimp_7d REAL,                   -- TRIMP últimos 7 dias (derivado de activities)
    trimp_28d REAL,                  -- TRIMP últimos 28 dias
    ctl REAL,                        -- Chronic Training Load (Banister, τ=42d)
    atl REAL,                        -- Acute Training Load (Banister, τ=7d)
    tsb REAL,                        -- Training Stress Balance = CTL − ATL
    atl_ctl_ratio REAL,              -- Razão aguda/crônica (ATL / CTL)

    -- DOMÍNIO RECUPERAÇÃO (9 campos)
    hrv_avg REAL,                    -- HRV média, ms [~40% cobertura]
    rhr INTEGER,                     -- Resting heart rate, bpm [descontinuidade: 2026-06-11]
    night_hr_avg INTEGER,            -- FC noturna, bpm [~60% cobertura]
    stress_avg INTEGER,              -- Stress médio, 0-100
    body_battery_max INTEGER,        -- Body Battery máxima noturna, 0-100
    body_battery_min INTEGER,        -- Body Battery mínima do dia, 0-100 [~40% cobertura]
    respiration_avg REAL,            -- Frequência respiratória média em repouso, rpm [~40% cobertura]
    spo2_avg REAL,                   -- Saturação de oxigênio média, % [~40% cobertura]
    spo2_min REAL,                   -- Saturação de oxigênio mínima, % [~40% cobertura]

    -- DOMÍNIO SONO (6 campos)
    sleep_total_min REAL,            -- Duração total, minutos [~60% cobertura]
    awake_minutes REAL,              -- Tempo acordado dentro janela de sono
    deep_sleep_min REAL,             -- Sono profundo, minutos
    rem_sleep_min REAL,              -- Sono REM, minutos
    sleep_score INTEGER,             -- Score qualitativo, 0-100 [descontinuidade: 2026-08-01,
                                    -- política split_by_date executada em farol/metrics.py
                                    -- (DATE_SPLITS), igual ao awakenings: a baseline não
                                    -- cruza 2026-08-01]
    sleep_regularity_min REAL,       -- SD circular do horario de dormir, janela de 14 dias, minutos

    -- DOMÍNIO PERFORMANCE (2 campos)
    output_preserved TEXT,           -- 'yes' / 'no' / 'indeterminate' (NULL se sem atividade)
    deterioration_detected TEXT,     -- 'yes' / 'no' / 'indeterminate' (NULL se sem intervalado)

    -- DOMÍNIO OBSERVAÇÃO (1 campo)
    measurement_breakpoints TEXT     -- JSON: descontinuidades de medição detectadas
);

CREATE INDEX IF NOT EXISTS idx_farol_daily_state_date ON farol_daily_state(date);
