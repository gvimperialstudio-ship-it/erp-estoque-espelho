"""Regras puras de estoque espelho (sem I/O, 100% testáveis)."""
from __future__ import annotations

from datetime import datetime, timedelta


def qtd_por_componentes(componentes, disp: dict[str, int]) -> int | None:
    """Unidades vendáveis de um anúncio (kit = mínimo entre componentes)."""
    vals = []
    for cod, mult in componentes:
        if cod not in disp:
            return None
        vals.append(max(0, disp[cod]) // max(1, mult))
    return min(vals) if vals else None


def aplicar_limites(q: int, buffer: int, maximo: int) -> int:
    return max(0, min(q - buffer, maximo))


def donos_baixo_estoque(listings, disp: dict[str, int], limite: int,
                        prioridades: dict[str, int]) -> dict[str, str]:
    """codigo -> único canal que pode vender quando disp <= limite (modo prioridade)."""
    canais_por_cod: dict[str, set] = {}
    for l in listings:
        for cod, _ in l["componentes"]:
            canais_por_cod.setdefault(cod, set()).add(l["canal"])
    return {cod: min(cs, key=lambda c: (prioridades.get(c, 99), c))
            for cod, cs in canais_por_cod.items() if cod in disp and disp[cod] <= limite}


def reservas_por_codigo(reservas: dict) -> dict[str, int]:
    tot: dict[str, int] = {}
    for r in reservas.values():
        for cod, q in r["itens"].items():
            if q > 0:
                tot[cod] = tot.get(cod, 0) + q
    return tot


def reconciliar(reservas: dict, saldos: dict[str, int], ttl_horas: int, agora: datetime) -> list[str]:
    """Baixa reservas já refletidas no Bling (queda de saldo, FIFO) ou expiradas (TTL).

    Retorna as chaves removidas. Reposição de estoque mascara a queda -> a reserva
    fica até o TTL (erra para o lado seguro: mostra menos, nunca mais).
    """
    removidas = []
    limite = agora - timedelta(hours=ttl_horas)
    for k in list(reservas):
        if datetime.fromisoformat(reservas[k]["criada"]) < limite:
            removidas.append(k)
            del reservas[k]

    por_cod: dict[str, list[str]] = {}
    for k, r in sorted(reservas.items(), key=lambda kv: kv[1]["criada"]):
        for cod, q in r["itens"].items():
            if q > 0:
                por_cod.setdefault(cod, []).append(k)

    for cod, keys in por_cod.items():
        ref = reservas[keys[0]]["saldo_ref"].get(cod)
        if cod not in saldos or ref is None:
            continue
        queda = ref - saldos[cod]
        baixado = 0
        for k in keys:
            q = reservas[k]["itens"][cod]
            if q > queda:
                break
            queda -= q
            baixado += q
            reservas[k]["itens"][cod] = 0
        if baixado:
            for k in keys:
                if reservas[k]["itens"][cod] > 0:
                    reservas[k]["saldo_ref"][cod] = ref - baixado

    for k in list(reservas):
        if all(q <= 0 for q in reservas[k]["itens"].values()):
            removidas.append(k)
            del reservas[k]
    return removidas


def trava_zeragem(vinculados: int, zerando: int, pct: int, minimo: int) -> bool:
    """True = bloquear execução (provável falha no Bling zerando tudo)."""
    return vinculados > 0 and zerando >= minimo and zerando * 100 / vinculados > pct
