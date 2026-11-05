"""Leitura mínima, somente local, das métricas de corrida em arquivos FIT.

O export do Strava inclui os FIT originais. Só são lidas mensagens ``record``
e campos de dinâmica; coordenadas e outros dados pessoais são ignorados.
"""

import gzip
import statistics


def _records(payload: bytes):
    if payload[:2] == b"\x1f\x8b":
        payload = gzip.decompress(payload)
    if len(payload) < 14 or payload[8:12] != b".FIT":
        return
    header_size = payload[0]
    end = min(len(payload), header_size + int.from_bytes(payload[4:8], "little"))
    pos = header_size
    definitions = {}
    while pos < end:
        header = payload[pos]
        pos += 1
        compressed = bool(header & 0x80)
        local = (header >> 5) & 0x03 if compressed else header & 0x0F
        if not compressed and header & 0x40:
            if pos + 5 > end:
                break
            architecture = payload[pos + 1]
            endian = "big" if architecture else "little"
            message = int.from_bytes(payload[pos + 2:pos + 4], endian)
            count = payload[pos + 4]
            pos += 5
            fields = []
            for _ in range(count):
                if pos + 3 > end:
                    return
                fields.append(tuple(payload[pos:pos + 3]))
                pos += 3
            developer_sizes = []
            if header & 0x20:
                if pos >= end:
                    return
                developer_count = payload[pos]
                pos += 1
                for _ in range(developer_count):
                    if pos + 3 > end:
                        return
                    developer_sizes.append(payload[pos + 1])
                    pos += 3
            definitions[local] = (message, endian, fields, developer_sizes)
            continue
        if local not in definitions:
            return
        message, endian, fields, developer_sizes = definitions[local]
        values = {}
        for number, size, base in fields:
            chunk = payload[pos:pos + size]
            pos += size
            if len(chunk) != size:
                return
            if message != 20 or number not in (4, 39, 41, 83, 85) or size not in (1, 2, 4):
                continue
            signed = base in (0x01, 0x83, 0x85)
            value = int.from_bytes(chunk, endian, signed=signed)
            if value in ((1 << (8 * size)) - 1, (1 << (8 * size - 1)) - 1):
                continue
            values[number] = value
        pos += sum(developer_sizes)
        if message == 20 and values.get(4, 0) > 0:
            yield values


def summarize_dynamics(payload: bytes) -> dict[str, float]:
    """Medianas por registro em movimento; campo ausente permanece ausente."""
    scales = {
        "avg_cadence": (4, 2),
        "vertical_oscillation": (39, 1 / 100),
        "ground_contact_time": (41, 1 / 10),
        "vertical_ratio": (83, 1 / 100),
        "stride_length": (85, 1 / 100),
    }
    series = {key: [] for key in scales}
    for record in _records(payload):
        for key, (field, scale) in scales.items():
            if record.get(field, 0) > 0:
                series[key].append(record[field] * scale)
    return {key: round(statistics.median(values), 3)
            for key, values in series.items() if values}
