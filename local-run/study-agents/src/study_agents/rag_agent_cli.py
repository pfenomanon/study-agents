"""CLI entrypoint for reasoning-driven RAG bundle generation."""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import urlparse
from urllib.request import urlretrieve

from dataclasses import asdict

from .kg_pipeline import KnowledgeIngestionService, episode_from_rag_artifacts
from .rag_builder_core import ensure_dir
from .rag_reasoning import RAGBuildAgent, RAGReasoningPlanner

REMOTE_SCHEMES = {"http", "https"}


def _collect_inputs(tokens: Iterable[str]) -> tuple[list[Path], Optional[Path]]:
    downloaded_dir: Optional[Path] = None
    collected: list[Path] = []

    for token in tokens:
        parsed = urlparse(token)
        if parsed.scheme in REMOTE_SCHEMES:
            if downloaded_dir is None:
                downloaded_dir = Path(tempfile.mkdtemp(prefix="rag_agent_"))
            filename = Path(parsed.path).name or "downloaded.pdf"
            target = downloaded_dir / filename
            urlretrieve(token, target)
            collected.append(target)
            continue

        if any(ch in token for ch in "*?[]"):
            matches = [Path(p) for p in glob.glob(token) if Path(p).is_file()]
            collected.extend(matches)
            continue

        path = Path(token)
        if path.is_dir():
            collected.extend(sorted(path.glob("*.pdf")))
        else:
            collected.append(path)

    return collected, downloaded_dir


def _validate_inputs(paths: list[Path]) -> list[Path]:
    valid: list[Path] = []
    for path in paths:
        if not path.exists():
            print(f"⚠️  Skipping missing input: {path}", file=sys.stderr)
            continue
        if path.suffix.lower() != ".pdf":
            print(f"⚠️  Skipping non-PDF input: {path}", file=sys.stderr)
            continue
        valid.append(path.resolve())
    return valid


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reasoning-driven PDF → RAG bundle builder",
    )
    parser.add_argument("inputs", nargs="+", help="PDF paths, directories, globs, or HTTP URLs")
    parser.add_argument("--outdir", default="out", help="Output directory root (default: out)")
    parser.add_argument("--chunk-size", type=int, default=None)
    parser.add_argument("--overlap", type=int, default=None)
    parser.add_argument("--max-sections", type=int, default=None)
    parser.add_argument("--triples", type=int, default=None)
    parser.add_argument(
        "--provider",
        choices=["auto", "openai", "ollama"],
        default="auto",
        help="Reasoning model provider preference",
    )
    parser.add_argument("--reason-model", default=None, help="Override reasoning model name")
    parser.add_argument("--push", action="store_true", help="Push artifacts into Supabase")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary (stdout)")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    paths, tmp_dir = _collect_inputs(args.inputs)
    inputs = _validate_inputs(paths)
    if not inputs:
        print("No valid PDF inputs provided.", file=sys.stderr)
        return 1

    outdir = ensure_dir(Path(args.outdir).expanduser().resolve())
    overrides = {
        key: getattr(args, key.replace("-", "_"))
        for key in ("chunk-size", "overlap", "max-sections", "triples")
    }
    overrides = {k.replace("-", "_"): v for k, v in overrides.items() if v is not None}

    planner = RAGReasoningPlanner(provider=args.provider, default_model=args.reason_model)
    agent = RAGBuildAgent(planner=planner)
    ingestion_service = KnowledgeIngestionService() if args.push else None

    results = []
    try:
        for pdf_path in inputs:
            try:
                build = agent.build_bundle(pdf_path=pdf_path, outdir=outdir, overrides=overrides)
                ingest_result = None
                if ingestion_service is not None:
                    payload = episode_from_rag_artifacts(pdf_path, build.artifacts)
                    ingest_result = ingestion_service.ingest_episode(payload)
                result_payload = {
                    "input": str(pdf_path),
                    "artifacts": build.artifacts,
                    "plan": build.plan.__dict__,
                    "ingested": asdict(ingest_result) if ingest_result else None,
                }
                results.append(result_payload)
                if not args.json:
                    print(f"✅ Built bundle for {pdf_path} -> {build.artifacts['folder']}")
                    if ingest_result:
                        print(
                            f"   Ingested: documents={ingest_result.documents_written} "
                            f"nodes={ingest_result.nodes_written} edges={ingest_result.edges_written}"
                        )
            except Exception as exc:  # pragma: no cover - runtime safeguard
                print(f"❌ Failed for {pdf_path}: {exc}", file=sys.stderr)
    finally:
        if tmp_dir and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if args.json:
        print(json.dumps(results, indent=2))

    failed = len(inputs) - len(results)
    return 0 if failed == 0 else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
