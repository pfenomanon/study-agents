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

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const sourcePath = String(body?.path || "").trim();
    const profileId = String(body?.profile_id || body?.profile || "").trim();
    if (!sourcePath) {
      return NextResponse.json({ error: "Missing path" }, { status: 400 });
    }

    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (COPILOT_API_KEY) {
      headers["X-API-Key"] = COPILOT_API_KEY;
    }

    const res = await fetchWithRetry(`${BASE}/copilot/prepare-markdown`, {
      method: "POST",
      headers,
      body: JSON.stringify({ path: sourcePath, profile_id: profileId || undefined }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return NextResponse.json(
        { error: data?.detail || data?.error || "prepare_markdown_error" },
        { status: res.status },
      );
    }
    return NextResponse.json(data);
  } catch (err: any) {
    return NextResponse.json({ error: err?.message || "proxy_error" }, { status: 500 });
  }
}
