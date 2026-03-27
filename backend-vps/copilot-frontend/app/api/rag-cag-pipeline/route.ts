import { NextRequest, NextResponse } from "next/server";
import fs from "fs/promises";
import path from "path";

const CHAT_BACKEND = process.env.COPILOT_BACKEND_URL || "https://copilot-service:9010/copilot/chat";
const BASE = CHAT_BACKEND.replace(/\/copilot\/chat$/, "");
const COPILOT_API_KEY = (process.env.COPILOT_API_KEY || process.env.API_TOKEN || "").trim();
const DATA_ROOT = path.resolve("/app/data");
const RESEARCH_ROOT = path.resolve(process.env.RESEARCH_ROOT || "/app/research_output");
const ALLOWED_DELETE_ROOTS = [DATA_ROOT, RESEARCH_ROOT];

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

function isPdfPath(value: string): boolean {
  return String(value || "").trim().toLowerCase().endsWith(".pdf");
}

function resolveSafeDeleteTarget(rawPath: string): string {
  const target = path.resolve(String(rawPath || "").trim());
  if (!target) throw new Error("invalid_delete_target");
  const inAllowed = ALLOWED_DELETE_ROOTS.some(
    (root) => target === root || target.startsWith(`${root}${path.sep}`),
  );
  if (!inAllowed) throw new Error("delete_target_outside_allowed_roots");
  return target;
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const path = String(body?.path || "").trim();
    const profileId = String(body?.profile_id || body?.profile || "").trim();
    if (!path) {
      return NextResponse.json({ error: "Missing path" }, { status: 400 });
    }

    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (COPILOT_API_KEY) {
      headers["X-API-Key"] = COPILOT_API_KEY;
    }

    let effectivePath = path;
    let preparedMarkdownPath: string | undefined;
    if (isPdfPath(path)) {
      const prepRes = await fetchWithRetry(`${BASE}/copilot/prepare-markdown`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          path,
          profile_id: profileId || undefined,
        }),
      });
      const prepData = await prepRes.json().catch(() => ({}));
      if (!prepRes.ok) {
        return NextResponse.json(
          {
            error: prepData?.detail || prepData?.error || `prepare_markdown_failed_${prepRes.status}`,
            step: "prepare_markdown",
            prepare_markdown: prepData,
          },
          { status: prepRes.status },
        );
      }
      preparedMarkdownPath = String(prepData?.markdown_path || "").trim() || undefined;
      if (!preparedMarkdownPath) {
        return NextResponse.json(
          { error: "prepare_markdown_missing_output", step: "prepare_markdown", prepare_markdown: prepData },
          { status: 500 },
        );
      }
      effectivePath = preparedMarkdownPath;
    }

    const ragRes = await fetchWithRetry(`${BASE}/copilot/chat`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        message: `RAG: Build bundle for ${effectivePath}`,
        profile_id: profileId || undefined,
      }),
    });
    const ragData = await ragRes.json().catch(() => ({}));
    if (!ragRes.ok) {
      return NextResponse.json(
        {
          error: ragData?.detail || ragData?.error || `rag_failed_${ragRes.status}`,
          step: "rag",
          rag: ragData,
        },
        { status: ragRes.status },
      );
    }

    const cagRes = await fetchWithRetry(`${BASE}/copilot/cag-process`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        path: effectivePath,
        profile_id: profileId || undefined,
      }),
    });
    const cagData = await cagRes.json().catch(() => ({}));
    if (!cagRes.ok) {
      return NextResponse.json(
        {
          error: cagData?.detail || cagData?.error || `cag_failed_${cagRes.status}`,
          step: "cag",
          rag: ragData,
          cag: cagData,
          processed_path: effectivePath,
          prepared_markdown_path: preparedMarkdownPath,
        },
        { status: cagRes.status },
      );
    }

    let sourceDeleted = false;
    let deleteWarning: string | undefined;
    if (isPdfPath(path)) {
      try {
        const deleteTarget = resolveSafeDeleteTarget(path);
        await fs.unlink(deleteTarget);
        sourceDeleted = true;
      } catch (err: any) {
        deleteWarning = err?.message || "source_delete_failed";
      }
    }

    return NextResponse.json({
      ok: true,
      path,
      processed_path: effectivePath,
      prepared_markdown_path: preparedMarkdownPath,
      profile_id: profileId || undefined,
      source_deleted: sourceDeleted,
      delete_warning: deleteWarning,
      rag: ragData,
      cag: cagData,
    });
  } catch (err: any) {
    return NextResponse.json({ error: err?.message || "pipeline_proxy_error" }, { status: 500 });
  }
}
