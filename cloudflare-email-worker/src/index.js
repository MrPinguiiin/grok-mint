const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}

function isAuthorized(request, env) {
  const expected = String(env.API_TOKEN || "");
  const authorization = request.headers.get("authorization") || "";
  return expected.length >= 24 && authorization === `Bearer ${expected}`;
}

function normalizeAddress(value) {
  return String(value || "").trim().toLowerCase();
}

function randomLocalPart() {
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  return Array.from(bytes, (value) => value.toString(36).padStart(2, "0"))
    .join("")
    .slice(0, 16);
}

function parseLimit(url) {
  const value = Number.parseInt(url.searchParams.get("limit") || "20", 10);
  return Math.min(100, Math.max(1, Number.isFinite(value) ? value : 20));
}

async function handleApi(request, env) {
  if (!isAuthorized(request, env)) {
    return json({ ok: false, error: "unauthorized" }, 401);
  }

  const url = new URL(request.url);
  if (request.method === "GET" && url.pathname === "/api/health") {
    return json({ ok: true, service: "temp-mail-grok" });
  }

  if (request.method === "POST" && url.pathname === "/api/new_address") {
    const domain = normalizeAddress(env.MAIL_DOMAIN);
    if (!domain) {
      return json({ ok: false, error: "MAIL_DOMAIN is not configured" }, 500);
    }
    const address = `${randomLocalPart()}@${domain}`;
    return json({ ok: true, address });
  }

  if (request.method === "GET" && url.pathname === "/api/mails") {
    const recipient = normalizeAddress(url.searchParams.get("recipient"));
    if (!recipient) {
      return json({ ok: false, error: "recipient is required" }, 400);
    }
    const result = await env.DB.prepare(
      `SELECT id, recipient, sender, subject, received_at
       FROM messages
       WHERE lower(recipient) = ?
       ORDER BY id DESC
       LIMIT ?`,
    ).bind(recipient, parseLimit(url)).all();
    return json({ ok: true, messages: result.results || [] });
  }

  const detailMatch = url.pathname.match(/^\/api\/mail\/(\d+)$/);
  if (request.method === "GET" && detailMatch) {
    const message = await env.DB.prepare(
      `SELECT id, recipient, sender, subject, raw, received_at
       FROM messages
       WHERE id = ?`,
    ).bind(Number(detailMatch[1])).first();
    if (!message) {
      return json({ ok: false, error: "message not found" }, 404);
    }
    return json({ ok: true, message });
  }

  if (request.method === "DELETE" && detailMatch) {
    const result = await env.DB.prepare("DELETE FROM messages WHERE id = ?")
      .bind(Number(detailMatch[1]))
      .run();
    return json({ ok: true, deleted: Number(result.meta?.changes || 0) });
  }

  return json({ ok: false, error: "not found" }, 404);
}

export default {
  async email(message, env) {
    const recipient = normalizeAddress(message.to);
    const sender = normalizeAddress(message.from);
    const subject = message.headers.get("subject") || "";
    const raw = await new Response(message.raw).text();

    await env.DB.prepare(
      `INSERT INTO messages (recipient, sender, subject, raw)
       VALUES (?, ?, ?, ?)`,
    ).bind(recipient, sender, subject, raw).run();
  },

  async fetch(request, env) {
    try {
      return await handleApi(request, env);
    } catch {
      console.error("request failed");
      return json({ ok: false, error: "internal error" }, 500);
    }
  },
};
