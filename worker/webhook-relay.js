// OPCIONAL (Fase 3 - tempo real). Cloudflare Workers plano grátis.
// Recebe webhooks do Mercado Livre (orders_v2) e/ou do Bling (estoque) e dispara
// o workflow `sync-estoque` via repository_dispatch.
// Variáveis (wrangler secret put): HOOK_SECRET, GH_TOKEN (PAT fine-grained: Contents RW no repo)
// Variável pública: GH_REPO = "usuario/erp-estoque-espelho"
// URL de callback a cadastrar: https://<worker>.workers.dev/hook/<HOOK_SECRET>

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    if (req.method !== "POST" || url.pathname !== `/hook/${env.HOOK_SECRET}`) {
      return new Response("ok");
    }
    let topic = "";
    try {
      const body = await req.json();
      topic = body.topic || body.event || "";
    } catch (_) {}
    // ML: só pedidos interessam. Bling: qualquer evento de estoque/pedido.
    if (topic && !/orders|stock|estoque|order/i.test(topic)) return new Response("ignored");

    ctx.waitUntil(fetch(`https://api.github.com/repos/${env.GH_REPO}/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "erp-estoque-relay",
      },
      body: JSON.stringify({ event_type: "estoque-webhook", client_payload: { topic } }),
    }));
    return new Response("ok"); // ML exige resposta 200 rápida
  },
};
