"""Vínculo anúncio → SKU(s) do Bling da Rennar.

Ordem de resolução:
1. config/sku_map.csv por (canal, item_id, variation_id)  -> kits/exceções
2. config/sku_map.csv por (canal, sku_anuncio)            -> SKU diferente entre contas
3. SKU do próprio anúncio (seller_custom_field / SELLER_SKU), sem prefixos configurados
Componentes: "COD1:2|COD2:1" = kit com 2x COD1 + 1x COD2.
"""
from __future__ import annotations

import csv
import os

Componentes = list[tuple[str, int]]


def sku_of(entity: dict) -> str | None:
    v = (entity.get("seller_custom_field") or "").strip()
    if v:
        return v
    for a in entity.get("attributes") or []:
        if a.get("id") == "SELLER_SKU" and (a.get("value_name") or "").strip():
            return a["value_name"].strip()
    return None


def normaliza(sku: str, prefixos: list[str]) -> str:
    s = sku.strip().upper()
    for p in prefixos:
        if p and s.startswith(p.upper()):
            s = s[len(p):]
            break
    return s.strip()


def parse_componentes(txt: str) -> Componentes:
    out: Componentes = []
    for part in (txt or "").split("|"):
        part = part.strip()
        if not part:
            continue
        cod, _, mult = part.partition(":")
        out.append((cod.strip().upper(), int(mult) if mult.strip() else 1))
    return out


class SkuMap:
    def __init__(self, path: str | None, prefixos: list[str], prefixo_obrigatorio: str = ""):
        self.prefixos = prefixos
        self.prefixo_obrigatorio = (prefixo_obrigatorio or "").strip().upper()
        self.by_listing: dict[tuple[str, str, str], Componentes] = {}
        self.by_sku: dict[tuple[str, str], Componentes] = {}
        if path and os.path.exists(path):
            with open(path, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    comp = parse_componentes(row.get("componentes", ""))
                    if not comp:
                        continue
                    canal = (row.get("canal") or "").strip() or "*"
                    item = (row.get("item_id") or "").strip().upper()
                    var = (row.get("variation_id") or "").strip()
                    sku = (row.get("sku_anuncio") or "").strip().upper()
                    if item:
                        self.by_listing[(canal, item, var)] = comp
                    elif sku:
                        self.by_sku[(canal, sku)] = comp

    def classificar(self, canal: str, item_id: str, variation_id, sku: str | None) -> tuple[str, Componentes | None]:
        var = str(variation_id or "")
        item = (item_id or "").upper()
        for c in (canal, "*"):
            if (c, item, var) in self.by_listing:
                return ("mapeado", self.by_listing[(c, item, var)])
        if sku:
            sku_up = sku.strip().upper()
            for c in (canal, "*"):
                if (c, sku_up) in self.by_sku:
                    return ("mapeado", self.by_sku[(c, sku_up)])
        if self.prefixo_obrigatorio:
            if not sku:
                return ("proprio", None)
            sku_up = sku.strip().upper()
            if not sku_up.startswith(self.prefixo_obrigatorio):
                return ("proprio", None)
            resto = sku_up[len(self.prefixo_obrigatorio):]
            cod = normaliza(resto, self.prefixos)
            if not cod:
                return ("sem_sku_rennar", None)
            return ("rennar", [(cod, 1)])
        if not sku:
            return ("sem_vinculo", None)
        return ("rennar", [(normaliza(sku, self.prefixos), 1)])

    def componentes(self, canal: str, item_id: str, variation_id, sku: str | None) -> Componentes | None:
        return self.classificar(canal, item_id, variation_id, sku)[1]
