"""Agent-agnostic helper that visualizes the knowledge graph and validates CAG via a test question."""
from __future__ import annotations

from argparse import ArgumentParser
from dataclasses import dataclass
import csv
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Sequence

from .config import (
    SUPABASE_KEY,
    SUPABASE_URL,
)
from .kb_capture_agent import answer_with_cag
from .supabase_client import create_supabase_client


@dataclass
class GraphArtifact:
    mermaid_path: Path
    nodes: list[Mapping]
    edges: list[Mapping]


def _require_supabase_client():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL/SUPABASE_KEY are required for graph inspection.")
    return create_supabase_client()


def fetch_graph():
    supabase = _require_supabase_client()
    nodes = supabase.table("kg_nodes").select("*").execute().data or []
    edges = supabase.table("kg_edges").select("*").execute().data or []
    return nodes, edges


def build_mermaid(nodes: Iterable[Mapping], edges: Iterable[Mapping]) -> str:
    lines = ["flowchart TD"]
    for node in nodes:
        node_id = node.get("id") or f"node{hash(node.get('title',''))}"
        label = node.get("title", "Node").replace('"', '\\"')
        lines.append(f'    {node_id}["{label}"]')
    for edge in edges:
        src = edge.get("src")
        dst = edge.get("dst")
        rel = edge.get("rel", "").replace('"', '\\"')
        if not (src and dst):
            continue
        label_part = f"|{rel}|" if rel else ""
        lines.append(f"    {src} -->{label_part} {dst}")
    return "\n".join(lines)


def filter_graph(
    nodes: Sequence[Mapping],
    edges: Sequence[Mapping],
    group_id: Optional[str] = None,
) -> tuple[list[Mapping], list[Mapping]]:
    """Return nodes/edges filtered to a specific group id."""
    if not group_id:
        return list(nodes), list(edges)

    filtered_nodes = [n for n in nodes if (n.get("group_id") or "") == group_id]
    node_ids = {n.get("id") for n in filtered_nodes}
    filtered_edges = [
        e
        for e in edges
        if e.get("src") in node_ids and e.get("dst") in node_ids
    ]
    return filtered_nodes, filtered_edges


def slugify(value: str) -> str:
    value = value or "ungrouped"
    value = re.sub(r"[^0-9A-Za-z-]+", "-", value.strip())
    return re.sub(r"-{2,}", "-", value).strip("-").lower() or "group"


def write_mermaid_md(mermaid: str, outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / "graph_inspector.md"
    path.write_text(mermaid, encoding="utf-8")
    return path


def write_mermaid_mmd(mermaid: str, outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / "graph_inspector.mmd"
    path.write_text(mermaid, encoding="utf-8")
    return path


def convert_to_svg(mermaid_path: Path, outdir: Path) -> Optional[Path]:
    svg_path = outdir / "graph_inspector.svg"
    mmdc = shutil.which("mmdc")
    if mmdc:
        cmd = [mmdc, "-i", str(mermaid_path), "-o", str(svg_path)]
    else:
        npx = shutil.which("npx")
        if not npx:
            print("⚠️ Neither `mmdc` nor `npx` is available on PATH; skipping SVG export.")
            return None
        cmd = [npx, "-y", "@mermaid-js/mermaid-cli", "-i", str(mermaid_path), "-o", str(svg_path)]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return svg_path
    except FileNotFoundError as exc:
        print("⚠️ Mermaid CLI not found:", exc)
        return None
    except subprocess.CalledProcessError as exc:
        print("⚠️ Mermaid export failed:", exc.stderr)
        return None


def _stringify(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def export_graph_data(nodes: Sequence[Mapping], edges: Sequence[Mapping], outdir: Path, prefix: str = "graph"):
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"{prefix}_nodes.json").write_text(json.dumps(nodes, indent=2, default=str), encoding="utf-8")
    (outdir / f"{prefix}_edges.json").write_text(json.dumps(edges, indent=2, default=str), encoding="utf-8")

    if nodes:
        node_fields = sorted({k for node in nodes for k in node.keys()})
        with (outdir / f"{prefix}_nodes.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=node_fields)
            writer.writeheader()
            for node in nodes:
                writer.writerow({k: _stringify(node.get(k, "")) for k in node_fields})
    if edges:
        edge_fields = sorted({k for edge in edges for k in edge.keys()})
        with (outdir / f"{prefix}_edges.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=edge_fields)
            writer.writeheader()
            for edge in edges:
                writer.writerow({k: _stringify(edge.get(k, "")) for k in edge_fields})


def main():
    parser = ArgumentParser(description="Visualize the knowledge graph and validate CAG with a question.")
    parser.add_argument("--question", "-q", default="What is the most important concept in the knowledge graph?")
    parser.add_argument("--outdir", "-o", default="knowledge_graph")
    parser.add_argument("--group", "-g", help="Filter nodes/edges by a specific group_id before rendering.")
    parser.add_argument(
        "--split-by-group",
        action="store_true",
        help="Create subdirectories per group_id with their own Mermaid/CSV/JSON exports.",
    )
    parser.add_argument(
        "--export-data",
        action="store_true",
        help="Export nodes/edges as JSON and CSV for downstream tools (Gephi, Cytoscape, etc.).",
    )
    parser.add_argument(
        "--skip-answer-check",
        action="store_true",
        help="Skip the CAG validation question at the end (useful in non-UTF8 terminals).",
    )
    args = parser.parse_args()

    nodes, edges = fetch_graph()

    outdir = Path(args.outdir)

    def _render_and_export(current_nodes, current_edges, target_dir, prefix="graph"):
        mermaid = build_mermaid(current_nodes, current_edges)
        md_path = write_mermaid_md(mermaid, target_dir)
        mmd_path = write_mermaid_mmd(mermaid, target_dir)
        svg_path = convert_to_svg(mmd_path, target_dir)
        if args.export_data:
            export_graph_data(current_nodes, current_edges, target_dir, prefix=prefix)
        print(f"[ok] Mermaid saved to {md_path}")
        if svg_path:
            print(f"[ok] SVG saved to {svg_path}")
        else:
            print("[info] Install `@mermaid-js/mermaid-cli` (`mmdc`) to auto-export an SVG.")

    if args.split_by_group:
        groups = sorted({n.get("group_id") or "ungrouped" for n in nodes})
        for group in groups:
            subset_nodes, subset_edges = filter_graph(nodes, edges, None if group == "ungrouped" else group)
            if not subset_nodes:
                continue
            group_dir = outdir / "groups" / slugify(group)
            print(f"\n[Group] Rendering '{group}' ({len(subset_nodes)} nodes / {len(subset_edges)} edges)")
            _render_and_export(subset_nodes, subset_edges, group_dir, prefix="graph")
    else:
        filtered_nodes, filtered_edges = filter_graph(nodes, edges, args.group)
        _render_and_export(filtered_nodes, filtered_edges, outdir)

    if not args.skip_answer_check:
        ctx, answer = answer_with_cag(args.question)
        print(f"[info] Retrieved context length: {len(ctx)} chars")
        print(f"[answer] {args.question}\n{answer}")
    else:
        print("[info] Skipped answer validation per flag.")

    return GraphArtifact(mermaid_path=outdir / "graph_inspector.md", nodes=nodes, edges=edges)


if __name__ == "__main__":
    main()
