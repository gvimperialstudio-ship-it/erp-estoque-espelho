"""HTTP com rate limit, retry exponencial e importação de refresh token semente."""
from __future__ import annotations

import hashlib
import logging
import time

import requests

log = logging.getLogger("erp")


class RateLimiter:
    def __init__(self, per_second: float):
        self.interval = 1.0 / per_second
        self._last = 0.0

    def wait(self) -> None:
        delta = self._last + self.interval - time.monotonic()
        if delta > 0:
            time.sleep(delta)
        self._last = time.monotonic()


def request(session: requests.Session, method: str, url: str, limiter: RateLimiter | None = None,
            retries: int = 4, **kw) -> requests.Response:
    kw.setdefault("timeout", 30)
    for attempt in range(retries + 1):
        if limiter:
            limiter.wait()
        try:
            r = session.request(method, url, **kw)
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            if attempt == retries:
                return r
            espera = min(30, 2 ** attempt + 1)
            log.warning("%s %s -> %s, nova tentativa em %ss", method, url, r.status_code, espera)
            time.sleep(espera)
            continue
        return r
    raise RuntimeError("inalcançável")


def importar_seed(tok: dict, seed: str | None) -> None:
    """Importa o refresh token do secret só quando ele muda (re-autorização).

    Depois da 1ª renovação o token rotacionado vive no estado; o secret continua
    com o valor antigo, mas o hash igual impede reimportar um token já consumido.
    """
    if not seed:
        return
    h = hashlib.sha256(seed.strip().encode()).hexdigest()[:16]
    if tok.get("seed_hash") != h:
        tok.clear()
        tok.update(refresh_token=seed.strip(), seed_hash=h)


def chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
