import { NextRequest, NextResponse } from "next/server";

const BASE =
  process.env.COPILOT_BACKEND_URL?.replace(/\/copilot\/chat$/, "") || "http://copilot-service:9010";
const COPILOT_API_KEY = (process.env.COPILOT_API_KEY || process.env.API_TOKEN || "").trim();

async function fetchWithRetry(url: string, init: RequestInit, retries = 2) {
  let lastErr: unknown;
  for (let i = 0; i <= retries; i += 1) {
    try {
      return await fetch(url, init);
    } catch (err) {
      lastErr = err;
      if (i === retries) break;
      await new Promise((resolve) => setTimeout(resolve, 200 * (i + 1)));
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error("backend_fetch_failed");
}

function authHeaders(extra: Record<string, string> = {}) {
  const headers: Record<string, string> = { ...extra };
  if (COPILOT_API_KEY) {
    headers["X-API-Key"] = COPILOT_API_KEY;
  }
  return headers;
}

export async function GET(req: NextRequest) {
  try {
    const profileId = String(req.nextUrl.searchParams.get("profile_id") || "").trim();
    if (profileId) {
      const res = await fetchWithRetry(`${BASE}/profiles/${encodeURIComponent(profileId)}`, {
        headers: authHeaders(),
        cache: "no-store",
      });
      const data = await res.json().catch(() => ({}));
      return NextResponse.json(data, { status: res.status });
    }
    const search = req.nextUrl.searchParams.toString();
    const url = `${BASE}/profiles${search ? `?${search}` : ""}`;
    const res = await fetchWithRetry(url, { headers: authHeaders() });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err: any) {
    return NextResponse.json({ error: err?.message || "proxy_error" }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const action = String(body?.action || "").trim().toLowerCase();
    const path =
      action === "use"
        ? "/profiles/use"
        : action === "purge"
          ? "/profiles/purge"
          : action === "delete"
            ? "/profiles/delete"
          : "/profiles";
    const res = await fetchWithRetry(`${BASE}${path}`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err: any) {
    return NextResponse.json({ error: err?.message || "proxy_error" }, { status: 500 });
  }
}
