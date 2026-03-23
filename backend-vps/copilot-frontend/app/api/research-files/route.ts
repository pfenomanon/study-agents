import fs from "fs/promises";
import path from "path";
import { NextRequest, NextResponse } from "next/server";

export const revalidate = 0;

const ROOT = path.resolve(process.env.RESEARCH_ROOT || "/app/research_output");

function sanitizeProfile(raw: string | null) {
  const cleaned = String(raw || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return cleaned;
}

function scopedRoot(profile: string | null) {
  const cleaned = sanitizeProfile(profile);
  if (!cleaned) return ROOT;
  return path.resolve(ROOT, "profiles", cleaned);
}

function resolveSafe(root: string, userPath: string) {
  const target = path.resolve(root, userPath || "");
  if (target !== root && !target.startsWith(`${root}${path.sep}`)) {
    throw new Error("Invalid path");
  }
  return target;
}

async function listFiles(root: string) {
  const entries: string[] = [];
  const walk = async (dir: string) => {
    const items = await fs.readdir(dir, { withFileTypes: true });
    for (const item of items) {
      const full = path.join(dir, item.name);
      if (item.isDirectory()) {
        await walk(full);
      } else if (item.isFile() && item.name.toLowerCase().endsWith(".md")) {
        entries.push(path.relative(root, full));
      }
    }
  };
  try {
    await walk(root);
  } catch (err: any) {
    if (err.code === "ENOENT") return [];
    throw err;
  }
  return entries.sort();
}

export async function GET(req: NextRequest) {
  const searchParams = req.nextUrl.searchParams;
  const relPath = searchParams.get("path");
  const profile = searchParams.get("profile");
  const root = scopedRoot(profile);

  try {
    if (!relPath) {
      const files = await listFiles(root);
      return NextResponse.json({ files, profile: sanitizeProfile(profile) || null });
    }

    const target = resolveSafe(root, relPath);
    const content = await fs.readFile(target, "utf-8");
    return NextResponse.json({ path: relPath, content, profile: sanitizeProfile(profile) || null });
  } catch (err: any) {
    return NextResponse.json({ error: err?.message || "read_error" }, { status: 400 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const relPath = String(body?.path || "").trim();
    const content = String(body?.content ?? "");
    const root = scopedRoot(body?.profile || null);
    if (!relPath) throw new Error("Missing path");

    const target = resolveSafe(root, relPath);
    await fs.mkdir(path.dirname(target), { recursive: true });
    await fs.writeFile(target, content, "utf-8");

    return NextResponse.json({ ok: true, profile: sanitizeProfile(body?.profile || null) || null });
  } catch (err: any) {
    return NextResponse.json({ error: err?.message || "write_error" }, { status: 400 });
  }
}

export async function DELETE(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const relPath = String(body?.path || "").trim();
    const root = scopedRoot(body?.profile || null);
    if (!relPath) throw new Error("Missing path");

    const target = resolveSafe(root, relPath);
    await fs.unlink(target);
    return NextResponse.json({ ok: true, profile: sanitizeProfile(body?.profile || null) || null });
  } catch (err: any) {
    const msg = err?.message || "delete_error";
    const status = msg.includes("Invalid path") ? 400 : 404;
    return NextResponse.json({ error: msg }, { status });
  }
}
