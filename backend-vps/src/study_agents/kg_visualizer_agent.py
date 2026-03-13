"""
Interactive KG visualizer.

Prompts the user for a search term, filters the Supabase knowledge graph, and
renders the matching subgraph in a matplotlib window for quick inspection.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .graph_inspector import fetch_graph, filter_graph


def _import_graph_libs():
    try:
        import matplotlib.pyplot as plt  # type: ignore
        import networkx as nx  # type: ignore
    except ImportError as exc:  # pragma: no cover - user environment issue
        raise SystemExit(
            "This tool needs `matplotlib` and `networkx`. "
            "Install them with `pip install matplotlib networkx`."
        ) from exc
    return plt, nx


def _normalize(text: str | None) -> str:
    return (text or "").strip().lower()


def search_nodes_by_term(nodes: Sequence[Mapping], term: str) -> list[Mapping]:
    """Return nodes whose id/title/group/attrs contain the search term."""
    term = _normalize(term)
    if not term:
        return list(nodes)

    result = []
    for node in nodes:
        haystack_parts = [
            node.get("id", ""),
            node.get("title", ""),
            node.get("group_id", ""),
            json.dumps(node.get("attrs", {}), ensure_ascii=False),
        ]
        haystack = " ".join(haystack_parts).lower()
        if term in haystack:
            result.append(node)
    return result


def filter_edges_for_nodes(edges: Sequence[Mapping], node_ids: set[str]) -> list[Mapping]:
    return [
        edge
        for edge in edges
        if edge.get("src") in node_ids or edge.get("dst") in node_ids
    ]


def truncate_label(text: str, limit: int = 40) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def plot_graph(term: str, nodes: Sequence[Mapping], edges: Sequence[Mapping], max_nodes: int = 75) -> None:
    plt, nx = _import_graph_libs()

    if not nodes:
        print("⚠️ No nodes matched that search term.")
        return

    if len(nodes) > max_nodes:
        print(f"[info] {len(nodes)} nodes matched; showing only the first {max_nodes}. Refine your term for a smaller slice.")
        nodes = nodes[:max_nodes]
        node_ids = {n.get("id") for n in nodes}
        edges = [e for e in edges if e.get("src") in node_ids and e.get("dst") in node_ids]

    graph = nx.DiGraph()
    for node in nodes:
        node_id = node.get("id") or f"node-{len(graph)}"
        graph.add_node(node_id, label=node.get("title", node_id), group=node.get("group_id"))
    for edge in edges:
        src = edge.get("src")
        dst = edge.get("dst")
        if src in graph.nodes and dst in graph.nodes:
            graph.add_edge(src, dst, label=edge.get("rel", ""))

    if not graph.nodes:
        print("⚠️ No drawable nodes after filtering.")
        return

    fig, ax = plt.subplots(figsize=(12, 8))
    fig.canvas.manager.set_window_title(f"KG slice: '{term or 'all'}'")

    pos = nx.spring_layout(graph, seed=42)
    nx.draw(
        graph,
        pos,
        with_labels=False,
        node_color="#cfd8ff",
        edge_color="#666",
        node_size=700,
        width=1.2,
        arrowsize=12,
        ax=ax,
    )
    labels = {node: truncate_label(data.get("label", node)) for node, data in graph.nodes(data=True)}
    nx.draw_networkx_labels(graph, pos, labels=labels, font_size=8, font_weight="bold", ax=ax)

    edge_labels = { (u, v): truncate_label(data.get("label", "")) for u, v, data in graph.edges(data=True) if data.get("label") }
    if edge_labels:
        nx.draw_networkx_edge_labels(graph, pos, edge_labels=edge_labels, font_size=7, font_color="#333", ax=ax)

    subtitle = f"{len(graph.nodes)} nodes / {len(graph.edges)} edges"
    ax.set_title(f"Knowledge Graph slice for '{term or 'all'}' ({subtitle})")
    ax.axis("off")
    plt.tight_layout()
    print("[info] Close the matplotlib window to search again.")
    plt.show()


def interactive_loop(nodes: Sequence[Mapping], edges: Sequence[Mapping], max_nodes: int = 75) -> None:
    print("Enter a search term to filter the knowledge graph (blank to exit).")
    while True:
        try:
            term = input("\nSearch term (or press Enter to quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[info] Exiting visualizer.")
            return

        if not term:
            print("[info] No term entered. Exiting visualizer.")
            return

        matched_nodes = search_nodes_by_term(nodes, term)
        if not matched_nodes:
            print("⚠️ No nodes matched that term. Try another keyword.")
            continue

        node_ids = {node.get("id") for node in matched_nodes}
        matched_edges = filter_edges_for_nodes(edges, node_ids)
        plot_graph(term, matched_nodes, matched_edges, max_nodes=max_nodes)


def main():
    parser = argparse.ArgumentParser(description="Filter and visualize the knowledge graph interactively.")
    parser.add_argument("--group", help="Pre-filter by a specific group_id before searching.")
    parser.add_argument("--max-nodes", type=int, default=75, help="Maximum nodes to plot at once (default: 75).")
    args = parser.parse_args()

    nodes, edges = fetch_graph()
    if not nodes:
        raise SystemExit("No nodes available in Supabase. Ingest content first.")

    if args.group:
        nodes, edges = filter_graph(nodes, edges, args.group)
        if not nodes:
            raise SystemExit(f"No nodes found for group '{args.group}'.")
        print(f"[info] Loaded group '{args.group}' ({len(nodes)} nodes / {len(edges)} edges).")
    else:
        print(f"[info] Loaded entire graph ({len(nodes)} nodes / {len(edges)} edges).")

    interactive_loop(nodes, edges, max_nodes=args.max_nodes)


if __name__ == "__main__":
    main()
