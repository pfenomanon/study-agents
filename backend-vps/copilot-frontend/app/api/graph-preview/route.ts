import fs from "fs";
import path from "path";
import readline from "readline";
import { NextRequest, NextResponse } from "next/server";

const BASE =
  process.env.COPILOT_BACKEND_URL?.replace(/\/copilot\/chat$/, "") || "https://copilot-service:9010";
const COPILOT_API_KEY = (process.env.COPILOT_API_KEY || process.env.API_TOKEN || "").trim();
const ALLOWED_ROOTS = [path.resolve("/app/data"), path.resolve("/app/research_output")];

type GraphPaths = {
  nodesPath: string;
  edgesPath: string;
};

type GraphNode = {
  id: string;
  title: string;
  type: string;
};

type GraphEdge = {
  src: string;
  dst: string;
  rel: string;
};

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

function resolveSafeAbsolute(rawPath: string): string {
  const target = path.resolve(String(rawPath || "").trim());
  const inRoot = ALLOWED_ROOTS.some((root) => target === root || target.startsWith(`${root}${path.sep}`));
  if (!inRoot) {
    throw new Error(`Path is outside allowed roots: ${target}`);
  }
  return target;
}

async function fileExists(filePath: string): Promise<boolean> {
  try {
    await fs.promises.access(filePath, fs.constants.R_OK);
    return true;
  } catch {
    return false;
  }
}

async function findGraphFilesFromFolder(folderPath: string): Promise<GraphPaths | null> {
  try {
    const folder = resolveSafeAbsolute(folderPath);
    const entries = await fs.promises.readdir(folder);
    const nodesName = entries.find((name) => name.toLowerCase().endsWith(".nodes.jsonl"));
    const edgesName = entries.find((name) => name.toLowerCase().endsWith(".edges.jsonl"));
    if (!nodesName || !edgesName) return null;
    return {
      nodesPath: path.join(folder, nodesName),
      edgesPath: path.join(folder, edgesName),
    };
  } catch {
    return null;
  }
}

async function pickGraphPaths(profilePayload: any): Promise<GraphPaths | null> {
  const artifacts = Array.isArray(profilePayload?.recent_artifacts)
    ? profilePayload.recent_artifacts
    : [];
  for (const artifact of artifacts) {
    const meta = artifact?.metadata || {};
    const bundle = meta?.artifacts || {};
    const nodesPathRaw = String(bundle?.nodes || "").trim();
    const edgesPathRaw = String(bundle?.edges || "").trim();
    if (nodesPathRaw && edgesPathRaw) {
      const nodesPath = resolveSafeAbsolute(nodesPathRaw);
      const edgesPath = resolveSafeAbsolute(edgesPathRaw);
      if ((await fileExists(nodesPath)) && (await fileExists(edgesPath))) {
        return { nodesPath, edgesPath };
      }
    }

    const folderPathRaw = String(bundle?.folder || artifact?.path || "").trim();
    if (folderPathRaw) {
      const found = await findGraphFilesFromFolder(folderPathRaw);
      if (found) return found;
    }
  }
  return null;
}

async function readJsonl(filePath: string, maxItems: number): Promise<any[]> {
  const rows: any[] = [];
  const stream = fs.createReadStream(filePath, { encoding: "utf-8" });
  const rl = readline.createInterface({ input: stream, crlfDelay: Infinity });
  try {
    for await (const line of rl) {
      if (rows.length >= maxItems) break;
      const trimmed = String(line || "").trim();
      if (!trimmed) continue;
      try {
        rows.push(JSON.parse(trimmed));
      } catch {
        // ignore malformed lines
      }
    }
  } finally {
    rl.close();
    stream.close();
  }
  return rows;
}

function filterGraphByQuery(
  nodes: GraphNode[],
  edges: GraphEdge[],
  query: string,
  maxNodes: number,
  maxEdges: number,
): { nodes: GraphNode[]; edges: GraphEdge[]; matchedNodeCount: number } {
  const trimmed = String(query || "").trim().toLowerCase();
  const tokens = Array.from(
    new Set(
      trimmed
        .replace(/[^a-z0-9\s_-]+/g, " ")
        .split(/\s+/)
        .map((t) => t.trim())
        .filter((t) => t.length >= 3),
    ),
  );
  if (!trimmed || tokens.length === 0) {
    const subsetNodes = nodes.slice(0, maxNodes);
    const nodeIds = new Set(subsetNodes.map((n) => n.id));
    const subsetEdges = edges.filter((e) => nodeIds.has(e.src) && nodeIds.has(e.dst)).slice(0, maxEdges);
    return { nodes: subsetNodes, edges: subsetEdges, matchedNodeCount: subsetNodes.length };
  }

  const scored = nodes
    .map((node) => {
      const haystack = `${node.id} ${node.title} ${node.type}`.toLowerCase();
      const score = tokens.reduce((acc, token) => (haystack.includes(token) ? acc + 1 : acc), 0);
      return { node, score };
    })
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score);

  const matched = scored.map((item) => item.node);
  const selected = new Set<string>(matched.slice(0, maxNodes).map((n) => n.id));
  const matchedNodeCount = matched.length;

  // Add one-hop neighbors around matched nodes so the subgraph has context.
  for (const edge of edges) {
    if (selected.size >= maxNodes) break;
    if (selected.has(edge.src) || selected.has(edge.dst)) {
      selected.add(edge.src);
      selected.add(edge.dst);
    }
  }

  const subsetNodes = nodes.filter((node) => selected.has(node.id)).slice(0, maxNodes);
  const subsetIds = new Set(subsetNodes.map((n) => n.id));
  const subsetEdges = edges
    .filter((edge) => subsetIds.has(edge.src) && subsetIds.has(edge.dst))
    .slice(0, maxEdges);

  return { nodes: subsetNodes, edges: subsetEdges, matchedNodeCount };
}

function filterGraphByLiteral(
  nodes: GraphNode[],
  query: string,
): GraphNode[] {
  const trimmed = String(query || "").trim().toLowerCase();
  if (!trimmed) return [];
  return nodes.filter((node) => {
    const haystack = `${node.id} ${node.title} ${node.type}`.toLowerCase();
    return haystack.includes(trimmed);
  });
}

export async function GET(req: NextRequest) {
  try {
    const profileId = String(req.nextUrl.searchParams.get("profile_id") || "").trim();
    const query = String(req.nextUrl.searchParams.get("query") || "").trim();
    const maxNodes = Math.max(20, Math.min(300, Number.parseInt(req.nextUrl.searchParams.get("max_nodes") || "140", 10) || 140));
    const maxEdges = Math.max(20, Math.min(600, Number.parseInt(req.nextUrl.searchParams.get("max_edges") || "260", 10) || 260));
    if (!profileId) {
      return NextResponse.json({ error: "Missing profile_id" }, { status: 400 });
    }

    // Primary source: backend profile-scoped graph rows from Supabase.
    try {
      const graphParams = new URLSearchParams({
        query,
        max_nodes: String(maxNodes),
        max_edges: String(maxEdges),
      });
      const graphRes = await fetchWithRetry(
        `${BASE}/profiles/${encodeURIComponent(profileId)}/graph?${graphParams.toString()}`,
        {
          headers: authHeaders(),
          cache: "no-store",
        },
      );
      const graphData = await graphRes.json().catch(() => ({}));
      if (graphRes.ok) {
        return NextResponse.json(graphData);
      }
    } catch {
      // Fallback below for older backend deployments that don't expose /profiles/{id}/graph.
    }

    // Legacy fallback: graph artifacts from profile history.
    const profileRes = await fetchWithRetry(`${BASE}/profiles/${encodeURIComponent(profileId)}`, {
      headers: authHeaders(),
      cache: "no-store",
    });
    const profileData = await profileRes.json().catch(() => ({}));
    if (!profileRes.ok) {
      return NextResponse.json(
        { error: profileData?.detail || profileData?.error || "profile_lookup_failed" },
        { status: profileRes.status },
      );
    }

    const graphPaths = await pickGraphPaths(profileData);
    if (!graphPaths) {
      return NextResponse.json(
        { error: "No graph artifacts found for this profile yet." },
        { status: 404 },
      );
    }

    const [rawNodes, rawEdges] = await Promise.all([
      readJsonl(graphPaths.nodesPath, 1200),
      readJsonl(graphPaths.edgesPath, 2200),
    ]);

    const nodeMap = new Map<string, GraphNode>();
    for (const node of rawNodes) {
      const id = String(node?.id || "").trim();
      if (!id || nodeMap.has(id)) continue;
      nodeMap.set(id, {
        id,
        title: String(node?.title || id).trim() || id,
        type: String(node?.type || "Node").trim() || "Node",
      });
    }

    const edges: GraphEdge[] = [];
    for (const edge of rawEdges) {
      const src = String(edge?.src || "").trim();
      const dst = String(edge?.dst || "").trim();
      if (!src || !dst) continue;
      if (!nodeMap.has(src) || !nodeMap.has(dst)) continue;
      edges.push({
        src,
        dst,
        rel: String(edge?.rel || "").trim() || "related_to",
      });
    }

    const fullNodes = Array.from(nodeMap.values());
    const filtered = filterGraphByQuery(fullNodes, edges, query, maxNodes, maxEdges);
    const literalMatches = filterGraphByLiteral(fullNodes, query).length;

    return NextResponse.json({
      ok: true,
      profile_id: profileId,
      query,
      nodes_path: graphPaths.nodesPath,
      edges_path: graphPaths.edgesPath,
      nodes: filtered.nodes,
      edges: filtered.edges,
      counts: {
        nodes: filtered.nodes.length,
        edges: filtered.edges.length,
        matched_nodes: filtered.matchedNodeCount,
        literal_matches: literalMatches,
        total_nodes: fullNodes.length,
        total_edges: edges.length,
      },
    });
  } catch (err: any) {
    return NextResponse.json({ error: err?.message || "graph_preview_error" }, { status: 500 });
  }
}
