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

export async function GET(req: NextRequest) {
  try {
    const profileId = String(req.nextUrl.searchParams.get("profile_id") || "").trim();
    const limitRaw = String(req.nextUrl.searchParams.get("limit") || "").trim();
    if (!profileId) {
      return NextResponse.json({ error: "Missing profile_id" }, { status: 400 });
    }
    const limit = Number.parseInt(limitRaw || "10", 10);
    const params = new URLSearchParams({
      profile_id: profileId,
      limit: String(Number.isFinite(limit) ? Math.max(1, Math.min(limit, 100)) : 10),
    });
    const headers: Record<string, string> = {};
    if (COPILOT_API_KEY) {
      headers["X-API-Key"] = COPILOT_API_KEY;
    }
    const res = await fetchWithRetry(`${BASE}/domain/wizard/history?${params.toString()}`, {
      headers,
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err: any) {
    return NextResponse.json({ error: err?.message || "proxy_error" }, { status: 500 });
  }
}
