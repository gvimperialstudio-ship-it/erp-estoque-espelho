"""Alertas por e-mail (SMTP stdlib) e arquivo Markdown para GitHub Issues."""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger("erp")


def smtp_configurado() -> bool:
    return all(bool((os.getenv(k) or "").strip()) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "ALERT_EMAIL_TO"))


def enviar_email(assunto: str, corpo: str) -> bool:
    """Envia e-mail texto plano UTF-8 via SMTP. Nunca levanta exceção para não quebrar o sync."""
    if not smtp_configurado():
        log.info("SMTP não configurado; alerta por e-mail direto ignorado (usando GitHub Issue).")
        return False
    host = os.environ["SMTP_HOST"].strip()
    user = os.environ["SMTP_USER"].strip()
    password = os.environ["SMTP_PASS"]
    destinatarios = [d.strip() for d in os.environ["ALERT_EMAIL_TO"].split(",") if d.strip()]
    if not destinatarios:
        return False
    remetente = (os.getenv("ALERT_EMAIL_FROM") or user).strip()
    try:
        port = int((os.getenv("SMTP_PORT") or "587").strip())
    except ValueError:
        port = 587

    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = remetente
    msg["To"] = ", ".join(destinatarios)
    msg.set_content(corpo, subtype="plain", charset="utf-8")

    try:
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20, context=ctx) as srv:
                srv.login(user, password)
                srv.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as srv:
                srv.ehlo()
                srv.starttls(context=ctx)
                srv.ehlo()
                srv.login(user, password)
                srv.send_message(msg)
        log.info("Alerta por e-mail enviado para %s", ", ".join(destinatarios))
        return True
    except Exception as e:
        log.warning("Falha ao enviar e-mail SMTP (%s): %s", type(e).__name__, e)
        return False


def formatar_cancelamentos_md(cancelamentos: list[dict], dry_run: bool, run_url: str = "") -> str:
    """Formata resumo de cancelamentos/devoluções para corpo de Issue e e-mail."""
    modo = "SIMULAÇÃO (dry-run)" if dry_run else "PRODUÇÃO"
    linhas = [
        f"### Alerta de Cancelamento / Devolução ({modo})",
        "",
        f"Foram detectados **{len(cancelamentos)}** pedido(s) cancelado(s) ou devolvido(s) nesta rodada:",
        "",
    ]
    for c in cancelamentos:
        itens_txt = ", ".join(f"{cod} x{q}" for cod, q in sorted((c.get("itens") or {}).items())) or "(sem itens)"
        linhas.append(
            f"- **{c['canal']}** | Pedido `{c['pedido']}` | **{c['tipo']}** | Itens: `{itens_txt}` | Ação: {c['acao']}"
        )
    if run_url:
        linhas.extend(["", f"Execução: {run_url}"])
    return "\n".join(linhas) + "\n"


def notificar_rodada(rel, s, state_dir: str) -> None:
    """Grava/limpa .state/cancelamentos_alerta.md e dispara alertas SMTP quando configurados."""
    os.makedirs(state_dir, exist_ok=True)
    alerta_path = os.path.join(state_dir, "cancelamentos_alerta.md")
    server = (os.getenv("GITHUB_SERVER_URL") or "").strip()
    repo = (os.getenv("GITHUB_REPOSITORY") or "").strip()
    run_id = (os.getenv("GITHUB_RUN_ID") or "").strip()
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if (server and repo and run_id) else ""

    if rel.cancelamentos:
        corpo_cancel = formatar_cancelamentos_md(rel.cancelamentos, rel.dry_run, run_url)
        with open(alerta_path, "w", encoding="utf-8") as f:
            f.write(corpo_cancel)
        if s.alerta_email_cancelamento:
            prefixo = "[SIMULAÇÃO] " if rel.dry_run else ""
            assunto = f"{prefixo}[ERP Estoque] Alerta de Cancelamento/Devolução ({len(rel.cancelamentos)} pedido(s))"
            enviar_email(assunto, corpo_cancel)
    elif os.path.exists(alerta_path):
        os.remove(alerta_path)

    if rel.erros and s.alerta_email_erro:
        prefixo = "[SIMULAÇÃO] " if rel.dry_run else ""
        assunto = f"{prefixo}[ERP Estoque] ERRO na sincronização"
        corpo_err = rel.markdown() + (f"\nExecução: {run_url}\n" if run_url else "")
        enviar_email(assunto, corpo_err)
