import fs from "fs/promises";
import path from "path";
import { NextRequest, NextResponse } from "next/server";

export const revalidate = 0;

const DATA_ROOT = path.resolve("/app/data");
const RESEARCH_ROOT = path.resolve(process.env.RESEARCH_ROOT || "/app/research_output");
const ALLOWED_ROOTS = [DATA_ROOT, RESEARCH_ROOT];

type DocEntry = {
  label: string;
  path: string; // absolute in container
};

type ScanRoot = {
  root: string;
  labelPrefix: string;
};

function sanitizeProfile(raw: string | null) {
  const cleaned = String(raw || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return cleaned;
}

function resolveScanRoots(profile: string | null): ScanRoot[] {
  const cleaned = sanitizeProfile(profile);
  if (!cleaned) {
    return [];
  }
  return [
    {
      root: path.resolve(DATA_ROOT, "output", "research", "profiles", cleaned),
      labelPrefix: `data/output/research/profiles/${cleaned}`,
    },
    {
      root: path.resolve(DATA_ROOT, "profiles", cleaned),
      labelPrefix: `data/profiles/${cleaned}`,
    },
    {
      root: path.resolve(RESEARCH_ROOT, "profiles", cleaned),
      labelPrefix: `research/profiles/${cleaned}`,
    },
  ];
}

async function walkPdfs(root: string, prefixLabel: string): Promise<DocEntry[]> {
  const entries: DocEntry[] = [];
  const walk = async (dir: string) => {
    const items = await fs.readdir(dir, { withFileTypes: true });
    for (const item of items) {
      const full = path.join(dir, item.name);
      if (item.isDirectory()) {
        await walk(full);
      } else if (item.isFile()) {
        const lower = item.name.toLowerCase();
        if (lower.endsWith(".pdf") || lower.endsWith(".md")) {
          const rel = path.relative(root, full);
          entries.push({
            label: `${prefixLabel}/${rel}`,
            path: full,
          });
        }
      }
    }
  };
  try {
    await walk(root);
  } catch (err: any) {
    if (err.code !== "ENOENT") throw err;
  }
  return entries;
}

export async function GET(req: NextRequest) {
  try {
    const profile = req.nextUrl.searchParams.get("profile");
    const roots = resolveScanRoots(profile);
    if (roots.length === 0) {
      return NextResponse.json({ docs: [], profile: null });
    }
    const batches = await Promise.all(roots.map((item) => walkPdfs(item.root, item.labelPrefix)));
    const merged = batches.flat();
    const uniqueByPath = Array.from(new Map(merged.map((entry) => [entry.path, entry])).values());
    const docs = uniqueByPath.sort((a, b) => a.label.localeCompare(b.label));
    return NextResponse.json({ docs, profile: sanitizeProfile(profile) || null });
  } catch (err: any) {
    return NextResponse.json({ error: err?.message || "list_error" }, { status: 500 });
  }
}

function isWithinAllowedRoots(target: string): boolean {
  return ALLOWED_ROOTS.some((root) => target === root || target.startsWith(`${root}${path.sep}`));
}

function resolveDocPath(rawPath: string): string {
  const cleaned = String(rawPath || "").trim();
  if (!cleaned) {
    throw new Error("Missing path");
  }
  const target = path.resolve(cleaned);
  if (!isWithinAllowedRoots(target)) {
    throw new Error("Invalid path");
  }
  return target;
}

export async function DELETE(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const single = typeof body?.path === "string" ? [body.path] : [];
    const many = Array.isArray(body?.paths) ? body.paths : [];
    const paths = [...single, ...many]
      .map((item) => String(item || "").trim())
      .filter(Boolean);

    if (paths.length === 0) {
      throw new Error("Missing path");
    }

    const uniqueTargets = Array.from(new Set(paths.map(resolveDocPath)));
    const deleted: string[] = [];
    const failed: { path: string; error: string }[] = [];

    for (const target of uniqueTargets) {
      try {
        await fs.unlink(target);
        deleted.push(target);
      } catch (err: any) {
        failed.push({ path: target, error: err?.message || "delete_error" });
      }
    }

    return NextResponse.json({ ok: failed.length === 0, deleted, failed });
  } catch (err: any) {
    const msg = err?.message || "delete_error";
    const status = msg.includes("Invalid path") || msg.includes("Missing path") ? 400 : 500;
    return NextResponse.json({ error: msg }, { status });
  }
}
