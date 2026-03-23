import fs from "fs/promises";
import path from "path";
import { NextRequest, NextResponse } from "next/server";
import { randomBytes } from "crypto";

export const revalidate = 0;

const ROOT = path.resolve(process.env.RESEARCH_ROOT || "/app/research_output");

function sanitizeName(name: string) {
  return name.replace(/[^a-zA-Z0-9_.-]/g, "_");
}

function isFilePart(value: FormDataEntryValue): value is File {
  return typeof value !== "string" && typeof (value as any).arrayBuffer === "function";
}

function sanitizeProfile(raw: string | null) {
  const cleaned = String(raw || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return cleaned;
}

function uploadRoot(profile: string | null) {
  const cleaned = sanitizeProfile(profile);
  if (!cleaned) {
    return path.join(ROOT, "uploads");
  }
  return path.join(ROOT, "profiles", cleaned, "uploads");
}

async function ensureDirs(dir: string) {
  await fs.mkdir(dir, { recursive: true });
}

async function listUploads(dir: string, rootPrefix: string) {
  try {
    const entries = await fs.readdir(dir, { withFileTypes: true });
    return entries
      .filter((e) => e.isFile())
      .map((e) => ({ name: e.name, path: path.join(rootPrefix, e.name) }))
      .sort((a, b) => a.name.localeCompare(b.name));
  } catch (err: any) {
    if (err.code === "ENOENT") return [];
    throw err;
  }
}

export async function GET(req: NextRequest) {
  try {
    const profile = req.nextUrl.searchParams.get("profile");
    const cleaned = sanitizeProfile(profile);
    const dir = uploadRoot(profile);
    const prefix = cleaned ? path.join("profiles", cleaned, "uploads") : "uploads";
    const files = await listUploads(dir, prefix);
    return NextResponse.json({ files, profile: cleaned || null });
  } catch (err: any) {
    return NextResponse.json({ error: err?.message || "list_error" }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const form = await req.formData();
    const fileParts = form.getAll("file");
    const files = fileParts.filter(isFilePart);
    const profileRaw = form.get("profile");
    const profile = typeof profileRaw === "string" ? profileRaw : "";
    const cleaned = sanitizeProfile(profile || null);
    const dir = uploadRoot(profile || null);
    if (!files.length) throw new Error("Missing file");

    await ensureDirs(dir);

    const uploaded: Array<{ name: string; path: string }> = [];
    for (const file of files) {
      const bytes = Buffer.from(await file.arrayBuffer());
      let name = sanitizeName(file.name || "upload");
      if (!name) name = "upload";
      const unique = `${Date.now()}-${randomBytes(3).toString("hex")}-${name}`;
      const target = path.join(dir, unique);
      await fs.writeFile(target, bytes);

      const rel = cleaned
        ? path.join("profiles", cleaned, "uploads", unique)
        : path.join("uploads", unique);
      uploaded.push({ name, path: rel });
    }

    return NextResponse.json({
      ok: true,
      files: uploaded,
      count: uploaded.length,
      profile: cleaned || null,
    });
  } catch (err: any) {
    return NextResponse.json({ error: err?.message || "upload_error" }, { status: 400 });
  }
}

export async function DELETE(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const relPath = String(body?.path || "").trim();
    if (!relPath) throw new Error("Missing path");
    const target = path.resolve(ROOT, relPath);
    if (target !== ROOT && !target.startsWith(`${ROOT}${path.sep}`)) {
      throw new Error("Invalid path");
    }
    await fs.unlink(target);
    return NextResponse.json({ ok: true });
  } catch (err: any) {
    const msg = err?.message || "delete_error";
    const status = msg.includes("Invalid") ? 400 : 404;
    return NextResponse.json({ error: msg }, { status });
  }
}
