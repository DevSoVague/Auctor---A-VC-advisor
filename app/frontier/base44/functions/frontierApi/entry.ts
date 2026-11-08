// Server-side proxy from the Base44 app to the Frontier run API (owner: person 2).
//
// Why a proxy: the run API needs a secret token (APP_API_TOKEN). Kept here as a Base44
// secret, it never reaches the browser. The proxy also:
//   - rejects anyone not logged in to the app;
//   - forwards only the run API's own paths (an allow-list), so it can't be used to reach
//     anything else;
//   - stamps the reviewer's email on every review decision, for the audit trail.
//
// Secrets (set with the Base44 CLI or dashboard): API_BASE_URL, APP_API_TOKEN.
import { createClientFromRequest } from "npm:@base44/sdk@0.8.59";

const ALLOWED_PATH =
  /^\/runs(\/[A-Za-z0-9_-]+(\/(checkpoint|citations|report|files(\/[A-Za-z0-9._-]+)?))?)?$/;

function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

Deno.serve(async (req) => {
  try {
    const base44 = createClientFromRequest(req);
    const user = await base44.auth.me().catch(() => null);
    if (!user) {
      return Response.json({ error: "Please log in to use the Frontier app." }, { status: 401 });
    }

    const { method = "GET", path = "/runs", body } = await req.json();
    if (!["GET", "POST"].includes(method) || typeof path !== "string" || !ALLOWED_PATH.test(path)) {
      return Response.json({ error: "That request isn't allowed." }, { status: 400 });
    }

    const base = Deno.env.get("API_BASE_URL");
    const token = Deno.env.get("APP_API_TOKEN");
    if (!base || !token) {
      return Response.json(
        { error: "The app isn't connected to the engine yet (API_BASE_URL / APP_API_TOKEN)." },
        { status: 500 },
      );
    }

    let payload = body ?? {};
    if (method === "POST" && path.endsWith("/checkpoint")) {
      const who = user.email || user.full_name || user.id;
      payload = { ...payload, comment: [payload.comment, `reviewed by ${who}`].filter(Boolean).join(" · ") };
    }

    const upstream = await fetch(base.replace(/\/$/, "") + path, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "bypass-tunnel-reminder": "1", // localtunnel: skip its browser warning page
      },
      body: method === "POST" ? JSON.stringify(payload) : undefined,
    });

    const type = upstream.headers.get("content-type") || "";
    if (type.includes("application/json")) {
      return Response.json({ status: upstream.status, data: await upstream.json() });
    }
    if (type.startsWith("text/")) {
      return Response.json({ status: upstream.status, text: await upstream.text() });
    }
    const bytes = new Uint8Array(await upstream.arrayBuffer());
    return Response.json({ status: upstream.status, base64: toBase64(bytes), contentType: type });
  } catch (err) {
    return Response.json({ error: `Couldn't reach the engine: ${err}` }, { status: 502 });
  }
});
