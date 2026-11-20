"""Cliente Bling API v3 (OAuth2 com refresh token rotativo)."""
from __future__ import annotations

import base64
import logging
import os
import time

import requests

from .http import RateLimiter, chunks, importar_seed, request

log = logging.getLogger("erp")

BASE = "https://api.bling.com.br/Api/v3"
TOKEN_URL = f"{BASE}/oauth/token"
AUTH_URL = "https://www.bling.com.br/Api/v3/oauth/authorize"


class BlingAuthError(RuntimeError):
    pass


def _basic(cid: str, secret: str) -> str:
    return base64.b64encode(f"{cid}:{secret}".encode()).decode()


def trocar_token(cid: str, secret: str, data: dict) -> dict:
    r = requests.post(TOKEN_URL, data=data, timeout=30, headers={
        "Authorization": f"Basic {_basic(cid, secret)}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "1.0",
    })
    if r.status_code != 200:
        raise BlingAuthError(f"Bling OAuth {r.status_code}: {r.text[:300]}")
    return r.json()


class Bling:
    def __init__(self, client_id: str, client_secret: str, state):
        self.cid, self.secret, self.state = client_id, client_secret, state
        self.s = requests.Session()
        self.lim = RateLimiter(2.5)  # limite oficial: 3 req/s

    @property
    def tok(self) -> dict:
        return self.state.section("tokens").setdefault("bling", {})

    def _token(self, force: bool = False) -> str:
        t = self.tok
        importar_seed(t, os.getenv("BLING_REFRESH_TOKEN"))
        if not t.get("refresh_token"):
            raise BlingAuthError("Sem refresh token do Bling. Rode `python -m erp auth-bling`.")
        if not force and t.get("access_token") and t.get("expires_at", 0) > time.time():
            return t["access_token"]
        d = trocar_token(self.cid, self.secret, {"grant_type": "refresh_token",
                                                  "refresh_token": t["refresh_token"]})
        t.update(access_token=d["access_token"], refresh_token=d["refresh_token"],
                 expires_at=time.time() + int(d.get("expires_in", 21600)) - 300)
        self.state.save()  # grava JÁ: o refresh antigo morreu
        log.info("Bling: token renovado")
        return t["access_token"]

    def _call(self, method: str, path: str, **kw) -> dict:
        for tent in range(2):
            h = {"Authorization": f"Bearer {self._token(force=tent == 1)}", "Accept": "application/json"}
            r = request(self.s, method, BASE + path, self.lim, headers=h, **kw)
            if r.status_code == 401 and tent == 0:
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Bling {method} {path} {r.status_code}: {r.text[:300]}")
            return r.json() if r.content else {}
        raise BlingAuthError("Bling recusou o token após renovação")

    def produtos(self):
        """Todos os produtos ativos (id, codigo, nome...)."""
        pagina = 1
        while True:
            data = self._call("GET", "/produtos", params={"pagina": pagina, "limite": 100,
                                                           "criterio": 2}).get("data") or []
            if not data:
                return
            yield from data
            pagina += 1

    def saldos(self, ids: list[int], deposito_id: int | None, campo: str) -> dict[int, int]:
        out: dict[int, int] = {}
        chave_total = "saldoVirtualTotal" if campo == "virtual" else "saldoFisicoTotal"
        chave_dep = "saldoVirtual" if campo == "virtual" else "saldoFisico"
        for bloco in chunks(ids, 50):
            params = [("idsProdutos[]", i) for i in bloco]
            for row in self._call("GET", "/estoques/saldos", params=params).get("data") or []:
                pid = row["produto"]["id"]
                if deposito_id:
                    dep = next((d for d in row.get("depositos") or [] if d.get("id") == deposito_id), {})
                    val = dep.get(chave_dep, 0)
                else:
                    val = row.get(chave_total, 0)
                out[pid] = int(float(val or 0))
        return out

    def produto_detalhe(self, produto_id: int) -> dict:
        return self._call("GET", f"/produtos/{produto_id}").get("data") or {}

    def depositos(self) -> list[dict]:
        return self._call("GET", "/depositos").get("data") or []

    def lancar(self, produto_id: int, deposito_id: int, operacao: str, qtd: int, obs: str) -> None:
        """operacao: S=saída, E=entrada, B=balanço."""
        self._call("POST", "/estoques", json={
            "produto": {"id": produto_id}, "deposito": {"id": deposito_id},
            "operacao": operacao, "quantidade": qtd, "observacoes": obs[:100],
        })
