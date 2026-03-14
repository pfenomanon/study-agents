#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import aiohttp

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore[assignment]

try:
    from dotenv import load_dotenv as _load_dotenv
except ImportError:  # pragma: no cover
    def _load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]

PROFILES_DIR = ROOT / "domain" / "profiles"
RESEARCH_DIR = ROOT / "domain" / "research"
HELPER_FILE = ROOT / "domain" / "profile_name_template_helper.md"
GENERIC_PROFILE = PROFILES_DIR / "generic.json"

REQUIRED_PROFILE_KEYS = {
    "schema_version",
    "profile_name",
    "domain_name",
    "assistant_role",
    "domain_expertise",
    "entity_types",
    "relationship_types",
    "relationship_priorities",
    "topic_priorities",
    "vision_focus_areas",
    "examples",
    "forbidden_terms",
    "allow_legacy_terms",
}


@dataclass
class SourceRecord:
    source_id: str
    url: str
    domain: str
    authority_score: float
    keyword_score: float
    relevance_score: float
    title: str
    snippet: str
    fetched_at: str


class SimpleCrawler:
    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "SimpleCrawler":
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=25),
            headers={"User-Agent": "DomainProfilePipeline/1.0"},
        )
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.session:
            await self.session.close()
        self.session = None

    async def fetch_page(self, url: str) -> str | None:
        if not self.session:
            raise RuntimeError("SimpleCrawler session not initialized.")
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    return None
                try:
                    return await response.text()
                except UnicodeDecodeError:
                    return await response.text(encoding="iso-8859-1")
        except Exception:
            return None


def _load_env_file(path: Path) -> None:
    loaded = _load_dotenv(path)
    if loaded:
        return
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _load_env_sources(explicit_env_file: str | None) -> list[Path]:
    candidates: list[Path] = []
    if explicit_env_file:
        candidates.append(Path(explicit_env_file).expanduser().resolve())
    candidates.extend(
        [
            Path.cwd() / ".env",
            ROOT / ".env",
            ROOT.parent / ".env",
            Path("/home/study-agents/.env"),
        ]
    )
    loaded: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        _load_env_file(resolved)
        loaded.append(resolved)
    return loaded


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower())
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "profile"


def _seed_domain_from_profile_name(profile_name: str) -> str:
    cleaned = re.sub(r"[_\-]+", " ", profile_name).strip()
    return re.sub(r"\s+", " ", cleaned) or "general subject-matter"


def _clean_text(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    text = re.sub(r"(?i)\bexam helper\b", "subject-matter-expert assistant", text)
    return text


def _normalize_string_list(value: Any, fallback: list[str]) -> list[str]:
    if isinstance(value, list):
        items = [str(v).strip() for v in value]
    elif isinstance(value, str):
        items = [p.strip() for p in value.split(",")]
    else:
        items = []
    cleaned = [_clean_text(v) for v in items if str(v).strip()]
    return cleaned or list(fallback)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected object JSON: {path}")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _authority_score(url: str) -> float:
    host = (urlparse(url).netloc or "").lower()
    if host.endswith(".gov"):
        return 1.0
    if host.endswith(".edu"):
        return 0.95
    if host.endswith(".org"):
        return 0.8
    official_markers = (
        "state.tx.us",
        "texas.gov",
        "naic.org",
        "iii.org",
        "nist.gov",
        "iso.org",
        "fema.gov",
        "github.com",
        "docs.",
    )
    if any(marker in host for marker in official_markers):
        return 0.85
    if host.endswith(".com"):
        return 0.6
    return 0.5


def _keyword_score(text: str, keywords: list[str]) -> float:
    if not keywords:
        return 0.0
    lower = text.lower()
    hits = 0
    for key in keywords:
        key_clean = key.strip().lower()
        if key_clean and key_clean in lower:
            hits += 1
    return min(1.0, hits / max(1, len(keywords)))


def _extract_title_and_snippet(html: str) -> tuple[str, str]:
    title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, flags=re.IGNORECASE)
    title = _clean_text(title_match.group(1)) if title_match else "Untitled"
    body = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    body = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", body)
    body = re.sub(r"(?is)<[^>]+>", " ", body)
    snippet = _clean_text(body)
    return title, snippet[:4000]


def _extract_search_urls(html: str, max_results: int, search_domain: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    if BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(html, "html.parser")
            for anchor in soup.find_all("a", href=True):
                href = str(anchor.get("href") or "").strip()
                if not href:
                    continue
                normalized = href
                lower = normalized.lower()
                if lower.startswith(("javascript:", "mailto:", "tel:")) or normalized.startswith("#"):
                    continue
                if normalized.startswith("/l/?") or normalized.startswith("/d.js?"):
                    params = parse_qs(urlparse(normalized).query)
                    target = params.get("uddg") or params.get("u")
                    if target:
                        normalized = unquote(target[0])
                if normalized.startswith("/"):
                    normalized = urljoin(f"https://{search_domain}", normalized)
                elif normalized.startswith("//"):
                    normalized = f"https:{normalized}"
                if not normalized.startswith("http"):
                    continue
                domain = (urlparse(normalized).netloc or "").lower()
                if search_domain in domain:
                    continue
                if normalized not in seen:
                    seen.add(normalized)
                    urls.append(normalized)
                    if len(urls) >= max_results:
                        return urls
        except Exception:
            pass

    patterns = [
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"',
        r'<a[^>]+href="([^"]+)"',
    ]
    for pattern in patterns:
        for match in re.findall(pattern, html, flags=re.IGNORECASE):
            normalized = match
            if normalized.startswith("/"):
                normalized = urljoin(f"https://{search_domain}", normalized)
            if normalized.startswith("//"):
                normalized = f"https:{normalized}"
            if not normalized.startswith("http"):
                continue
            domain = (urlparse(normalized).netloc or "").lower()
            if search_domain in domain:
                continue
            if normalized not in seen:
                seen.add(normalized)
                urls.append(normalized)
                if len(urls) >= max_results:
                    return urls
    return urls


async def _search_web(query: str, max_results: int) -> list[str]:
    encoded = quote_plus(query)
    urls: list[str] = []
    async with SimpleCrawler() as crawler:
        brave = await crawler.fetch_page(f"https://search.brave.com/search?q={encoded}")
        if brave:
            urls = _extract_search_urls(brave, max_results=max_results, search_domain="brave.com")
        if not urls:
            ddg = await crawler.fetch_page(f"https://html.duckduckgo.com/html/?q={encoded}")
            if ddg:
                urls = _extract_search_urls(ddg, max_results=max_results, search_domain="duckduckgo.com")
    return urls


def _merge_profile(base_profile: dict[str, Any], candidate: dict[str, Any], profile_name: str) -> dict[str, Any]:
    merged = dict(base_profile)
    merged.update(candidate)

    list_keys = (
        "domain_expertise",
        "entity_types",
        "relationship_types",
        "relationship_priorities",
        "topic_priorities",
        "vision_focus_areas",
        "examples",
        "forbidden_terms",
    )
    for key in list_keys:
        merged[key] = _normalize_string_list(merged.get(key), list(base_profile.get(key, [])))

    for key in ("domain_name", "assistant_role"):
        merged[key] = _clean_text(str(merged.get(key, "") or base_profile.get(key, "")))
    merged["profile_name"] = _slugify(profile_name)
    merged["schema_version"] = int(merged.get("schema_version", 1) or 1)
    allow_legacy = merged.get("allow_legacy_terms", False)
    if isinstance(allow_legacy, str):
        allow_legacy = allow_legacy.strip().lower() in {"1", "true", "yes", "y"}
    merged["allow_legacy_terms"] = bool(allow_legacy)
    domain_lower = str(merged.get("domain_name", "")).lower()
    if "texas" in domain_lower and ("adjuster" in domain_lower or "insurance" in domain_lower):
        merged["allow_legacy_terms"] = True
    return merged


def _validate_profile(profile: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = REQUIRED_PROFILE_KEYS - set(profile.keys())
    if missing:
        errors.append("Missing keys: " + ", ".join(sorted(missing)))

    for key in ("profile_name", "domain_name", "assistant_role"):
        value = str(profile.get(key, "")).strip()
        if not value:
            errors.append(f"`{key}` must be a non-empty string.")
    if "exam helper" in json.dumps(profile).lower():
        errors.append("Profile must not contain phrase `exam helper`.")

    list_keys = (
        "domain_expertise",
        "entity_types",
        "relationship_types",
        "relationship_priorities",
        "topic_priorities",
        "vision_focus_areas",
        "examples",
        "forbidden_terms",
    )
    for key in list_keys:
        value = profile.get(key)
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            errors.append(f"`{key}` must be a list[str].")
        elif key != "forbidden_terms" and len(value) == 0:
            errors.append(f"`{key}` must not be empty.")

    if not isinstance(profile.get("schema_version"), int):
        errors.append("`schema_version` must be an integer.")
    if not isinstance(profile.get("allow_legacy_terms"), bool):
        errors.append("`allow_legacy_terms` must be boolean.")

    for key in ("entity_types", "relationship_types"):
        items = [str(v).strip().lower() for v in profile.get(key, []) if str(v).strip()]
        if len(items) != len(set(items)):
            errors.append(f"`{key}` contains duplicates.")

    return errors


def _deterministic_profile(base_profile: dict[str, Any], profile_name: str, domain: str) -> dict[str, Any]:
    profile = dict(base_profile)
    profile["profile_name"] = _slugify(profile_name)
    profile["domain_name"] = domain
    profile["assistant_role"] = f"subject-matter-expert assistant for {domain}"
    profile["domain_expertise"] = [
        f"terminology normalization and concept boundaries in {domain}",
        f"evidence-grounded reasoning from retrieved context for {domain}",
        f"workflow and dependency analysis in {domain}",
        f"risk, exception, limitation, and compliance identification in {domain}",
    ]
    profile["topic_priorities"] = [
        f"core definitions and terminology for {domain}",
        f"requirements and constraints in {domain}",
        f"roles, responsibilities, and decision handoffs in {domain}",
        f"timelines, dependencies, and operational sequencing in {domain}",
        f"risks, exceptions, limitations, and controls in {domain}",
    ]
    profile["vision_focus_areas"] = [
        "correctness to provided context",
        "clear next-step guidance",
        "explicit assumptions and limitations",
    ]
    profile["examples"] = [
        f"Primary process definition in {domain}",
        f"Eligibility or precondition in {domain}",
        f"Explicit limitation or exception in {domain}",
        f"Responsible role and required action in {domain}",
        f"Critical timeline milestone in {domain}",
    ]

    domain_lower = domain.lower()
    if "adjuster" in domain_lower and "auto" in domain_lower:
        profile["assistant_role"] = (
            "subject-matter-expert assistant for Texas auto independent adjusting workflows"
        )
        profile["domain_expertise"] = [
            "Texas auto claim coverage interpretation and policy condition analysis",
            "independent adjuster workflows for intake, inspection, estimate, and settlement support",
            "liability, damages, comparative responsibility, and claim decision rationale",
            "regulatory and carrier workflow obligations, timelines, and documentation quality",
        ]
        profile["entity_types"] = [
            "Document",
            "Section",
            "Requirement",
            "RegulatoryBody",
            "Process",
            "Term",
            "CoverageForm",
            "PolicyFeature",
            "Party",
            "Vehicle",
            "DamageType",
            "EstimateLineItem",
            "Obligation",
            "Risk",
            "Timeline",
            "ClaimEvent",
            "EvidenceItem",
        ]
        profile["relationship_types"] = [
            "governs",
            "requires",
            "defines",
            "part_of",
            "references",
            "contradicts",
            "depends_on",
            "supports",
            "excludes",
        ]
        profile["topic_priorities"] = [
            "coverage triggers, limits, exclusions, and conditions for auto claims",
            "liability determination factors and comparative responsibility",
            "inspection, estimate, repair, and settlement workflow requirements",
            "documentation completeness, evidence quality, and discrepancy handling",
            "regulatory, carrier, and deadline compliance for claim handling",
        ]
        profile["vision_focus_areas"] = [
            "grounded interpretation of policy/claim artifacts in screenshots",
            "clear adjuster next-step recommendations and required documentation",
            "explicit uncertainty when evidence is incomplete or conflicting",
        ]
        profile["examples"] = [
            "Coverage determination for collision vs comprehensive",
            "Liability assessment with conflicting witness statements",
            "Estimate line-item validation against documented damage",
            "Documentation checklist for settlement recommendation",
            "Timeline checkpoint for acknowledgment, inspection, and payment communication",
        ]
        profile["forbidden_terms"] = []
        profile["allow_legacy_terms"] = True

    return _merge_profile(base_profile, profile, profile_name)


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Expected JSON object.")
    return parsed


def _build_openai_client() -> Any:
    if OpenAI is None:
        raise RuntimeError("openai package is not installed.")
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    return OpenAI(api_key=api_key)


def _summarize_sources(sources: list[SourceRecord], max_sources: int = 12) -> list[dict[str, Any]]:
    ranked = sorted(
        sources,
        key=lambda s: (s.relevance_score, s.authority_score, s.keyword_score),
        reverse=True,
    )
    output: list[dict[str, Any]] = []
    for src in ranked[:max_sources]:
        output.append(
            {
                "source_id": src.source_id,
                "url": src.url,
                "domain": src.domain,
                "authority_score": round(src.authority_score, 3),
                "relevance_score": round(src.relevance_score, 3),
                "title": src.title,
                "snippet": src.snippet[:800],
            }
        )
    return output


def _generate_profile_with_openai(
    *,
    model: str,
    profile_name: str,
    domain: str,
    base_profile: dict[str, Any],
    helper_text: str,
    source_summaries: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    client = _build_openai_client()
    system_msg = (
        "You are a senior domain modeling architect. "
        "Create a profile JSON for retrieval, graph extraction, and grounded QA. "
        "Return strict JSON only."
    )
    user_msg = (
        "Build a domain profile JSON from research evidence.\n"
        "Output exactly this JSON shape:\n"
        "{\n"
        '  "profile": { ... required schema ... },\n'
        '  "field_evidence": {\n'
        '    "domain_expertise": ["S1"],\n'
        '    "entity_types": ["S1"],\n'
        '    "relationship_types": ["S1"],\n'
        '    "relationship_priorities": ["S1"],\n'
        '    "topic_priorities": ["S1"],\n'
        '    "vision_focus_areas": ["S1"],\n'
        '    "examples": ["S1"]\n'
        "  }\n"
        "}\n\n"
        f"profile_name: {profile_name}\n"
        f"domain: {domain}\n\n"
        "Profile helper guidance:\n"
        f"{helper_text}\n\n"
        "Baseline generic profile:\n"
        f"{json.dumps(base_profile, indent=2)}\n\n"
        "Research evidence summary:\n"
        f"{json.dumps(source_summaries, indent=2)}\n\n"
        "Rules:\n"
        "- Keep required keys complete.\n"
        "- Use domain-specific values supported by evidence.\n"
        "- Do not output markdown.\n"
        "- Do not use phrase 'exam helper'.\n"
    )
    completion = client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
    )
    content = completion.choices[0].message.content or "{}"
    payload = _extract_json_object(content)
    profile_raw = payload.get("profile")
    if not isinstance(profile_raw, dict):
        raise RuntimeError("AI output missing `profile` object.")
    field_evidence = payload.get("field_evidence")
    if not isinstance(field_evidence, dict):
        field_evidence = {}
    merged = _merge_profile(base_profile, profile_raw, profile_name)
    return merged, field_evidence


def _source_metrics(sources: list[SourceRecord]) -> dict[str, Any]:
    domains = sorted({src.domain for src in sources})
    authoritative = [src for src in sources if src.authority_score >= 0.8]
    return {
        "source_count": len(sources),
        "authoritative_count": len(authoritative),
        "unique_domain_count": len(domains),
        "domains": domains,
        "avg_relevance": round(
            sum(src.relevance_score for src in sources) / len(sources), 3
        )
        if sources
        else 0.0,
    }


def _validate_field_evidence(
    field_evidence: dict[str, Any], valid_source_ids: set[str]
) -> list[str]:
    required_fields = {
        "domain_expertise",
        "entity_types",
        "relationship_types",
        "relationship_priorities",
        "topic_priorities",
        "vision_focus_areas",
        "examples",
    }
    errors: list[str] = []
    for field in sorted(required_fields):
        refs = field_evidence.get(field)
        if not isinstance(refs, list) or not refs:
            errors.append(f"missing evidence refs for `{field}`")
            continue
        normalized = [str(ref).strip() for ref in refs if str(ref).strip()]
        if not normalized:
            errors.append(f"empty evidence refs for `{field}`")
            continue
        invalid = [ref for ref in normalized if ref not in valid_source_ids]
        if invalid:
            errors.append(
                f"invalid evidence refs for `{field}`: {', '.join(invalid)}"
            )
    return errors


def _research_ready(
    metrics: dict[str, Any],
    *,
    min_sources: int,
    min_authoritative: int,
    min_domains: int,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if metrics["source_count"] < min_sources:
        reasons.append(
            f"source_count {metrics['source_count']} < required {min_sources}"
        )
    if metrics["authoritative_count"] < min_authoritative:
        reasons.append(
            f"authoritative_count {metrics['authoritative_count']} < required {min_authoritative}"
        )
    if metrics["unique_domain_count"] < min_domains:
        reasons.append(
            f"unique_domain_count {metrics['unique_domain_count']} < required {min_domains}"
        )
    return len(reasons) == 0, reasons


async def _research_pass(
    *,
    query: str,
    seed_urls: list[str],
    max_results: int,
    fetch_limit: int,
) -> list[SourceRecord]:
    urls = []
    seen: set[str] = set()
    for url in seed_urls:
        if url.startswith("http") and url not in seen:
            seen.add(url)
            urls.append(url)
    discovered = await _search_web(query, max_results=max_results)
    for url in discovered:
        if url.startswith("http") and url not in seen:
            seen.add(url)
            urls.append(url)

    sources: list[SourceRecord] = []
    query_terms = [t for t in re.split(r"[^a-zA-Z0-9]+", query.lower()) if len(t) > 2]
    async with SimpleCrawler() as crawler:
        source_num = 1
        for url in urls[: max_results * 2]:
            html = await crawler.fetch_page(url)
            if not html:
                continue
            title, snippet = _extract_title_and_snippet(html)
            if len(snippet) < 300:
                continue
            authority = _authority_score(url)
            keyword = _keyword_score(f"{title}\n{snippet}", query_terms)
            relevance = round((authority * 0.45) + (keyword * 0.55), 3)
            domain = (urlparse(url).netloc or "").lower()
            sources.append(
                SourceRecord(
                    source_id=f"S{source_num}",
                    url=url,
                    domain=domain,
                    authority_score=authority,
                    keyword_score=keyword,
                    relevance_score=relevance,
                    title=title,
                    snippet=snippet[:fetch_limit],
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            source_num += 1
    ranked = sorted(
        sources,
        key=lambda s: (s.relevance_score, s.authority_score, s.keyword_score),
        reverse=True,
    )
    return ranked[:max_results]


def _default_seed_urls(domain: str) -> list[str]:
    domain_lower = domain.lower()
    if "texas" in domain_lower and "auto" in domain_lower and "adjuster" in domain_lower:
        return [
            "https://www.tdi.texas.gov/",
            "https://www.tdi.texas.gov/pubs/consumer/cb020.html",
            "https://www.tdi.texas.gov/agent/general/index.html",
            "https://www.tdi.texas.gov/rules/index.html",
            "https://statutes.capitol.texas.gov/",
            "https://www.iii.org/",
            "https://www.naic.org/",
        ]
    return [
        "https://www.nist.gov/",
        "https://www.iso.org/",
        "https://www.govinfo.gov/",
        "https://en.wikipedia.org/wiki/Main_Page",
    ]


def _build_followup_query(base_domain: str, failed_reasons: list[str], pass_num: int) -> str:
    hint = "official standards regulations glossary workflow"
    if any("authoritative_count" in reason for reason in failed_reasons):
        hint = "official guidance statute standards regulator documentation"
    if any("unique_domain_count" in reason for reason in failed_reasons):
        hint += " comparison references"
    return f"{base_domain} {hint} pass {pass_num}"


def _run_domain_wizard_check(profile_name: str) -> tuple[int, str]:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "domain_wizard.py"),
        "--profile-name",
        profile_name,
        "--check",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output.strip()


async def run_pipeline(args: argparse.Namespace) -> int:
    loaded_envs = _load_env_sources(args.env_file)
    if loaded_envs:
        print("Loaded env files: " + ", ".join(str(path) for path in loaded_envs))

    profile_name = _slugify(args.profile_name)
    domain = _clean_text(args.domain or _seed_domain_from_profile_name(profile_name))
    profile_dir = RESEARCH_DIR / profile_name
    profile_dir.mkdir(parents=True, exist_ok=True)
    dossier_path = profile_dir / "dossier.json"
    report_path = profile_dir / "profile_generation_report.md"
    validation_path = profile_dir / "validation_report.json"
    profile_path = PROFILES_DIR / f"{profile_name}.json"

    helper_text = HELPER_FILE.read_text(encoding="utf-8")
    base_profile = _read_json(GENERIC_PROFILE)

    seed_urls = _default_seed_urls(domain) + list(args.seed_urls or [])
    all_sources: list[SourceRecord] = []
    query_runs: list[dict[str, Any]] = []
    query = args.research_query or f"{domain} standards regulations workflows terminology"

    for pass_num in range(1, args.max_research_passes + 1):
        print(f"[research] pass={pass_num} query={query}")
        pass_sources = await _research_pass(
            query=query,
            seed_urls=seed_urls,
            max_results=args.max_sources,
            fetch_limit=args.max_snippet_chars,
        )
        # Deduplicate by URL while preserving best relevance.
        by_url: dict[str, SourceRecord] = {src.url: src for src in all_sources}
        for src in pass_sources:
            existing = by_url.get(src.url)
            if existing is None or src.relevance_score > existing.relevance_score:
                by_url[src.url] = src
        all_sources = sorted(
            by_url.values(),
            key=lambda s: (s.relevance_score, s.authority_score, s.keyword_score),
            reverse=True,
        )[: args.max_sources * 3]

        metrics = _source_metrics(all_sources)
        ready, reasons = _research_ready(
            metrics,
            min_sources=args.min_sources,
            min_authoritative=args.min_authoritative_sources,
            min_domains=args.min_unique_domains,
        )
        query_runs.append(
            {
                "pass": pass_num,
                "query": query,
                "pass_source_count": len(pass_sources),
                "aggregate_metrics": metrics,
                "ready": ready,
                "gaps": reasons,
            }
        )
        print(
            "[research] metrics "
            f"sources={metrics['source_count']} authoritative={metrics['authoritative_count']} "
            f"domains={metrics['unique_domain_count']} ready={ready}"
        )
        if ready:
            break
        query = _build_followup_query(domain, reasons, pass_num + 1)

    summary_sources = _summarize_sources(all_sources, max_sources=args.max_sources)
    metrics = _source_metrics(all_sources)
    ready, reasons = _research_ready(
        metrics,
        min_sources=args.min_sources,
        min_authoritative=args.min_authoritative_sources,
        min_domains=args.min_unique_domains,
    )

    field_evidence: dict[str, Any] = {}
    evidence_errors: list[str] = []
    used_ai = False
    ai_error = ""
    profile = _deterministic_profile(base_profile, profile_name, domain)
    if args.use_ai:
        try:
            profile, field_evidence = _generate_profile_with_openai(
                model=args.model,
                profile_name=profile_name,
                domain=domain,
                base_profile=profile,
                helper_text=helper_text,
                source_summaries=summary_sources,
            )
            used_ai = True
        except Exception as exc:  # noqa: BLE001
            ai_error = str(exc)
            if args.no_ai_fallback:
                print(f"[ai error] {ai_error}", file=sys.stderr)
                return 1
            print(f"[ai warn] {ai_error}. Falling back to deterministic profile.")

    if used_ai:
        valid_source_ids = {src["source_id"] for src in summary_sources}
        evidence_errors = _validate_field_evidence(field_evidence, valid_source_ids)
        if evidence_errors:
            msg = "; ".join(evidence_errors)
            if args.no_ai_fallback:
                print(f"[ai error] field evidence validation failed: {msg}", file=sys.stderr)
                return 1
            print(
                f"[ai warn] field evidence validation failed: {msg}. "
                "Falling back to deterministic profile."
            )
            used_ai = False
            field_evidence = {}
            profile = _deterministic_profile(base_profile, profile_name, domain)

    profile_errors = _validate_profile(profile)
    if profile_errors:
        print("[profile error] invalid profile generated:", file=sys.stderr)
        for err in profile_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    _write_json(profile_path, profile)
    print(f"[profile] wrote {profile_path}")

    wizard_code, wizard_output = _run_domain_wizard_check(profile_name)
    wizard_ok = wizard_code == 0

    dossier = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile_name": profile_name,
        "domain": domain,
        "query_runs": query_runs,
        "research_ready": ready,
        "research_gaps": reasons,
        "metrics": metrics,
        "sources": summary_sources,
        "helper_file": str(HELPER_FILE),
    }
    _write_json(dossier_path, dossier)

    validation = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile_path": str(profile_path),
        "used_ai": used_ai,
        "ai_error": ai_error,
        "profile_validation_errors": profile_errors,
        "wizard_check_ok": wizard_ok,
        "wizard_check_output": wizard_output,
        "field_evidence": field_evidence,
        "field_evidence_errors": evidence_errors,
    }
    _write_json(validation_path, validation)

    report = [
        f"# Domain Profile Pipeline Report: {profile_name}",
        "",
        f"- Generated at: {datetime.now(timezone.utc).isoformat()}",
        f"- Domain: {domain}",
        f"- Used AI profile generation: {used_ai}",
        f"- Research ready: {ready}",
        f"- Research gaps: {', '.join(reasons) if reasons else 'none'}",
        f"- Wizard check: {'PASS' if wizard_ok else 'FAIL'}",
        "",
        "## Metrics",
        f"- Source count: {metrics['source_count']}",
        f"- Authoritative source count: {metrics['authoritative_count']}",
        f"- Unique domain count: {metrics['unique_domain_count']}",
        f"- Avg relevance: {metrics['avg_relevance']}",
        "",
        "## Key Artifacts",
        f"- Profile JSON: `{profile_path}`",
        f"- Dossier JSON: `{dossier_path}`",
        f"- Validation JSON: `{validation_path}`",
        "",
        "## Top Sources",
    ]
    for src in summary_sources[:10]:
        report.append(
            f"- {src['source_id']}: {src['title']} ({src['url']}) "
            f"[authority={src['authority_score']}, relevance={src['relevance_score']}]"
        )
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"[report] wrote {report_path}")

    if args.generate_prompts:
        wizard_cmd = [
            sys.executable,
            str(ROOT / "scripts" / "domain_wizard.py"),
            "--profile-name",
            profile_name,
            "--apply",
            "--check",
        ]
        if args.use_ai:
            wizard_cmd.extend(
                [
                    "--use-ai",
                    "--platform",
                    args.platform,
                    "--model",
                    args.model,
                ]
            )
        proc = subprocess.run(
            wizard_cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        print("[wizard] apply/check exit_code=", proc.returncode)
        if proc.stdout:
            print(proc.stdout.strip())
        if proc.stderr:
            print(proc.stderr.strip(), file=sys.stderr)
        if proc.returncode != 0:
            return 1

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Research-driven domain profile pipeline using helper-guided JSON generation."
    )
    parser.add_argument("--profile-name", required=True, help="Profile name slug.")
    parser.add_argument("--domain", default=None, help="Domain phrase.")
    parser.add_argument("--research-query", default=None, help="Optional explicit research query.")
    parser.add_argument(
        "--seed-url",
        dest="seed_urls",
        action="append",
        default=[],
        help="Optional seed URL (repeatable).",
    )
    parser.add_argument("--max-research-passes", type=int, default=2)
    parser.add_argument("--max-sources", type=int, default=12)
    parser.add_argument("--max-snippet-chars", type=int, default=2000)
    parser.add_argument("--min-sources", type=int, default=6)
    parser.add_argument("--min-authoritative-sources", type=int, default=3)
    parser.add_argument("--min-unique-domains", type=int, default=4)
    parser.add_argument("--use-ai", action="store_true")
    parser.add_argument("--platform", choices=["openai"], default="openai")
    parser.add_argument("--model", default="gpt-5.2")
    parser.add_argument("--no-ai-fallback", action="store_true")
    parser.add_argument("--env-file", default=None)
    parser.add_argument(
        "--generate-prompts",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run domain_wizard apply/check after profile generation (default: true).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return asyncio.run(run_pipeline(args))


if __name__ == "__main__":
    raise SystemExit(main())
