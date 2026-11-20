# ERP Estoque Espelho: Rennar (Bling) → Capital · Vero · Eluxia

Sincroniza o estoque do Bling da Rennar (fornecedor dropshipping) com os anúncios do Mercado Livre de três revendedores. Custo zero, hospedado em `https://github.com/gvimperialstudio-ship-it/erp-estoque-espelho` via GitHub Actions.

```
Bling Rennar (fonte da verdade)
      │  saldo por SKU (API v3, OAuth)
      ▼
GitHub Actions (cron 10 min / webhook) ── estado criptografado (branch `state`)
      │  disponível = saldo − reservas − buffer
      ├──► ML CAPITAL (seller_id 3554514707)
      ├──► ML VERO    (seller_id 3728045217)
      └──► ML ELUXIA  (seller_id 3720968135)
```

## Como funciona

1. **Isolamento total dos anúncios próprios (`sku_prefixo_obrigatorio: "REN-"`):**
   - Capital, Vero e Eluxia já possuem anúncios próprios/Donegá que **não** pertencem à Rennar.
   - O ERP só sincroniza anúncios cujo SKU comece com `REN-` (ex.: `REN-12345` busca o SKU `12345` no Bling da Rennar) ou que estejam explicitamente listados em `config/sku_map.csv`.
   - Todos os demais anúncios próprios são 100% ignorados.
2. **Saldo:** lê o saldo de cada SKU no Bling da Rennar (`virtual` por padrão).
3. **Reserva:** uma venda paga em qualquer conta é descontada na hora das outras duas. A reserva sai quando o Bling da Rennar baixar o saldo (FIFO) ou depois de `reserva_horas`.
4. **Cancelamento × Devolução (60 dias) + Alerta por E-mail:**
   - Cancelamento antes do envio libera a reserva / estorna no Bling imediatamente;
   - Devolução após o envio (`shipped`/`delivered`) gera aviso e aguarda conferência física na Rennar (`devolucao_auto_estorno: false`);
   - Em **qualquer** cancelamento ou devolução detectada, o workflow abre uma Issue `cancelamento-alerta` (o GitHub envia e-mail automático) e, se `SMTP_*` estiver configurado, envia e-mail direto via SMTP.
5. **Estoque baixo:** com `modo_baixo_estoque: prioridade`, quando o saldo fica ≤ `limite_baixo_estoque`, só o canal de maior prioridade continua vendendo (`CAPITAL` → `VERO` → `ELUXIA`).
6. **Travas:**
   - anúncio próprio (sem `REN-`) nunca é alterado;
   - anúncio Rennar sem correspondência no Bling não é zerado;
   - anúncios Full e anúncios pausados manualmente são ignorados;
   - se mais de 40% dos anúncios vinculados fossem a zero numa rodada, a execução é abortada;
   - se `STATE_KEY` não decifrar `state.enc`, nada é gravado.

## Implantação passo a passo

### 0. Pré-requisitos locais
```powershell
cd erp_estoque_espelho
py -m pip install -q -r requirements.txt
py -m erp gen-key                 # guarde como STATE_KEY no GitHub Secrets
py -m erp mapear-contas-locais    # inspeciona seller_ids/apps locais (sem segredos)
```
Para comandos locais (`auth-bling`, `auth-meli`, `exportar-catalogo`), o CLI lê automaticamente o `.env` local (ignorado pelo `.gitignore`, **nunca** vai para o GitHub). Veja `.env.example`.

### 1. Repositório no GitHub (`Public`, cron a cada 10 min)
1. Repositório alvo: `https://github.com/gvimperialstudio-ship-it/erp-estoque-espelho` (**Public**, cron a cada **10 min** grátis; `state.enc` permanece criptografado).
2. Referência Git padrão (caso deseje clonar/atualizar localmente):
```powershell
git init -b main
git config user.name "gvimperialstudio-ship-it"
git config user.email "gvimperialstudio-ship-it@users.noreply.github.com"
git remote add origin https://gvimperialstudio-ship-it@github.com/gvimperialstudio-ship-it/erp-estoque-espelho.git
git add .
git status          # confirme que .env e .state/ NÃO aparecem
git commit -m "ERP estoque espelho: Bling Rennar -> Capital, Vero e Eluxia"
git push -u origin main
```
> Verifique sempre com `git status`: `.env` e `.state/` **nunca** podem ser commitados.

### 2. Autorizar o Bling da Rennar (em chamada — code expira em ~30s)
Com `BLING_CLIENT_ID`, `BLING_CLIENT_SECRET` e `BLING_REDIRECT_URI` (ex.: `https://github.com/GVtiktok/erp-estoque-espelho` conforme cadastrado no app Bling) no `.env` local:
```powershell
py -m erp auth-bling
```
1. Envie o link exibido para a Rennar (logada no Bling).
2. A Rennar clica em **Autorizar** → copia a URL inteira de retorno da barra de endereço e te envia **na hora** (expira em ~30 segundos).
3. Cole no terminal → copie o `BLING_REFRESH_TOKEN` retornado direto para o GitHub Secrets.
> **Atenção:** não rode `sync` local usando esse refresh token antes do 1º run no GitHub Actions, pois cada renovação invalida o token anterior.

### 3. Mercado Livre: 1 app novo + 3 autorizações
1. Crie um app **novo** em https://developers.mercadolivre.com.br (ex.: `Rennar Estoque`), Redirect URI `https://github.com/gvimperialstudio-ship-it/erp-estoque-espelho`.
   > Nunca reutilize o app/tokens das automações locais ativas (`ml_dashboard` / `LOJA_JEAN`), pois os refresh tokens giram e derrubam um ao outro.
   > (Se preferir usar um app diferente para algum canal, o ERP também aceita `MELI_CLIENT_ID_<CANAL>` e `MELI_CLIENT_SECRET_<CANAL>`.)
2. Coloque `MELI_CLIENT_ID` e `MELI_CLIENT_SECRET` no `.env` local e rode:
```powershell
py -m erp auth-meli --canal CAPITAL   # seller_id 3554514707
py -m erp auth-meli --canal VERO      # seller_id 3728045217 (dono da Vero abre logado na conta dele)
py -m erp auth-meli --canal ELUXIA    # seller_id 3720968135 (dono da Eluxia abre logado na conta dele)
```
Os `seller_id` das 3 contas já estão preenchidos em `config/config.yaml`.

### 4. Secrets e Variables no GitHub
Acesse `https://github.com/gvimperialstudio-ship-it/erp-estoque-espelho/settings/secrets/actions`:

| Aba Secrets (Repository secrets) | Valor |
|---|---|
| `STATE_KEY` | saída de `py -m erp gen-key` (ou valor do `.env`) |
| `BLING_CLIENT_ID` / `BLING_CLIENT_SECRET` | dados do app Bling da Rennar |
| `BLING_REFRESH_TOKEN` | saída de `py -m erp auth-bling` |
| `MELI_CLIENT_ID` / `MELI_CLIENT_SECRET` | dados do app novo no ML |
| `MELI_REFRESH_TOKEN_CAPITAL` | saída de `auth-meli --canal CAPITAL` |
| `MELI_REFRESH_TOKEN_VERO` | saída de `auth-meli --canal VERO` |
| `MELI_REFRESH_TOKEN_ELUXIA` | saída de `auth-meli --canal ELUXIA` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `ALERT_EMAIL_TO` | *(opcional)* alerta por e-mail SMTP direto |

Na aba **Variables**, crie `DRY_RUN = true`.

### 5. Simulação (dry-run) → Produção
1. Vá em **Actions → sync-estoque → Run workflow**.
2. Como ainda não existem anúncios da Rennar (com prefixo `REN-`), o relatório mostrará os anúncios próprios ignorados com segurança e sairá **OK** (zero erros).
3. Conforme for criando os anúncios da Rennar com SKU `REN-<codigo_bling>`, valide os primeiros no `dry_run` e depois mude a variável para `DRY_RUN = false`.
4. **Alertas:** falhas abrem/comentam a issue `erp-alerta`; cancelamentos e devoluções abrem a issue `cancelamento-alerta` (com e-mail do GitHub) + e-mail SMTP direto se configurado.

### 6. Tempo real (opcional — Fase 3)
Faça deploy de `worker/webhook-relay.js` no Cloudflare Workers (plano grátis), apontando `GH_REPO = "gvimperialstudio-ship-it/erp-estoque-espelho"`, e cadastre a URL do Worker nos webhooks de pedidos do ML (`orders_v2`) e de estoque do Bling.

### 7. Exportar catálogo da Rennar para criar novos anúncios
```powershell
py -m erp exportar-catalogo --detalhes --saida catalogo_rennar.csv
```
Exporta `sku`, `sku_ml_sugerido` (já com prefixo `REN-`), título, marca, preço, saldo atual, GTIN, NCM, CEST, peso, dimensões, descrições e URLs de imagens (aprox. 150 produtos/min).

## Testes
```powershell
py -m pytest -q
```
