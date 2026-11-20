"""Orquestração de uma rodada de sincronização.

1. Catálogo Bling (codigo -> id), cache de 12h
2. Anúncios das 3 contas ML -> componentes (SKU Rennar)
3. Saldos Bling dos SKUs vinculados
4. Reconcilia reservas antigas (já baixadas no Bling ou expiradas)
5. Novos pedidos pagos -> reserva (ou saída direta no Bling se bling_write_mode=estoque)
6. Disponível = saldo - reservas -> buffer/máximo/prioridade -> alvo por anúncio
7. Trava anti-zeragem em massa -> PUT só do que mudou
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import allocation as al
from . import notify
from . import settings as st
from .bling import Bling
from .mapping import SkuMap, sku_of
from .meli import Meli
from .report import Relatorio
from .state import State

log = logging.getLogger("erp")


class TravaSeguranca(RuntimeError):
    pass


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _catalogo(bling: Bling, state: State, ttl_h: int, rel: Relatorio) -> dict[str, int]:
    cat = state.section("bling_catalogo")
    if cat.get("map") and time.time() - cat.get("ts", 0) < ttl_h * 3600:
        return cat["map"]
    m = {}
    for p in bling.produtos():
        cod = (p.get("codigo") or "").strip().upper()
        if cod:
            m[cod] = p["id"]
    if not m:
        raise RuntimeError("Catálogo do Bling veio vazio: abortando sem tocar nos anúncios")
    cat.update(map=m, ts=time.time())
    state.save()
    rel.info(f"Catálogo Bling recarregado: {len(m)} SKUs")
    return m


def _coletar_anuncios(melis, skumap, rel):
    listings, itens_raw = [], {}
    for nome, (m, seller) in melis.items():
        n = 0
        for item in m.itens_sincronizaveis(seller):
            n += 1
            itens_raw[(nome, item["id"])] = item
            variacoes = item.get("variations") or []
            if variacoes:
                sku_item = sku_of(item) if len(variacoes) == 1 else None
                for v in variacoes:
                    sku = sku_of(v) or sku_item
                    origem, comp = skumap.classificar(nome, item["id"], v["id"], sku)
                    listings.append(dict(canal=nome, item_id=item["id"], variation_id=v["id"], sku=sku,
                                         origem=origem, componentes=comp,
                                         atual=int(v.get("available_quantity") or 0)))
            else:
                sku = sku_of(item)
                origem, comp = skumap.classificar(nome, item["id"], None, sku)
                listings.append(dict(canal=nome, item_id=item["id"], variation_id=None, sku=sku,
                                     origem=origem, componentes=comp,
                                     atual=int(item.get("available_quantity") or 0)))
        rel.metricas[f"Anúncios {nome}"] = n
    return listings, itens_raw


def _foi_enviado(m, order: dict) -> bool:
    tags = set(order.get("tags") or [])
    if "delivered" in tags:
        return True
    sid = (order.get("shipping") or {}).get("id")
    if not sid or not hasattr(m, "envio"):
        return False
    try:
        return (m.envio(sid) or {}).get("status") in ("shipped", "delivered", "not_delivered")
    except Exception:
        return False


def _processar_pedidos(s, melis, state, saldos, codigo_id, idx, skumap, bling, rel):
    reservas = state.section("reservas")
    vistos = state.section("pedidos_vistos")
    chk = state.section("checkpoints")
    novos = cancelados = 0
    for nome, (m, seller) in melis.items():
        inicio_run = _agora()
        if not chk.get(nome):
            chk[nome] = inicio_run.isoformat()
            rel.info(f"{nome}: 1ª execução, pedidos contados a partir de agora")
            continue
        desde = datetime.fromisoformat(chk[nome]) - timedelta(minutes=15)
        for o in m.pedidos_desde(seller, desde):
            key, status = f"{nome}:{o['id']}", o.get("status")
            visto = vistos.get(key)
            if status == "paid" and not visto:
                itens: dict[str, int] = defaultdict(int)
                for oi in o.get("order_items") or []:
                    it = oi.get("item") or {}
                    var = str(it.get("variation_id") or "")
                    comp = idx.get((nome, it.get("id"), var)) or skumap.componentes(
                        nome, it.get("id"), var, it.get("seller_sku"))
                    for cod, mult in comp or []:
                        itens[cod] += int(oi.get("quantity") or 0) * mult
                if not itens:
                    vistos[key] = {"status": "paid", "ts": inicio_run.isoformat(), "lancado": False}
                    continue
                lancado = False
                if s.bling_write_mode == "estoque" and not s.dry_run:
                    try:
                        for cod, q in itens.items():
                            if cod in codigo_id:
                                bling.lancar(codigo_id[cod], s.bling_deposito_id, "S", q,
                                             f"Venda {nome} ML {o['id']}")
                                if cod in saldos:
                                    saldos[cod] -= q
                        lancado = True
                    except Exception as e:  # mantém reserva se a saída falhar
                        rel.erro(f"Saída Bling pedido {key}: {e}")
                if not lancado:
                    reservas[key] = {"canal": nome, "pedido": o["id"], "criada": inicio_run.isoformat(),
                                     "itens": dict(itens),
                                     "saldo_ref": {c: saldos[c] for c in itens if c in saldos}}
                vistos[key] = {"status": "paid", "ts": inicio_run.isoformat(), "lancado": lancado,
                               "itens": dict(itens)}
                novos += 1
            elif status == "cancelled" and visto and visto.get("status") == "paid":
                reservas.pop(key, None)
                itens_cancel = visto.get("itens") or {}
                enviado = _foi_enviado(m, o)
                tipo_alerta = "DEVOLUÇÃO PÓS-ENVIO" if enviado else "CANCELAMENTO PRÉ-ENVIO"
                if enviado and not s.devolucao_auto_estorno:
                    rel.info(f"Devolução pós-envio {key}: estoque NÃO estornado automaticamente "
                             "(aguardar conferência e entrada física na Rennar)")
                    visto["status"] = "returned_pending"
                    acao = "Estoque NÃO estornado: aguardar conferência física na Rennar"
                else:
                    if visto.get("lancado") and not s.dry_run:
                        motivo = "Devolucao" if enviado else "Cancelamento"
                        erro_estorno = False
                        try:
                            for cod, q in itens_cancel.items():
                                if cod in codigo_id:
                                    bling.lancar(codigo_id[cod], s.bling_deposito_id, "E", q,
                                                 f"{motivo} {nome} ML {o['id']}")
                                    if cod in saldos:
                                        saldos[cod] += q
                        except Exception as e:
                            erro_estorno = True
                            rel.erro(f"Estorno Bling pedido {key}: {e}")
                        acao = ("Erro ao lançar entrada (E) no Bling (verificar manualmente)"
                                if erro_estorno else "Entrada (E) lançada no Bling + reserva liberada")
                    elif visto.get("lancado") and s.dry_run:
                        acao = "[simulado] Entrada (E) no Bling + reserva liberada"
                    else:
                        acao = "Reserva liberada (modo leitura Bling)"
                    visto["status"] = "cancelled"
                rel.add_cancelamento(canal=nome, pedido=o["id"], tipo=tipo_alerta,
                                     itens=itens_cancel, acao=acao)
                cancelados += 1
            elif status == "cancelled" and not visto:
                vistos[key] = {"status": "cancelled", "ts": inicio_run.isoformat()}
        chk[nome] = inicio_run.isoformat()
    corte = (_agora() - timedelta(days=s.historico_pedidos_dias)).isoformat()
    for k in [k for k, v in vistos.items() if v.get("ts", "") < corte]:
        del vistos[k]
    state.save()
    rel.metricas["Pedidos novos (pagos)"] = novos
    rel.metricas["Pedidos cancelados"] = cancelados
    rel.metricas["Reservas ativas"] = len(reservas)
    return reservas


def _meli_creds(canal) -> tuple[str, str]:
    cid = (os.getenv(f"MELI_CLIENT_ID_{canal.env_key}") or "").strip() or st.env("MELI_CLIENT_ID")
    sec = (os.getenv(f"MELI_CLIENT_SECRET_{canal.env_key}") or "").strip() or st.env("MELI_CLIENT_SECRET")
    return cid, sec


def sync(config_path: str, state_dir: str) -> Relatorio:
    s = st.load(config_path)
    rel = Relatorio(dry_run=s.dry_run)
    state = State(state_dir, st.env("STATE_KEY")).load()
    try:
        _sync(s, state, rel)
    except Exception as e:
        rel.erro(f"{type(e).__name__}: {e}")
        log.exception("Falha na sincronização")
    finally:
        state.save()
        rel.escrever(state_dir)
        try:
            notify.notificar_rodada(rel, s, state_dir)
        except Exception:
            log.exception("Falha ao processar notificações da rodada")
    return rel


def _sync(s: st.Settings, state: State, rel: Relatorio) -> None:
    bling = Bling(st.env("BLING_CLIENT_ID"), st.env("BLING_CLIENT_SECRET"), state)
    skumap = SkuMap(s.sku_map_csv, s.sku_prefixos_remover, s.sku_prefixo_obrigatorio)
    codigo_id = _catalogo(bling, state, s.catalogo_ttl_horas, rel)

    melis = {}
    for c in s.canais:
        if c.ativo:
            cid, secret = _meli_creds(c)
            m = Meli(c, cid, secret, state)
            melis[c.nome] = (m, m.seller_id())

    listings, itens_raw = _coletar_anuncios(melis, skumap, rel)
    vinculados, sem_vinculo = [], []
    proprios_ignorados = 0
    for l in listings:
        if l.get("origem") == "proprio":
            proprios_ignorados += 1
            continue
        if l["componentes"] and all(c in codigo_id for c, _ in l["componentes"]):
            vinculados.append(l)
        else:
            sem_vinculo.append(l)
    rel.metricas["Anúncios próprios ignorados (não-Rennar)"] = proprios_ignorados
    rel.sem_vinculo = [f"{l['canal']} {l['item_id']}{'/' + str(l['variation_id']) if l['variation_id'] else ''}"
                       f" SKU={l['sku'] or '(vazio)'}" for l in sem_vinculo]

    codigos = sorted({c for l in vinculados for c, _ in l["componentes"]})
    if codigos:
        saldo_id = bling.saldos([codigo_id[c] for c in codigos], s.bling_deposito_id, s.bling_saldo)
        saldos = {c: saldo_id[codigo_id[c]] for c in codigos if codigo_id[c] in saldo_id}
    else:
        saldos = {}
        rel.info("Nenhum anúncio Rennar vinculado ainda (anúncios próprios ignorados com segurança).")
    rel.metricas["SKUs Rennar vinculados"] = len(codigos)
    rel.metricas["SKUs sem saldo no Bling"] = len(codigos) - len(saldos)

    reservas = state.section("reservas")
    baixadas = al.reconciliar(reservas, saldos, s.reserva_horas, _agora())
    if baixadas:
        rel.info(f"Reservas baixadas: {len(baixadas)}")
    idx = {(l["canal"], l["item_id"], str(l["variation_id"] or "")): l["componentes"]
           for l in listings if l["componentes"]}
    reservas = _processar_pedidos(s, melis, state, saldos, codigo_id, idx, skumap, bling, rel)

    res_tot = al.reservas_por_codigo(reservas)
    disp = {c: max(0, saldos[c] - res_tot.get(c, 0)) for c in saldos}
    cfg_canal = {c.nome: c for c in s.canais}
    donos = (al.donos_baixo_estoque(vinculados, disp, s.limite_baixo_estoque,
                                    {c.nome: c.prioridade for c in s.canais})
             if s.modo_baixo_estoque == "prioridade" else {})

    mudancas = []
    for l in vinculados:
        q = al.qtd_por_componentes(l["componentes"], disp)
        if q is None:
            continue
        c = cfg_canal[l["canal"]]
        q = al.aplicar_limites(q, s.buffer_global if c.buffer is None else c.buffer,
                               c.max_publicado or s.max_publicado)
        if any(donos.get(cod) not in (None, l["canal"]) for cod, _ in l["componentes"]):
            q = 0
        if q != l["atual"]:
            l["alvo"] = q
            mudancas.append(l)

    zerando = sum(1 for l in mudancas if l["alvo"] == 0 and l["atual"] > 0)
    rel.metricas["Anúncios vinculados"] = len(vinculados)
    rel.metricas["Anúncios a alterar"] = len(mudancas)
    rel.metricas["Anúncios indo a zero"] = zerando
    if al.trava_zeragem(len(vinculados), zerando, s.trava_zeragem_pct, s.trava_zeragem_min):
        raise TravaSeguranca(f"{zerando}/{len(vinculados)} anúncios iriam a zero (> {s.trava_zeragem_pct}%). "
                             "Nada foi alterado. Verifique o Bling.")

    por_item = defaultdict(list)
    for l in mudancas:
        por_item[(l["canal"], l["item_id"])].append(l)
    ok = 0
    for (canal, item_id), ls in por_item.items():
        item = itens_raw[(canal, item_id)]
        if item.get("variations"):
            novos = {l["variation_id"]: l["alvo"] for l in ls}
            # ML apaga variações omitidas: enviar TODAS
            payload = {"variations": [{"id": v["id"], "available_quantity": novos.get(
                v["id"], int(v.get("available_quantity") or 0))} for v in item["variations"]]}
        else:
            payload = {"available_quantity": ls[0]["alvo"]}
        desc = ", ".join(f"{l['sku']}: {l['atual']}→{l['alvo']}" for l in ls)
        if s.dry_run:
            rel.mudancas.append(f"[simulado] {canal} {item_id} {desc}")
            continue
        try:
            melis[canal][0].atualizar_item(item_id, payload)
            rel.mudancas.append(f"{canal} {item_id} {desc}")
            ok += 1
        except Exception as e:
            rel.erro(f"{canal} {item_id}: {e}")
    rel.metricas["Itens atualizados no ML"] = ok
