export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "*";

    const corsHeaders = {
      "Access-Control-Allow-Origin": origin,
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders });
    }

    const url = new URL(request.url);

    try {
      if (url.pathname === "/votes" && request.method === "GET") {
        return await getVotes(env, corsHeaders);
      }

      if (url.pathname === "/vote" && request.method === "POST") {
        return await postVote(request, env, corsHeaders);
      }

      return new Response("Not found", { status: 404, headers: corsHeaders });
    } catch (err) {
      return new Response(JSON.stringify({ error: err.message }), {
        status: 500,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }
  },
};

async function getVotes(env, corsHeaders) {
  const results = await env.DB.prepare(
    `SELECT model, COUNT(*) as count, ROUND(AVG(rating), 1) as avg_rating
     FROM votes GROUP BY model`
  ).all();

  return new Response(JSON.stringify(results.results), {
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

async function postVote(request, env, corsHeaders) {
  const body = await request.json();
  const { model, rating, ts, hmac } = body;

  if (!model || !rating || !ts || !hmac) {
    return new Response(JSON.stringify({ error: "Missing fields" }), {
      status: 400,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }

  if (typeof rating !== "number" || rating < 1 || rating > 5) {
    return new Response(JSON.stringify({ error: "Rating must be 1-5" }), {
      status: 400,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }

  const now = Date.now();
  if (Math.abs(now - ts) > 300000) {
    return new Response(JSON.stringify({ error: "Request expired" }), {
      status: 400,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }

  const valid = await verifyHmac(env.HMAC_SECRET, model, rating, ts, hmac);
  if (!valid) {
    return new Response(JSON.stringify({ error: "Invalid signature" }), {
      status: 403,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }

  await env.DB.prepare("INSERT INTO votes (model, rating) VALUES (?, ?)")
    .bind(model, rating)
    .run();

  return new Response(JSON.stringify({ ok: true }), {
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

async function verifyHmac(secret, model, rating, ts, hmac) {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"]
  );

  const message = `${model}:${rating}:${ts}`;
  const sigBytes = hexToBytes(hmac);

  return crypto.subtle.verify("HMAC", key, sigBytes, encoder.encode(message));
}

function hexToBytes(hex) {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < hex.length; i += 2) {
    bytes[i / 2] = parseInt(hex.substr(i, 2), 16);
  }
  return bytes;
}
