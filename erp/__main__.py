"""CLI: python -m erp <comando>

  gen-key                        gera STATE_KEY (Fernet)
  auth-bling                     autoriza o Bling da Rennar e mostra o refresh token
  auth-meli --canal VERO         autoriza uma conta ML e mostra refresh token + seller_id
  mapear-contas-locais           inspeciona metadados (sem segredos) das contas ML locais
  bling-depositos                lista depósitos do Bling (para bling_deposito_id)
  exportar-catalogo --saida f.csv exporta catálogo completo do Bling para criar anúncios
  sync                           executa 1 rodada (respeita DRY_RUN)
  loop --intervalo 300           roda continuamente (PC/VM própria)
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import secrets
import sys
import time
from urllib.parse import parse_qs, urlencode, urlparse

from . import bling as bl
from . import meli as ml
from . import settings as st
from .state import State

DEFAULT_REDIRECT = "https://github.com/gvimperialstudio-ship-it/erp-estoque-espelho"


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ and v:
                os.environ[k] = v


def _code(valor: str | None) -> str:
    v = (valor or input("Cole aqui a URL de retorno completa (ou só o code): ")).strip()
    if v.startswith("http"):
        v = parse_qs(urlparse(v).query).get("code", [""])[0]
    if not v:
        sys.exit("code não encontrado")
    return v


def cmd_auth_bling(a):
    cid, sec = st.env("BLING_CLIENT_ID"), st.env("BLING_CLIENT_SECRET")
    redir = os.getenv("BLING_REDIRECT_URI") or DEFAULT_REDIRECT
    params = {"response_type": "code", "client_id": cid, "state": secrets.token_hex(8),
              "redirect_uri": redir}
    url = f"{bl.AUTH_URL}?{urlencode(params)}"
    print("\n1) A Rennar (logada no Bling) abre e autoriza:\n  ", url)
    print("2) ATENÇÃO: o code do Bling expira em ~30 segundos! Deixe este terminal pronto e cole na hora.\n")
    d = bl.trocar_token(cid, sec, {"grant_type": "authorization_code", "code": _code(a.code)})
    print("\nOK! Salve no GitHub > Settings > Secrets and variables > Actions:")
    print(f"  BLING_REFRESH_TOKEN = {d['refresh_token']}")
    print("(Não rode `sync` local com este token antes do 1º run no Actions: o Bling invalida o token antigo a cada uso.)")


def cmd_auth_meli(a):
    key = a.canal.upper().replace("-", "_").replace(" ", "_")
    cid = (os.getenv(f"MELI_CLIENT_ID_{key}") or "").strip() or st.env("MELI_CLIENT_ID")
    sec = (os.getenv(f"MELI_CLIENT_SECRET_{key}") or "").strip() or st.env("MELI_CLIENT_SECRET")
    redir = os.getenv("MELI_REDIRECT_URI") or DEFAULT_REDIRECT
    url = f"{ml.AUTH_URL}?{urlencode({'response_type': 'code', 'client_id': cid, 'redirect_uri': redir, 'state': secrets.token_hex(8)})}"
    print(f"\n1) O dono da conta {a.canal} (logado no ML) abre e autoriza:\n  ", url, "\n")
    d = ml.trocar_token({"grant_type": "authorization_code", "client_id": cid, "client_secret": sec,
                         "code": _code(a.code), "redirect_uri": redir})
    print("\nOK! Salve no GitHub Secrets:")
    print(f"  MELI_REFRESH_TOKEN_{key} = {d['refresh_token']}")
    print(f"E em config/config.yaml, canal {a.canal}: seller_id: {d.get('user_id')}")


def cmd_mapear_contas_locais(a):
    fontes = [
        ("CAPITAL", r"c:\Users\gabri\OneDrive\Documentos\ML\config.json", None),
        ("VERO", r"c:\Users\gabri\OneDrive\Desktop\02_Ecommerce_ML_Shopee\LOJA_JEAN\config_vero.json", None),
        ("ELUXIA", r"c:\Users\gabri\OneDrive\Desktop\02_Ecommerce_ML_Shopee\LOJA_JEAN\config.json", None),
        ("CAPITAL", r"C:\Users\gabri\teamwork_projects\ml_dashboard\data\tokens.json", "capitalparts"),
        ("VERO", r"C:\Users\gabri\teamwork_projects\ml_dashboard\data\tokens.json", "vero"),
        ("ELUXIA", r"C:\Users\gabri\teamwork_projects\ml_dashboard\data\tokens.json", "eluxia"),
    ]
    print("canal\texists\tseller_id\tclient_id\thas_refresh_token\tpath")
    for canal, path, store_key in fontes:
        exists = os.path.exists(path)
        seller_id = client_id = "-"
        has_rt = False
        if exists:
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                node = (d.get("stores") or {}).get(store_key, {}) if store_key else d
                seller_id = str(node.get("user_id") or node.get("seller_id") or "-")
                client_id = str(node.get("client_id") or "-")
                has_rt = bool(node.get("refresh_token"))
            except Exception as e:
                seller_id = f"erro:{type(e).__name__}"
        print(f"{canal}\t{exists}\t{seller_id}\t{client_id}\t{has_rt}\t{path}")
    print("\nAVISO: Reutilizar tokens de apps locais ativos causa colisão de refresh_token rotativo no ML.")
    print("Recomendação: crie 1 App ML dedicado no GitHub Actions (ou pare os refreshers locais se migrar).")


def cmd_depositos(a):
    state = State(a.state_dir, st.env("STATE_KEY")).load()
    for d in bl.Bling(st.env("BLING_CLIENT_ID"), st.env("BLING_CLIENT_SECRET"), state).depositos():
        print(f"{d.get('id')}\t{d.get('descricao')}\tpadrao={d.get('padrao')}\tsituacao={d.get('situacao')}")
    state.save()


def cmd_exportar_catalogo(a):
    s = st.load(a.config)
    state = State(a.state_dir, st.env("STATE_KEY")).load()
    bling = bl.Bling(st.env("BLING_CLIENT_ID"), st.env("BLING_CLIENT_SECRET"), state)
    prods = list(bling.produtos())
    if a.limite:
        prods = prods[:a.limite]
    saldos = bling.saldos([p["id"] for p in prods], s.bling_deposito_id, s.bling_saldo)
    campos = ["id", "sku", "sku_ml_sugerido", "nome", "marca", "preco", "saldo", "gtin", "ncm", "cest",
              "peso_bruto", "largura", "altura", "profundidade", "descricao_curta",
              "descricao_complementar", "imagens"]
    with open(a.saida, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        for p in prods:
            det = bling.produto_detalhe(p["id"]) if a.detalhes else p
            trib = det.get("tributacao") or {}
            dim = det.get("dimensoes") or {}
            mid = (det.get("midia") or {}).get("imagens") or {}
            imgs = [x.get("link") for x in (mid.get("internas") or []) + (mid.get("externas") or [])
                    if isinstance(x, dict) and x.get("link")]
            if not imgs and det.get("imagemURL"):
                imgs = [det["imagemURL"]]
            sku = (det.get("codigo") or p.get("codigo") or "").strip()
            w.writerow({
                "id": det.get("id", p["id"]),
                "sku": sku,
                "sku_ml_sugerido": f"{s.sku_prefixo_obrigatorio}{sku}" if sku else "",
                "nome": det.get("nome") or p.get("nome") or "",
                "marca": det.get("marca") or "",
                "preco": det.get("preco") or p.get("preco") or 0,
                "saldo": saldos.get(p["id"], 0),
                "gtin": det.get("gtin") or "",
                "ncm": trib.get("ncm") or "",
                "cest": trib.get("cest") or "",
                "peso_bruto": det.get("pesoBruto") or "",
                "largura": dim.get("largura") or "",
                "altura": dim.get("altura") or "",
                "profundidade": dim.get("profundidade") or "",
                "descricao_curta": (det.get("descricaoCurta") or "").replace("\r", " ").replace("\n", " "),
                "descricao_complementar": (det.get("descricaoComplementar") or "").replace("\r", " ").replace("\n", " "),
                "imagens": " | ".join(imgs),
            })
    state.save()
    print(f"Exportados {len(prods)} produtos para {a.saida}")


def cmd_sync(a):
    from .engine import sync
    rel = sync(a.config, a.state_dir)
    sys.exit(0 if rel.ok else 1)


def cmd_loop(a):
    from .engine import sync
    while True:
        try:
            sync(a.config, a.state_dir)
        except Exception:
            logging.exception("rodada falhou")
        time.sleep(a.intervalo)


def main():
    _load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="erp")
    p.add_argument("--config", default="config/config.yaml")
    p.add_argument("--state-dir", default=".state")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gen-key").set_defaults(fn=lambda a: print(State.gerar_chave()))
    sp = sub.add_parser("auth-bling"); sp.add_argument("--code"); sp.set_defaults(fn=cmd_auth_bling)
    sp = sub.add_parser("auth-meli"); sp.add_argument("--canal", required=True); sp.add_argument("--code")
    sp.set_defaults(fn=cmd_auth_meli)
    sub.add_parser("mapear-contas-locais").set_defaults(fn=cmd_mapear_contas_locais)
    sub.add_parser("bling-depositos").set_defaults(fn=cmd_depositos)
    sp = sub.add_parser("exportar-catalogo")
    sp.add_argument("--saida", default="catalogo_rennar.csv")
    sp.add_argument("--detalhes", action="store_true", help="Busca ficha completa (/produtos/{id})")
    sp.add_argument("--limite", type=int, default=0)
    sp.set_defaults(fn=cmd_exportar_catalogo)
    sub.add_parser("sync").set_defaults(fn=cmd_sync)
    sp = sub.add_parser("loop"); sp.add_argument("--intervalo", type=int, default=300); sp.set_defaults(fn=cmd_loop)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
