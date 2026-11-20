"""Carrega config/config.yaml (não secreto) + variáveis de ambiente (secretos)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml


def env(name: str, required: bool = True) -> str | None:
    v = os.getenv(name)
    if required and not v:
        raise RuntimeError(f"Variável de ambiente obrigatória ausente: {name}")
    return v


def _bool(v, default: bool) -> bool:
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() in ("1", "true", "sim", "yes", "on")


@dataclass
class Canal:
    nome: str
    tipo: str = "meli"
    seller_id: int | None = None
    prioridade: int = 1
    ativo: bool = True
    buffer: int | None = None
    max_publicado: int | None = None

    @property
    def env_key(self) -> str:
        return self.nome.upper().replace("-", "_").replace(" ", "_")


@dataclass
class Settings:
    dry_run: bool = True
    buffer_global: int = 0
    max_publicado: int = 999
    modo_baixo_estoque: str = "espelho"  # espelho | prioridade
    limite_baixo_estoque: int = 2
    reserva_horas: int = 24
    historico_pedidos_dias: int = 60
    devolucao_auto_estorno: bool = False
    trava_zeragem_pct: int = 40
    trava_zeragem_min: int = 5
    bling_saldo: str = "virtual"  # virtual | fisico
    bling_deposito_id: int | None = None
    bling_write_mode: str = "none"  # none | estoque
    catalogo_ttl_horas: int = 12
    sku_prefixo_obrigatorio: str = "REN-"
    sku_prefixos_remover: list[str] = field(default_factory=list)
    sku_map_csv: str = "config/sku_map.csv"
    alerta_email_cancelamento: bool = True
    alerta_email_erro: bool = True
    canais: list[Canal] = field(default_factory=list)


def load(path: str = "config/config.yaml") -> Settings:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    canais = [Canal(**c) for c in raw.pop("canais", []) or []]
    s = Settings(**raw, canais=canais)
    s.dry_run = _bool(os.getenv("DRY_RUN"), s.dry_run)
    if s.modo_baixo_estoque not in ("espelho", "prioridade"):
        raise ValueError("modo_baixo_estoque deve ser 'espelho' ou 'prioridade'")
    if s.bling_write_mode not in ("none", "estoque"):
        raise ValueError("bling_write_mode deve ser 'none' ou 'estoque'")
    if s.bling_write_mode == "estoque" and not s.bling_deposito_id:
        raise ValueError("bling_write_mode=estoque exige bling_deposito_id")
    if s.bling_saldo not in ("virtual", "fisico"):
        raise ValueError("bling_saldo deve ser 'virtual' ou 'fisico'")
    if not s.canais:
        raise ValueError("Nenhum canal configurado")
    return s
