import { NextRequest, NextResponse } from "next/server";

const BACKEND_CAPTURE_IMAGE =
  process.env.COPILOT_CAPTURE_IMAGE_URL ||
  (process.env.COPILOT_BACKEND_URL
    ? process.env.COPILOT_BACKEND_URL.replace("/copilot/chat", "/copilot/capture-image")
    : "http://localhost:9010/copilot/capture-image");
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
    const form = await req.formData();
    const headers: Record<string, string> = {};
    if (COPILOT_API_KEY) {
      headers["X-API-Key"] = COPILOT_API_KEY;
    }

    const res = await fetchWithRetry(BACKEND_CAPTURE_IMAGE, {
      method: "POST",
      headers,
      body: form,
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err: any) {
    return NextResponse.json({ error: err?.message || "capture_image_proxy_error" }, { status: 500 });
  }
}
