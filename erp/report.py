"""Relatório Markdown por execução (job summary do Actions + .state/relatorio.md)."""
from __future__ import annotations

import os
from datetime import datetime, timezone


class Relatorio:
    def __init__(self, dry_run: bool):
        self.dry_run = dry_run
        self.inicio = datetime.now(timezone.utc)
        self.metricas: dict[str, int | str] = {}
        self.mudancas: list[str] = []
        self.cancelamentos: list[dict] = []
        self.sem_vinculo: list[str] = []
        self.infos: list[str] = []
        self.erros: list[str] = []

    def info(self, msg: str) -> None:
        self.infos.append(msg)

    def erro(self, msg: str) -> None:
        self.erros.append(msg)

    def add_cancelamento(self, canal: str, pedido, tipo: str, itens: dict, acao: str) -> None:
        self.cancelamentos.append({
            "canal": canal,
            "pedido": pedido,
            "tipo": tipo,
            "itens": dict(itens or {}),
            "acao": acao,
        })

    @property
    def ok(self) -> bool:
        return not self.erros

    def markdown(self) -> str:
        modo = "SIMULAÇÃO (dry-run)" if self.dry_run else "PRODUÇÃO"
        L = [f"# Sync estoque espelho: {modo}",
             f"Início: {self.inicio:%Y-%m-%d %H:%M} UTC  |  Status: {'OK' if self.ok else 'COM ERROS'}", "",
             "| Métrica | Valor |", "|---|---|"]
        L += [f"| {k} | {v} |" for k, v in self.metricas.items()]

        def bloco(titulo, linhas, limite=80):
            if linhas:
                L.extend(["", f"## {titulo} ({len(linhas)})"])
                L.extend(f"- {x}" for x in linhas[:limite])
                if len(linhas) > limite:
                    L.append(f"- … +{len(linhas) - limite}")

        cancel_linhas = [
            f"{c['canal']} Pedido {c['pedido']} | {c['tipo']} | "
            f"{', '.join(f'{k} x{v}' for k, v in sorted(c['itens'].items())) or '(sem itens)'} -> {c['acao']}"
            for c in self.cancelamentos
        ]
        bloco("Erros", self.erros)
        bloco("Cancelamentos e Devoluções", cancel_linhas)
        bloco("Mudanças de estoque", self.mudancas)
        bloco("Anúncios sem vínculo com o Bling (não alterados)", self.sem_vinculo)
        bloco("Informações", self.infos)
        return "\n".join(L) + "\n"

    def escrever(self, state_dir: str) -> None:
        md = self.markdown()
        os.makedirs(state_dir, exist_ok=True)
        with open(os.path.join(state_dir, "relatorio.md"), "w", encoding="utf-8") as f:
            f.write(md)
        summary = os.getenv("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(md)
        print(md)
