"""Estado persistente criptografado (tokens rotativos, catálogo, reservas, checkpoints).

Fica em .state/state.enc e é salvo na branch `state` do repositório pelo workflow.
Criptografia Fernet (AES-128-CBC + HMAC) com a chave do secret STATE_KEY.
"""
from __future__ import annotations

import json
import os

from cryptography.fernet import Fernet, InvalidToken


class StateError(RuntimeError):
    pass


class State:
    FILE = "state.enc"

    def __init__(self, directory: str, key: str):
        self.dir = directory
        self.path = os.path.join(directory, self.FILE)
        self.fernet = Fernet(key.encode() if isinstance(key, str) else key)
        self.data: dict = {}

    def load(self) -> "State":
        if not os.path.exists(self.path) or os.path.getsize(self.path) == 0:
            self.data = {}
            return self
        with open(self.path, "rb") as f:
            raw = f.read()
        try:
            self.data = json.loads(self.fernet.decrypt(raw))
        except InvalidToken as e:
            # Nunca sobrescrever: perderíamos os refresh tokens rotacionados.
            raise StateError("STATE_KEY não decifra state.enc. Abortando sem gravar nada.") from e
        return self

    def save(self) -> None:
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.path + ".tmp"
        payload = json.dumps(self.data, ensure_ascii=False, separators=(",", ":")).encode()
        with open(tmp, "wb") as f:
            f.write(self.fernet.encrypt(payload))
        os.replace(tmp, self.path)

    def section(self, name: str) -> dict:
        return self.data.setdefault(name, {})

    @staticmethod
    def gerar_chave() -> str:
        return Fernet.generate_key().decode()
