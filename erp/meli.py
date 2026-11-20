"""Cliente Mercado Livre (1 instância por conta revendedora)."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import requests

from .http import RateLimiter, chunks, importar_seed, request

log = logging.getLogger("erp")

API = "https://api.mercadolibre.com"
AUTH_URL = "https://auth.mercadolivre.com.br/authorization"


class MeliAuthError(RuntimeError):
    pass


def trocar_token(data: dict) -> dict:
    r = requests.post(f"{API}/oauth/token", data=data, timeout=30, headers={
        "accept": "application/json", "content-type": "application/x-www-form-urlencoded"})
    if r.status_code != 200:
        raise MeliAuthError(f"ML OAuth {r.status_code}: {r.text[:300]}")
    return r.json()


class Meli:
    def __init__(self, canal, client_id: str, client_secret: str, state):
        self.canal, self.cid, self.secret, self.state = canal, client_id, client_secret, state
        self.s = requests.Session()
        self.lim = RateLimiter(8)

    @property
    def tok(self) -> dict:
        return self.state.section("tokens").setdefault(f"meli:{self.canal.nome}", {})

    def _token(self, force: bool = False) -> str:
        t = self.tok
        importar_seed(t, os.getenv(f"MELI_REFRESH_TOKEN_{self.canal.env_key}"))
        if not t.get("refresh_token"):
            raise MeliAuthError(f"{self.canal.nome}: sem refresh token. Rode `python -m erp auth-meli`.")
        if not force and t.get("access_token") and t.get("expires_at", 0) > time.time():
            return t["access_token"]
        d = trocar_token({"grant_type": "refresh_token", "client_id": self.cid,
                          "client_secret": self.secret, "refresh_token": t["refresh_token"]})
        t.update(access_token=d["access_token"], refresh_token=d["refresh_token"],
                 expires_at=time.time() + int(d.get("expires_in", 21600)) - 300,
                 user_id=d.get("user_id", t.get("user_id")))
        self.state.save()
        log.info("ML %s: token renovado", self.canal.nome)
        return t["access_token"]

    def _call(self, method: str, path: str, **kw):
        for tent in range(2):
            h = {"Authorization": f"Bearer {self._token(force=tent == 1)}"}
            r = request(self.s, method, API + path, self.lim, headers=h, **kw)
            if r.status_code == 401 and tent == 0:
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"ML {self.canal.nome} {method} {path} {r.status_code}: {r.text[:300]}")
            return r.json() if r.content else {}
        raise MeliAuthError(f"{self.canal.nome}: token recusado após renovação")

    def seller_id(self) -> int:
        if self.canal.seller_id:
            return int(self.canal.seller_id)
        self._token()
        return int(self.tok.get("user_id") or self._call("GET", "/users/me")["id"])

    def _ids(self, seller: int, status: str) -> list[str]:
        params = {"search_type": "scan", "limit": 100, "status": status}
        ids: list[str] = []
        while True:
            d = self._call("GET", f"/users/{seller}/items/search", params=params)
            res = d.get("results") or []
            if not res:
                return ids
            ids += res
            if not d.get("scroll_id"):
                return ids
            params["scroll_id"] = d["scroll_id"]

    def itens_sincronizaveis(self, seller: int):
        """Ativos + pausados por falta de estoque. Ignora Full e pausados manualmente."""
        ids = self._ids(seller, "active") + self._ids(seller, "paused")
        for bloco in chunks(ids, 20):
            for row in self._call("GET", "/items", params={"ids": ",".join(bloco),
                                                           "include_attributes": "all"}):
                if row.get("code") != 200:
                    continue
                it = row["body"]
                if (it.get("shipping") or {}).get("logistic_type") == "fulfillment":
                    continue
                if it["status"] == "active" or (
                        it["status"] == "paused" and "out_of_stock" in (it.get("sub_status") or [])):
                    yield it

    def atualizar_item(self, item_id: str, payload: dict) -> None:
        self._call("PUT", f"/items/{item_id}", json=payload)

    def envio(self, shipping_id: int | str) -> dict:
        return self._call("GET", f"/shipments/{shipping_id}")

    def pedidos_desde(self, seller: int, desde: datetime):
        fmt = desde.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000-00:00")
        offset = 0
        while True:
            d = self._call("GET", "/orders/search", params={
                "seller": seller, "order.date_last_updated.from": fmt,
                "sort": "date_asc", "offset": offset, "limit": 50})
            res = d.get("results") or []
            yield from res
            offset += len(res)
            if not res or offset >= (d.get("paging") or {}).get("total", 0):
                return
