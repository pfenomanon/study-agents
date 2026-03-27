import { NextRequest, NextResponse } from "next/server";

const BACKEND_CAPTURE =
  process.env.COPILOT_CAPTURE_URL ||
  (process.env.COPILOT_BACKEND_URL
    ? process.env.COPILOT_BACKEND_URL.replace("/copilot/chat", "/copilot/capture")
    : "https://copilot-service:9010/copilot/capture");
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
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (COPILOT_API_KEY) {
      headers["X-API-Key"] = COPILOT_API_KEY;
    }
    const res = await fetchWithRetry(BACKEND_CAPTURE, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err: any) {
    return NextResponse.json({ error: err?.message || "capture_proxy_error" }, { status: 500 });
  }
}
