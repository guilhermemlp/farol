"""Rotulos legiveis dos campos que aparecem em mais de um relatorio, com o
MESMO significado nos dois lugares.

Existia antes como dois dicionarios copiados (today._ROTULO, state._ROTULOS)
-- ja tinham divergido de verdade em 2 campos (rhr = "FC repouso" num
relatorio, "RHR" no outro; spo2_avg = "SpO2" num, "SpO2 media" no outro),
achado na auditoria de 2026-09-04. `sleep_score` fica de fora de proposito:
em today.py o mesmo campo rotula a fatia "Sono" do indice de recuperacao
(junto de HRV/FC repouso/TSB, contexto de composicao do indice) e em
state.py rotula a linha "Sleep score" numa tabela que ja tem "Duracao do
sono"/"Sono profundo" ao lado (contexto onde precisa ser especifico) --
dois usos legitimamente diferentes da mesma chave, nao drift acidental.
Cada modulo mantem esse um rotulo por conta propria.

Sem acento de proposito (mesma convencao ja usada no restante dos rotulos
de terminal deste projeto)."""

# Status -> (rotulo, emoji). A mesma escala vale para metricas e dominios em
# todos os relatorios; a cor/classe CSS fica na camada que renderiza.
STATUS_LABEL = {
    "ok": ("NORMAL", "\U0001F7E2"),
    "atencao": ("BORDERLINE", "\U0001F7E1"),
    "alerta": ("ALERTA", "\U0001F534"),
    "sem_dado": ("SEM DADO", "⚪"),
}

ROTULOS = {
    "hrv_avg": "HRV",
    "rhr": "FC repouso",
    "night_hr_avg": "FC noturna",
    "stress_avg": "Stress",
    "body_battery_max": "Body Battery",
    "body_battery_min": "Body Battery min",
    "respiration_avg": "Respiracao",
    "spo2_avg": "SpO2 media",
    "spo2_min": "SpO2 minima",
    "tsb": "TSB",
}
