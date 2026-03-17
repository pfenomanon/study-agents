#!/usr/bin/env python3
"""
Web Research Agent

An intelligent web research agent that uses reasoning models to traverse the internet,
discover relevant content, and prepare markdown files for RAG/CAG systems.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Set
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import aiohttp
try:
    from docling.document_converter import DocumentConverter
except ImportError:  # pragma: no cover - optional dependency
    DocumentConverter = None
try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - optional dependency
    BeautifulSoup = None
try:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, LLMConfig
    try:
        from crawl4ai import DefaultMarkdownGenerator
    except ImportError:  # pragma: no cover - API location differs by version
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    from crawl4ai.content_filter_strategy import LLMContentFilter
except ImportError:  # pragma: no cover - optional dependency
    AsyncWebCrawler = None
    BrowserConfig = None
    CrawlerRunConfig = None
    LLMConfig = None
    DefaultMarkdownGenerator = None
    LLMContentFilter = None

from .config import OLLAMA_API_KEY, OLLAMA_HOST, REASON_MODEL
from .kg_pipeline import EpisodeChunk, EpisodePayload, KnowledgeIngestionService
from .ollama_client import chat as ollama_chat
from .rag_builder_core import chunk_text, slugify, split_into_paragraphs
from .settings import SettingsError, get_settings

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
DEFAULT_WEB_PROMPT = PROMPTS_DIR / "web_research_system_prompt.md"

logger = logging.getLogger(__name__)

_settings = get_settings()
_OLLAMA_AVAILABLE = bool(os.getenv("OLLAMA_API_KEY") and os.getenv("OLLAMA_HOST"))
if not _OLLAMA_AVAILABLE:
    logger.warning("OLLAMA_HOST/OLLAMA_API_KEY not set; falling back to heuristic scoring/link extraction.")
_CRAWL4AI_AVAILABLE = all(
    obj is not None
    for obj in (
        AsyncWebCrawler,
        BrowserConfig,
        CrawlerRunConfig,
        LLMConfig,
        DefaultMarkdownGenerator,
        LLMContentFilter,
    )
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_optional_bool(
    true_flag: bool,
    false_flag: bool,
    env_name: str,
    *,
    default: bool,
) -> bool:
    if true_flag and false_flag:
        raise ValueError(f"Conflicting flags set for {env_name}")
    if true_flag:
        return True
    if false_flag:
        return False
    return _env_bool(env_name, default=default)


class SystemPrompt:
    """System prompt manager for web research guidance."""
    
    def __init__(self, prompt_file: Path = DEFAULT_WEB_PROMPT):
        self.prompt_file = prompt_file
        self._prompt: Optional[str] = None
        self._load_prompt()
    
    def _load_prompt(self) -> None:
        """Load system prompt from file."""
        if self.prompt_file.exists():
            self._prompt = self.prompt_file.read_text(encoding="utf-8")
        else:
            logger.warning(
                "System prompt file '%s' not found. Using built-in fallback instructions.",
                self.prompt_file,
            )
            self._prompt = """
You are a web research agent focused on fact-based research only.
Prioritize authoritative sources, verify information, and avoid speculation.
Extract comprehensive, accurate information suitable for research purposes.
"""


    
    @property
    def prompt(self) -> str:
        """Get the system prompt."""
        return self._prompt
    
    def get_relevance_evaluation_prompt(self, query: str, url: str, content: str) -> str:
        """Get prompt for relevance evaluation with system guidance."""
        return f"""
{self.prompt}

Based on the above guidelines, evaluate the relevance of this web content for the research query.

Query: {query}
URL: {url}

Content preview (first 1000 chars):
{content[:1000]}

Rate relevance from 0.0 to 1.0 where:
- 0.0 = Not relevant at all
- 0.3 = Minimally relevant (some connection but low value)
- 0.5 = Somewhat relevant (useful but not directly addressing query)
- 0.7 = Highly relevant (directly addresses query with good information)
- 1.0 = Extremely relevant (perfect match, authoritative source, comprehensive coverage)

Consider:
- Direct relevance to the query
- Quality and reliability of the source
- Depth of factual information
- Uniqueness of the content
- Authority and credibility of the source

Respond with just the numeric score (0.0-1.0):
"""
    
    def get_link_extraction_prompt(self, content: str, base_url: str) -> str:
        """Get prompt for link extraction with system guidance."""
        return f"""
{self.prompt}

Based on the above guidelines, extract relevant links from this HTML content for further research.

Base URL: {base_url}

Content (first 2000 chars):
{content[:2000]}

Extract links that are:
1. Relevant to research and fact-based content
2. Likely to contain valuable, authoritative information
3. From credible sources (academic, official, research institutions)
4. Not primarily marketing, opinion, or speculation

Return links as a JSON list of URLs only:
["url1", "url2", ...]
"""


@dataclass
class ResearchResult:
    """Container for research results."""
    url: str
    title: str
    content: str
    markdown_path: Path
    relevance_score: float


@dataclass
class SearchQuery:
    """Search query with context."""
    term: str
    context: Optional[str] = None
    depth: int = 1


class WebCrawler:
    """Web crawler with robots.txt compliance."""
    
    def __init__(self, max_retries: int = 3, retry_backoff: float = 0.75):
        self.session: Optional[aiohttp.ClientSession] = None
        self.robots_cache: dict[str, RobotFileParser] = {}
        self.max_retries = max(1, max_retries)
        self.retry_backoff = max(retry_backoff, 0.0)

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={'User-Agent': 'Mozilla/5.0 (compatible; WebResearchAgent/1.0)'}
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
        self.session = None

    async def can_fetch(self, url: str, user_agent: str = '*') -> bool:
        """Check if URL can be fetched according to robots.txt."""
        if not self.session:
            raise RuntimeError("WebCrawler session is not initialized. Use 'async with WebCrawler()'.")

        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        if robots_url not in self.robots_cache:
            rp = RobotFileParser()
            try:
                async with self.session.get(robots_url) as response:
                    if response.status == 200:
                        try:
                            text = await response.text()
                        except UnicodeDecodeError:
                            text = await response.text(encoding="iso-8859-1")
                        rp.parse(text.splitlines())
                    else:
                        rp.allow_all = True
            except Exception:
                rp.allow_all = True

            self.robots_cache[robots_url] = rp

        return self.robots_cache[robots_url].can_fetch(user_agent, url)

    async def fetch_page(self, url: str) -> Optional[str]:
        """Fetch page content with retry support."""
        if not await self.can_fetch(url):
            print(f"[warn] Robots.txt disallows: {url}")
            return None

        if not self.session:
            raise RuntimeError("WebCrawler session is not initialized. Use 'async with WebCrawler()'.")

        delay = self.retry_backoff
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                async with self.session.get(url) as response:
                    if response.status == 200:
                        raw_content_type = response.headers.get("Content-Type", "")
                        try:
                            text = await response.text()
                        except UnicodeDecodeError:
                            text = await response.text(encoding="iso-8859-1")

                        if not self._is_likely_html_response(raw_content_type, text):
                            print(
                                f"[skip] Non-HTML response ({raw_content_type or 'unknown'}): {url}"
                            )
                            return None
                        return text

                    if 500 <= response.status < 600 and attempt < self.max_retries:
                        print(f"[warn] HTTP {response.status}: {url} (retry {attempt}/{self.max_retries})")
                        if delay > 0:
                            await asyncio.sleep(delay)
                            delay *= 2
                        continue

                    print(f"[warn] HTTP {response.status}: {url}")
                    return None
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < self.max_retries:
                    print(f"[warn] Fetch attempt {attempt} failed for {url}: {exc}. Retrying...")
                    if delay > 0:
                        await asyncio.sleep(delay)
                        delay *= 2
                    continue
                print(f"[warn] Failed to fetch {url}: {exc}")

        if last_error:
            logger.warning("Fetch failed for %s after %d attempts: %s", url, self.max_retries, last_error)
        return None

    @staticmethod
    def _is_likely_html_response(content_type: str, body_text: str) -> bool:
        """Accept only likely human-readable HTML page responses."""
        ct = (content_type or "").lower()
        snippet = (body_text or "")[:3000].lower()
        html_markers = ("<html", "<body", "<main", "<article", "<head")
        has_html_markers = any(marker in snippet for marker in html_markers)
        starts_like_json = snippet.lstrip().startswith("{") or snippet.lstrip().startswith("[")
        starts_like_xml = snippet.lstrip().startswith("<?xml")
        has_feed_markers = "<rss" in snippet or "<feed" in snippet

        if ct:
            if "text/html" in ct or "application/xhtml+xml" in ct:
                return not starts_like_json and not (starts_like_xml and not has_html_markers)
            if any(
                token in ct
                for token in (
                    "application/json",
                    "text/json",
                    "application/ld+json",
                    "application/xml",
                    "text/xml",
                    "application/rss+xml",
                    "application/atom+xml",
                )
            ):
                return False
            if ct.startswith("text/"):
                return has_html_markers and not starts_like_json and not has_feed_markers
            return False

        return has_html_markers and not starts_like_json and not has_feed_markers

    async def fetch_binary(self, url: str) -> Optional[bytes]:
        """Fetch binary content such as PDFs with retry support."""
        if not await self.can_fetch(url):
            print(f"[warn] Robots.txt disallows: {url}")
            return None

        if not self.session:
            raise RuntimeError("WebCrawler session is not initialized. Use 'async with WebCrawler()'.")

        delay = self.retry_backoff
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                async with self.session.get(url) as response:
                    if response.status == 200:
                        return await response.read()

                    if 500 <= response.status < 600 and attempt < self.max_retries:
                        print(f"[warn] HTTP {response.status}: {url} (retry {attempt}/{self.max_retries})")
                        if delay > 0:
                            await asyncio.sleep(delay)
                            delay *= 2
                        continue

                    print(f"[warn] HTTP {response.status}: {url}")
                    return None
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < self.max_retries:
                    print(f"[warn] Binary fetch attempt {attempt} failed for {url}: {exc}. Retrying...")
                    if delay > 0:
                        await asyncio.sleep(delay)
                        delay *= 2
                    continue
                print(f"[warn] Failed to fetch {url}: {exc}")

        if last_error:
            logger.warning("Binary fetch failed for %s after %d attempts: %s", url, self.max_retries, last_error)
        return None

    async def discover_llms_md(self, base_url: str) -> Optional[str]:
        """Discover llms.md file for URL discovery."""
        parsed = urlparse(base_url)
        llms_urls = [
            f"{parsed.scheme}://{parsed.netloc}/llms.md",
            f"{parsed.scheme}://{parsed.netloc}/.well-known/llms.md",
            f"{parsed.scheme}://{parsed.netloc}/api/llms.md",
        ]

        for url in llms_urls:
            if await self.can_fetch(url):
                content = await self.fetch_page(url)
                if content:
                    print(f"✅ Found llms.md: {url}")
                    return content

        return None


class WebResearchAgent:

    """Intelligent web research agent with reasoning capabilities."""
    
    def __init__(
        self,
        output_dir: Path = Path("research_output"),
        system_prompt_file: Path = DEFAULT_WEB_PROMPT,
        query: str = "",
        use_llm_relevance: bool = False,
        download_docs: bool = False,
        *,
        auto_ingest: bool = False,
        ingest_threshold: float = 0.5,
        ingest_group: str = "web_research",
        ingest_chunk_size: int = 900,
        ingest_overlap: int = 120,
        resume_file: Optional[Path] = None,
        resume_reset: bool = False,
        markdown_engine: Optional[str] = None,
        crawl4ai_provider: Optional[str] = None,
        crawl4ai_platform: Optional[str] = None,
        crawl4ai_model: Optional[str] = None,
        crawl4ai_api_token: Optional[str] = None,
        crawl4ai_api_token_env: Optional[str] = None,
        crawl4ai_chunk_token_threshold: Optional[int] = None,
        crawl4ai_ignore_links: Optional[bool] = None,
        crawl4ai_headless: Optional[bool] = None,
    ):
        self.output_dir = output_dir
        self.output_dir.mkdir(exist_ok=True)
        self.query = query
        self.use_llm_relevance = use_llm_relevance
        self.download_docs = download_docs
        self.download_manifest: list[dict] = []
        if self.download_docs:
            self.download_dir = self.output_dir / "downloads"
            self.download_dir.mkdir(exist_ok=True)
        else:
            self.download_dir = None
        
        # Load system prompt
        self.system_prompt = SystemPrompt(system_prompt_file)

        # Markdown extraction strategy controls.
        env_markdown_engine = os.getenv("WEB_RESEARCH_MARKDOWN_ENGINE", "docling").strip().lower()
        self.markdown_engine = (markdown_engine or env_markdown_engine or "docling").strip().lower()
        if self.markdown_engine not in {"docling", "crawl4ai", "auto"}:
            logger.warning(
                "Unknown markdown engine '%s'; defaulting to docling.",
                self.markdown_engine,
            )
            self.markdown_engine = "docling"

        self.crawl4ai_platform = (
            crawl4ai_platform
            or os.getenv("WEB_RESEARCH_CRAWL4AI_PLATFORM", "openai")
        ).strip()
        self.crawl4ai_model = (
            crawl4ai_model
            or os.getenv("WEB_RESEARCH_CRAWL4AI_MODEL", "gpt-4o-mini")
        ).strip()
        auto_provider = (
            f"{self.crawl4ai_platform}/{self.crawl4ai_model}"
            if self.crawl4ai_platform and self.crawl4ai_model
            else ""
        )
        self.crawl4ai_provider = (
            crawl4ai_provider
            or os.getenv("WEB_RESEARCH_CRAWL4AI_PROVIDER", auto_provider)
            or auto_provider
        ).strip()

        token_env_name = (
            crawl4ai_api_token_env
            or os.getenv("WEB_RESEARCH_CRAWL4AI_API_TOKEN_ENV", "OPENAI_API_KEY")
        ).strip()
        token_direct = (
            crawl4ai_api_token
            if crawl4ai_api_token is not None
            else os.getenv("WEB_RESEARCH_CRAWL4AI_API_TOKEN", "").strip()
        )
        if token_direct:
            self.crawl4ai_api_token = token_direct.strip()
        else:
            self.crawl4ai_api_token = os.getenv(token_env_name, "").strip() if token_env_name else ""
        self.crawl4ai_api_token_env = token_env_name

        threshold_raw = (
            str(crawl4ai_chunk_token_threshold)
            if crawl4ai_chunk_token_threshold is not None
            else os.getenv("WEB_RESEARCH_CRAWL4AI_CHUNK_TOKEN_THRESHOLD", "4096")
        )
        try:
            self.crawl4ai_chunk_token_threshold = max(256, int(threshold_raw))
        except ValueError:
            self.crawl4ai_chunk_token_threshold = 4096

        if crawl4ai_ignore_links is None:
            self.crawl4ai_ignore_links = _env_bool(
                "WEB_RESEARCH_CRAWL4AI_IGNORE_LINKS", default=False
            )
        else:
            self.crawl4ai_ignore_links = bool(crawl4ai_ignore_links)

        if crawl4ai_headless is None:
            self.crawl4ai_headless = _env_bool(
                "WEB_RESEARCH_CRAWL4AI_HEADLESS", default=True
            )
        else:
            self.crawl4ai_headless = bool(crawl4ai_headless)

        if self.markdown_engine in {"crawl4ai", "auto"} and not _CRAWL4AI_AVAILABLE:
            logger.warning(
                "Crawl4AI markdown engine requested but crawl4ai is not installed; "
                "falling back to Docling/basic extraction."
            )
        
        # Configure Ollama
        os.environ["OLLAMA_HOST"] = OLLAMA_HOST
        os.environ["OLLAMA_API_KEY"] = OLLAMA_API_KEY
        
        # Initialize Docling converter if available
        if DocumentConverter is not None:
            try:
                self.converter = DocumentConverter()
            except Exception as exc:  # pragma: no cover - best effort init
                logger.warning("Docling initialization failed: %s", exc)
                self.converter = None
        else:
            self.converter = None
        
        # Track visited URLs to avoid loops
        self.visited_urls: Set[str] = set()
        self.resume_file = resume_file.expanduser().resolve() if resume_file else None
        self.resume_reset = resume_reset
        
        # Initialize crawler
        self.crawler = WebCrawler()

        # Auto-ingest configuration
        self.auto_ingest = auto_ingest
        self.ingest_threshold = ingest_threshold
        self.ingest_group = ingest_group
        self.ingest_chunk_size = ingest_chunk_size
        self.ingest_overlap = ingest_overlap
        self.ingestion_service = KnowledgeIngestionService() if auto_ingest else None
        self.ingest_documents = 0
        self.ingest_nodes = 0
        self.ingest_edges = 0
    
    async def search_web(self, query: str, max_results: int = 10) -> List[str]:
        """Search the web for relevant URLs."""
        # Try multiple search approaches
        urls = []
        
        encoded_query = quote_plus(query)

        # Method 1: Try Brave Search
        search_url = f"https://search.brave.com/search?q={encoded_query}"
        async with self.crawler as crawler:
            html = await crawler.fetch_page(search_url)
            if html:
                urls = self._extract_search_urls(html, max_results, "brave.com")
        
        # Method 2: Try DuckDuckGo
        if not urls:
            search_url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
            async with self.crawler as crawler:
                html = await crawler.fetch_page(search_url)
                if html:
                    urls = self._extract_search_urls(html, max_results, "duckduckgo.com")
        
        # Method 3: Fallback to curated sources based on query
        if not urls:
            urls = self._fallback_search(query, max_results)
        
        return urls
    
    def _extract_search_urls(self, html: str, max_results: int, search_domain: str) -> List[str]:
        """Extract URLs from search engine results."""
        url_patterns = [
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"',
            r'<a[^>]+class="result-link"[^>]+href="([^"]+)"',
            r'<a[^>]+href="([^"]+)"[^>]*>[^<]*(?:result|search|link)',
        ]

        matches = []
        for pattern in url_patterns:
            matches.extend(re.findall(pattern, html, re.IGNORECASE))

        # Clean and deduplicate URLs
        urls = []
        for url in matches[: max_results * 2]:
            normalized = url
            lower = normalized.lower()
            if lower.startswith(("javascript:", "mailto:", "tel:")) or normalized.startswith("#"):
                continue

            if normalized.startswith("/l/?"):
                params = parse_qs(urlparse(normalized).query)
                target = params.get("uddg") or params.get("u")
                if target:
                    normalized = unquote(target[0])
                else:
                    continue
            elif normalized.startswith("/d.js?"):
                params = parse_qs(urlparse(normalized).query)
                target = params.get("u") or params.get("uddg")
                if target:
                    normalized = unquote(target[0])
                else:
                    continue

            if normalized.startswith("/"):
                normalized = urljoin(f"https://{search_domain}", normalized)
            elif normalized.startswith("//"):
                normalized = f"https:{normalized}"

            if normalized not in urls and normalized not in self.visited_urls and normalized.startswith("http"):
                urls.append(normalized)
                if len(urls) >= max_results:
                    break

        return urls

    def _fallback_search(self, query: str, max_results: int) -> List[str]:
        """Fallback search using curated sources."""
        query_lower = query.lower()
        
        # Curated sources for different topics
        sources = {
            'ai': [
                'https://arxiv.org/list/cs.AI/recent',
                'https://openai.com/research',
                'https://ai.google/research',
                'https://deepmind.google/research',
            ],
            'machine learning': [
                'https://scikit-learn.org/stable/',
                'https://www.tensorflow.org/',
                'https://pytorch.org/',
                'https://www.kaggle.com/learn',
            ],
            'research': [
                'https://scholar.google.com',
                'https://arxiv.org',
                'https://research.microsoft.com',
                'https://ai.facebook.com/research',
            ],
            'pydantic': [
                'https://docs.pydantic.dev/',
                'https://github.com/pydantic/pydantic',
                'https://pypi.org/project/pydantic/',
                'https://pydantic-docs.helpmanual.io/',
            ],
            'python': [
                'https://docs.python.org/',
                'https://pypi.org/',
                'https://github.com/topics/python',
                'https://realpython.com/',
            ],
            'default': [
                'https://en.wikipedia.org/wiki/Main_Page',
                'https://github.com/topics',
                'https://stackoverflow.com/questions',
                'https://www.reddit.com/r/technology',
            ]
        }
        
        # Select appropriate sources
        selected_sources = sources['default']
        # Check for specific keywords first
        for key, source_list in sources.items():
            if key != 'default' and key in query_lower:
                selected_sources = source_list
                break
        
        return selected_sources[:max_results]
    
    async def evaluate_relevance(self, url: str, content: str, query: str) -> float:
        """Use reasoning model to evaluate content relevance."""
        prompt = self.system_prompt.get_relevance_evaluation_prompt(query, url, content)
        if not _OLLAMA_AVAILABLE:
            return self.simple_relevance(content, url)

        try:
            result = await asyncio.to_thread(
                ollama_chat,
                model=REASON_MODEL,
                messages=[{"role": "user", "content": prompt}]
            )

            score_text = result.get("message", {}).get("content", "0.5").strip()
            match = re.search(r"(\d+(?:\.\d+)?)", score_text)
            if not match:
                raise ValueError(f"No numeric score found in response: {score_text!r}")

            score = float(match.group(1))
            if score > 1.0:
                score = score / 100 if score <= 100 else 1.0

            score = max(0.0, min(score, 1.0))
            return score
        except Exception as e:
            logger.warning("Relevance evaluation failed for %s: %s", url, e)
            print(f"[warn] Relevance evaluation failed: {e}")
            return self.simple_relevance(content, url)


    def simple_relevance(self, content: str, title: str) -> float:
        """Fallback heuristic relevance scoring."""
        title_lower = title.lower()
        content_lower = content.lower()
        relevance = 0.5
        if any(word in title_lower or word in content_lower for word in ['documentation', 'tutorial', 'guide', 'api', 'reference']):
            relevance = 0.8
        if any(word in content_lower for word in ['learn', 'example', 'how to', 'getting started', 'home insurance']):
            relevance = max(relevance, 0.7)
        if any(word in content_lower for word in ['buy', 'price', 'sale', 'news', 'blog']):
            relevance = min(relevance, 0.3)
        return relevance
    
    async def extract_links(self, content: str, base_url: str) -> List[str]:
        """Extract relevant links from content using reasoning model."""
        prompt = self.system_prompt.get_link_extraction_prompt(content, base_url)
        if not _OLLAMA_AVAILABLE:
            # Heuristic: pull hrefs via BeautifulSoup if available.
            links: list[str] = []
            if BeautifulSoup:
                soup = BeautifulSoup(content, "html.parser")
                for a in soup.find_all("a", href=True):
                    link = urljoin(base_url, a["href"])
                    links.append(link)
            return links[:10]

        try:
            result = ollama_chat(
                model=REASON_MODEL,
                messages=[{"role": "user", "content": prompt}]
            )

            links_text = result.get("message", {}).get("content", "[]")
            links = json.loads(links_text)

            # Convert relative URLs to absolute
            absolute_links = []
            for link in links:
                if isinstance(link, str):
                    if link.startswith('/'):
                        link = urljoin(base_url, link)
                    elif not link.startswith(('http://', 'https://')):
                        link = urljoin(base_url, link)
                    absolute_links.append(link)

            return absolute_links
        except Exception as e:
            print(f"⚠️ Link extraction failed: {e}")
            return []

    def _use_crawl4ai_for_markdown(self) -> bool:
        if self.markdown_engine == "docling":
            return False
        if self.markdown_engine in {"crawl4ai", "auto"} and _CRAWL4AI_AVAILABLE:
            return True
        return False

    def _crawl4ai_instruction(self, url: str) -> str:
        query_line = f"Research query: {self.query}\n" if self.query else ""
        return (
            f"{self.system_prompt.prompt}\n\n"
            f"{query_line}"
            "Task: Produce clean, high-signal markdown from this page.\n\n"
            "Hard exclusions:\n"
            "- table of contents/contents blocks\n"
            "- global navigation, breadcrumbs, sidebars, headers, footers\n"
            "- advertisements, sponsored/promotional blocks, cookie/privacy banners\n"
            "- related-content link farms, social share widgets, pagination UI\n\n"
            "Keep only primary factual content that helps answer domain questions.\n"
            "Preserve meaningful headings, lists, and tables.\n"
            "Do not add commentary or hallucinated content.\n"
            f"Source URL: {url}\n"
        )

    def _extract_crawl4ai_markdown(self, crawl_result: Any) -> str:
        markdown_obj = getattr(crawl_result, "markdown", None)
        if isinstance(markdown_obj, str) and markdown_obj.strip():
            return markdown_obj.strip()
        if markdown_obj is not None:
            for attr in (
                "fit_markdown",
                "markdown_with_citations",
                "raw_markdown",
                "markdown",
            ):
                value = getattr(markdown_obj, attr, None)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        for attr in ("fit_markdown", "markdown", "raw_markdown"):
            value = getattr(crawl_result, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    async def _convert_to_markdown_crawl4ai(self, url: str) -> str:
        if not _CRAWL4AI_AVAILABLE:
            raise RuntimeError("crawl4ai dependency is not available")

        llm_config = LLMConfig(
            provider=self.crawl4ai_provider,
            api_token=self.crawl4ai_api_token or None,
        )
        content_filter = LLMContentFilter(
            llm_config=llm_config,
            instruction=self._crawl4ai_instruction(url),
            chunk_token_threshold=self.crawl4ai_chunk_token_threshold,
            verbose=False,
        )
        markdown_generator = DefaultMarkdownGenerator(
            content_filter=content_filter,
            options={"ignore_links": self.crawl4ai_ignore_links},
        )
        run_config = CrawlerRunConfig(markdown_generator=markdown_generator)
        browser_config = BrowserConfig(headless=self.crawl4ai_headless)

        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=url, config=run_config)

        if not getattr(result, "success", False):
            error = getattr(result, "error_message", "unknown crawl4ai error")
            raise RuntimeError(str(error))

        markdown = self._extract_crawl4ai_markdown(result)
        if not markdown:
            raise RuntimeError("crawl4ai returned empty markdown")
        return markdown

    async def convert_to_markdown(self, url: str, content: str) -> str:
        """Convert web content to markdown using Crawl4AI or Docling."""
        if self._use_crawl4ai_for_markdown():
            try:
                return await self._convert_to_markdown_crawl4ai(url)
            except Exception as exc:
                logger.warning(
                    "Crawl4AI markdown conversion failed for %s: %s. Falling back to Docling/basic conversion.",
                    url,
                    exc,
                )

        # Save content to temporary file
        temp_file = self.output_dir / f"temp_{hash(url) % 100000}.html"
        temp_file.write_text(content, encoding='utf-8')
        
        try:
            if self.converter is not None:
                try:
                    # Convert using Docling (run conversion off the event loop)
                    result = await asyncio.to_thread(
                        self.converter.convert, str(temp_file)
                    )

                    if hasattr(result, "document"):
                        markdown = result.document.export_to_markdown()
                    else:
                        markdown = str(result)

                    return markdown
                except Exception as e:
                    print(f"⚠️ Markdown conversion failed: {e}")

            # Fallback to basic HTML to text conversion
            import re

            text = re.sub(r"<[^>]+>", "\n", content)
            text = re.sub(r"\n+", "\n", text)
            return text.strip()
        finally:
            # Clean up temp file
            if temp_file.exists():
                temp_file.unlink()

    def reset_session_state(self) -> None:
        """Reset per-run stateful trackers."""
        self.visited_urls.clear()
        self.download_manifest = []
    
    async def research_from_url(
        self,
        start_url: str,
        max_depth: int = 2,
        max_pages: int = 20,
        max_seconds: Optional[int] = None,
        reset_state: bool = True,
    ) -> List[ResearchResult]:
        """Perform web research starting from a specific URL."""
        print(f"[start] Starting research from: {start_url}")

        if reset_state:
            self.reset_session_state()

        if self.resume_file and self.resume_reset and self.resume_file.exists():
            try:
                self.resume_file.unlink()
            except Exception as exc:
                logger.warning("Failed to remove existing resume file: %s", exc)
        
        results: list[ResearchResult] = []

        if self.resume_file and self.resume_file.exists() and not self.resume_reset:
            queue, resume_visited = self._load_resume_state(start_url, max_depth)
            self.visited_urls.update(resume_visited)
            processed_count = len(self.visited_urls)
        else:
            queue = [(start_url, 0)]
            processed_count = 0
        start_time = time.time()
        query_text = self.query or start_url
        
        async with self.crawler:
            while queue and processed_count < max_pages:
                if max_seconds and (time.time() - start_time) >= max_seconds:
                    print("[stop] Time limit reached. Ending crawl.")
                    break
                
                url, depth = queue.pop(0)
                if url in self.visited_urls or depth > max_depth:
                    continue

                if not self._is_probable_content_url(url):
                    self.visited_urls.add(url)
                    print(f"[skip] Non-content URL pattern: {url}")
                    continue
                
                self.visited_urls.add(url)
                processed_count += 1
                
                print(f"\n[page] depth={depth} url={url}")
                print(f"[fetch] {url}")
                
                content = await self.crawler.fetch_page(url)
                if not content:
                    continue

                if self._looks_like_machine_payload(content):
                    print(f"[skip] Machine payload detected: {url}")
                    continue
                
                title_match = re.search(r'<title[^>]*>([^<]+)</title>', content, re.IGNORECASE)
                title = title_match.group(1).strip() if title_match else url
                
                if depth == 0:
                    relevance = 1.0
                elif self.use_llm_relevance:
                    relevance = await self.evaluate_relevance(url, content, query_text)
                else:
                    relevance = self.simple_relevance(content, title)
                
                if relevance < 0.2 and depth > 0:
                    print(f"[skip] Low relevance ({relevance:.2f}): {url}")
                    continue
                
                print(f"[ok] Relevant ({relevance:.2f}): {url}")

                primary_content = self._extract_main_html(content)
                markdown = await self.convert_to_markdown(url, primary_content)
                
                safe_name = re.sub(r'[^\w\s-]', '', title)
                safe_name = re.sub(r'[-\s]+', '_', safe_name)
                safe_name = safe_name.strip(' _')[:50] or f"page_{hash(url) % 10000}"
                markdown_path = self.output_dir / f"{safe_name}_{hash(url) % 10000}.md"
                markdown_path.write_text(markdown, encoding='utf-8')
                
                result = ResearchResult(
                    url=url,
                    title=title,
                    content=markdown,
                    markdown_path=markdown_path,
                    relevance_score=relevance,
                )
                results.append(result)

                if self.auto_ingest and relevance >= self.ingest_threshold:
                    self._ingest_markdown(result)
                
                html_links, doc_links = self._extract_simple_links(content, url)
                
                if depth < max_depth:
                    for link in html_links:
                        if link not in self.visited_urls:
                            queue.append((link, depth + 1))
                
                if self.download_docs and doc_links:
                    for doc_url in doc_links:
                        try:
                            await self.download_document(doc_url)
                        except Exception as exc:
                            print(f"[doc-warn] Failed to download {doc_url}: {exc}")

                if self.resume_file:
                    self._save_resume_state(start_url, queue)
        
        if self.download_docs and self.download_manifest and self.download_dir:
            manifest_path = self.download_dir / "downloads_manifest.json"
            manifest_path.write_text(json.dumps(self.download_manifest, indent=2), encoding="utf-8")
            print(f"[doc] Manifest saved to: {manifest_path}")

        if self.resume_file:
            if queue:
                self._save_resume_state(start_url, queue)
            else:
                self._clear_resume_state()
        
        print(f"\n[done] Research complete. Relevant pages: {len(results)}.")
        if self.auto_ingest:
            print(
                f"[ingest-summary] documents={self.ingest_documents} "
                f"nodes={self.ingest_nodes} edges={self.ingest_edges}"
            )
        return results

    def _extract_main_html(self, html: str) -> str:
        """Trim HTML to the likely primary content region."""
        if not html or BeautifulSoup is None:
            return html

        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return html

        body = soup.body or soup
        if body is None:
            return html

        # Remove obvious non-content tags early.
        for tag in body(["script", "style", "noscript", "iframe", "svg", "canvas"]):
            tag.decompose()

        noise_tokens = {
            "sidebar",
            "side-bar",
            "nav",
            "navigation",
            "menu",
            "breadcrumb",
            "crumb",
            "footer",
            "foot",
            "social",
            "share",
            "promo",
            "advert",
            "ad-",
            "banner",
            "subscribe",
            "related",
            "recommend",
        }

        def is_noise(tag) -> bool:
            attrs: list[str] = []
            tag_attrs = getattr(tag, "attrs", None)
            if not isinstance(tag_attrs, dict):
                return False
            tag_id = tag_attrs.get("id")
            if isinstance(tag_id, str) and tag_id:
                attrs.append(tag_id)
            tag_classes = tag_attrs.get("class")
            if isinstance(tag_classes, str) and tag_classes:
                attrs.extend(tag_classes.split())
            elif isinstance(tag_classes, (list, tuple)):
                attrs.extend(str(cls) for cls in tag_classes if cls)
            combined = " ".join(attrs).lower()
            return any(token in combined for token in noise_tokens)

        for tag in list(body.find_all(True)):
            if tag.name in {"header", "footer", "nav", "aside"} or is_noise(tag):
                tag.decompose()

        selectors = [
            "main[role='main']",
            "main",
            "[role='main']",
            "article",
            "div#main",
            "div#content",
            "div.main-content",
            "div.content",
            "section#main",
            "section.main-content",
        ]

        candidate = None
        for selector in selectors:
            candidate = body.select_one(selector)
            if candidate:
                break

        if candidate is None:
            blocks = [
                tag
                for tag in body.find_all(["article", "section", "div"], limit=50)
                if len(tag.get_text(strip=True)) > 200
            ]
            if blocks:
                candidate = max(blocks, key=lambda tag: len(tag.get_text(strip=True)))
        if candidate is None:
            candidate = body

        for tag in candidate.find_all(['footer']):
            tag.decompose()
        for tag in list(candidate.find_all(True)):
            if is_noise(tag):
                tag.decompose()

        cleaned = str(candidate)
        if "<body" not in cleaned.lower():
            cleaned = f"<html><body>{cleaned}</body></html>"
        return cleaned
    
    def _is_probable_content_url(self, url: str) -> bool:
        """Filter out obvious machine endpoints (feeds, APIs, embeds, admin routes)."""
        parsed = urlparse(url)
        path = (parsed.path or "").lower()
        query = (parsed.query or "").lower()
        segments = [seg for seg in path.split("/") if seg]

        blocked_extensions = (".json", ".xml", ".rss", ".atom")
        if any(path.endswith(ext) for ext in blocked_extensions):
            return False

        if segments:
            if segments[0] in {"wp-json", "wp-admin", "wp-includes"}:
                return False
            if "feed" in segments or "oembed" in segments:
                return False
            if "embed" in segments and len(segments) <= 2:
                return False

        if "wp-json" in path or "oembed" in path:
            return False

        blocked_query_tokens = (
            "rest_route=",
            "oembed",
            "feed=",
            "format=json",
            "output=json",
            "output=xml",
            "type=rss",
        )
        if any(token in query for token in blocked_query_tokens):
            return False

        return True

    @staticmethod
    def _looks_like_machine_payload(content: str) -> bool:
        """Guard against JSON/XML/RSS payloads misclassified as regular pages."""
        if not content:
            return True
        snippet = content.lstrip()[:3000].lower()
        if snippet.startswith("{") or snippet.startswith("["):
            return True
        if snippet.startswith("<?xml") and ("<rss" in snippet or "<feed" in snippet):
            return True
        if "<rss" in snippet or "<feed" in snippet:
            return True
        return False
    
    def _extract_simple_links(self, content: str, base_url: str) -> tuple[list[str], list[str]]:
        """Extract HTML links and document links from content."""
        from urllib.parse import urljoin, urlparse
        
        href_pattern = r'href=["\']([^"\']+)["\']'
        matches = re.findall(href_pattern, content, re.IGNORECASE)
        
        html_links: list[str] = []
        doc_links: list[str] = []
        base_domain = urlparse(base_url).netloc
        doc_extensions = ('.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx', '.txt')
        skip_tokens = ['javascript:', 'mailto:', '#', 'tel:', 'ftp:', '.css', '.js', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.zip', '.exe', '.dmg', '.ico']
        
        for raw_link in matches[:200]:
            lower = raw_link.lower()
            if any(token in lower for token in skip_tokens):
                continue
            
            # Convert relative URLs to absolute
            if raw_link.startswith('/'):
                full_link = urljoin(base_url, raw_link)
            elif raw_link.startswith(('http://', 'https://')):
                full_link = raw_link
            else:
                full_link = urljoin(base_url, raw_link)
            
            lower_full = full_link.lower()
            if any(lower_full.endswith(ext) for ext in doc_extensions):
                doc_links.append(full_link)
                continue
            
            parsed = urlparse(full_link)
            if (
                parsed.netloc == base_domain
                or any(domain in parsed.netloc for domain in ['docs.', 'documentation.', 'api.', 'guide.'])
            ):
                if not self._is_probable_content_url(full_link):
                    continue
                html_links.append(full_link)
        
        def dedupe(sequence: list[str]) -> list[str]:
            seen = set()
            uniq = []
            for item in sequence:
                if item not in seen:
                    seen.add(item)
                    uniq.append(item)
            return uniq
        
        return dedupe(html_links), dedupe(doc_links)
    
    def prepare_rag_content(self, results: List[ResearchResult], output_file: Optional[Path] = None) -> Path:
        """Prepare consolidated content for RAG/CAG systems."""
        if not output_file:
            output_file = self.output_dir / "research_consolidated.md"

        # Sort by relevance
        results.sort(key=lambda x: x.relevance_score, reverse=True)
        
        generated_ts = datetime.utcnow().isoformat() + "Z"
        content = "# Web Research Results\n\n"
        content += f"Generated: {generated_ts}\n\n"
        content += f"Total sources: {len(results)}\n\n"

        content += "---\n\n"
        
        for i, result in enumerate(results, 1):
            content += f"## Source {i}: {result.title}\n\n"
            content += f"**URL:** {result.url}\n"
            content += f"**Relevance:** {result.relevance_score:.2f}\n\n"
            content += result.content
            content += "\n\n---\n\n"
        
        output_file.write_text(content, encoding='utf-8')
        print(f"[rag] RAG content saved to: {output_file}")
        return output_file

    async def download_document(self, url: str) -> Optional[Path]:
        """Download a linked document into the downloads folder."""
        if not self.download_docs or not self.download_dir:
            return None
        filename = Path(urlparse(url).path).name or f"doc_{abs(hash(url))}.bin"
        target = self.download_dir / filename
        data = await self.crawler.fetch_binary(url)
        if not data:
            return None
        target.write_bytes(data)
        self.download_manifest.append({"url": url, "path": str(target)})
        print(f"[doc] Saved {url} -> {target}")
        return target

    def _ingest_markdown(self, result: ResearchResult) -> None:
        """Automatically ingest high-relevance markdown into Supabase."""
        if not self.ingestion_service:
            return
        try:
            text = result.markdown_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to read markdown for ingestion (%s): %s", result.markdown_path, exc)
            return

        paragraphs = split_into_paragraphs(text)
        if not paragraphs:
            return
        chunks_text = chunk_text(paragraphs, chunk_size=self.ingest_chunk_size, overlap=self.ingest_overlap)
        if not chunks_text:
            return

        slug = slugify(result.markdown_path.stem)
        group_id = f"{self.ingest_group}:{slug}"
        chunks: list[EpisodeChunk] = []
        for idx, chunk in enumerate(chunks_text, start=1):
            chunks.append(
                EpisodeChunk(
                    chunk_id=f"{group_id}:{idx:04d}",
                    text=chunk,
                    metadata={
                        "source": result.url,
                        "title": result.title,
                        "chunk_index": idx,
                        "tags": ["web_research", "auto_ingest"],
                    },
                )
            )

        payload = EpisodePayload(
            episode_id=f"EP:{group_id}:{uuid.uuid4().hex[:8]}",
            source=result.url,
            source_type="html",
            reference_time=datetime.utcnow(),
            group_id=group_id,
            tags=["web_research", "auto_ingest"],
            chunks=chunks,
            raw_text=text,
            metadata={
                "markdown_path": str(result.markdown_path),
                "relevance": result.relevance_score,
            },
        )

        try:
            outcome = self.ingestion_service.ingest_episode(payload)
        except Exception as exc:
            logger.warning("Auto-ingest failed for %s: %s", result.url, exc)
            return

        self.ingest_documents += outcome.documents_written
        self.ingest_nodes += outcome.nodes_written
        self.ingest_edges += outcome.edges_written
        print(
            "[ingest] %s -> docs=%s nodes=%s edges=%s"
            % (
                result.title[:60] or result.url,
                outcome.documents_written,
                outcome.nodes_written,
                outcome.edges_written,
            )
        )

    def _load_resume_state(self, start_url: str, max_depth: int) -> tuple[list[tuple[str, int]], Set[str]]:
        """Load queue/visited data from resume file."""
        if not self.resume_file or not self.resume_file.exists():
            return [(start_url, 0)], set()
        try:
            data = json.loads(self.resume_file.read_text(encoding="utf-8"))
            if data.get("start_url") != start_url:
                return [(start_url, 0)], set()
            queue_data = [
                (entry["url"], int(entry.get("depth", 0)))
                for entry in data.get("queue", [])
                if entry.get("url") and int(entry.get("depth", 0)) <= max_depth
            ]
            visited = set(data.get("visited", []))
            if not queue_data:
                queue_data = [(start_url, 0)]
            return queue_data, visited
        except Exception as exc:
            logger.warning("Failed to load resume state: %s", exc)
            return [(start_url, 0)], set()

    def _save_resume_state(self, start_url: str, queue: list[tuple[str, int]]) -> None:
        """Persist resume state to disk."""
        if not self.resume_file:
            return
        state = {
            "start_url": start_url,
            "queue": [{"url": url, "depth": depth} for url, depth in queue],
            "visited": sorted(self.visited_urls),
        }
        try:
            self.resume_file.parent.mkdir(parents=True, exist_ok=True)
            self.resume_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to save resume state: %s", exc)

    def _clear_resume_state(self) -> None:
        """Delete resume file when crawl completes."""
        if self.resume_file and self.resume_file.exists():
            try:
                self.resume_file.unlink()
            except Exception as exc:
                logger.warning("Failed to clear resume state: %s", exc)


async def main():
    """Main entry point for web research agent."""
    parser = argparse.ArgumentParser(
        description="Crawl a seed URL, gather relevant content, and export Markdown summaries.",
    )
    parser.add_argument("start_url", help="Starting URL to crawl (e.g., https://www.tdi.texas.gov/)")
    parser.add_argument(
        "max_depth",
        nargs="?",
        type=int,
        default=2,
        help="Maximum crawl depth (default: 2)",
    )
    parser.add_argument(
        "max_pages",
        nargs="?",
        type=int,
        default=20,
        help="Maximum number of pages to process (default: 20)",
    )
    parser.add_argument(
        "--outdir",
        default="research_output",
        help="Directory for Markdown outputs (default: research_output)",
    )
    parser.add_argument(
        "--query",
        default="",
        help="Optional natural-language research query used for relevance scoring.",
    )
    parser.add_argument(
        "--max-seconds",
        type=int,
        default=None,
        help="Maximum time (in seconds) to spend crawling. Default: no limit.",
    )
    parser.add_argument(
        "--download-docs",
        action="store_true",
        help="Download linked documents (PDF, DOCX, etc.) into an output subfolder.",
    )
    parser.add_argument(
        "--llm-relevance",
        action="store_true",
        help="Use the reasoning model to score relevance instead of the heuristic filter.",
    )
    parser.add_argument(
        "--markdown-engine",
        choices=["docling", "crawl4ai", "auto"],
        default=os.getenv("WEB_RESEARCH_MARKDOWN_ENGINE", "docling"),
        help="Markdown conversion backend (default: WEB_RESEARCH_MARKDOWN_ENGINE or docling).",
    )
    parser.add_argument(
        "--crawl4ai-provider",
        default=None,
        help="Crawl4AI LLM provider string (e.g., openai/gpt-4o-mini). Overrides platform/model.",
    )
    parser.add_argument(
        "--crawl4ai-platform",
        default=None,
        help="Crawl4AI platform for provider assembly (e.g., openai, anthropic, ollama).",
    )
    parser.add_argument(
        "--crawl4ai-model",
        default=None,
        help="Crawl4AI model for provider assembly (e.g., gpt-4o-mini).",
    )
    parser.add_argument(
        "--crawl4ai-api-token",
        default=None,
        help="LLM API token used by Crawl4AI markdown filtering.",
    )
    parser.add_argument(
        "--crawl4ai-api-token-env",
        default=None,
        help="Env var name to read LLM API token from (default: OPENAI_API_KEY).",
    )
    parser.add_argument(
        "--crawl4ai-chunk-token-threshold",
        type=int,
        default=None,
        help="Chunk token threshold for Crawl4AI LLM filter.",
    )
    parser.add_argument(
        "--crawl4ai-ignore-links",
        action="store_true",
        help="When using Crawl4AI markdown generation, drop links from markdown output.",
    )
    parser.add_argument(
        "--crawl4ai-keep-links",
        action="store_true",
        help="When using Crawl4AI markdown generation, keep links in markdown output.",
    )
    parser.add_argument(
        "--crawl4ai-headless",
        action="store_true",
        help="Run Crawl4AI browser in headless mode.",
    )
    parser.add_argument(
        "--crawl4ai-headed",
        action="store_true",
        help="Run Crawl4AI browser with visible UI (debugging).",
    )
    parser.add_argument(
        "--auto-ingest",
        action="store_true",
        help="Automatically ingest relevant markdown pages into Supabase.",
    )
    parser.add_argument(
        "--ingest-threshold",
        type=float,
        default=0.5,
        help="Minimum relevance score required before ingestion (default: 0.5).",
    )
    parser.add_argument(
        "--ingest-group",
        default="web_research",
        help="Prefix for auto-ingestion group IDs (default: web_research).",
    )
    parser.add_argument(
        "--ingest-chunk-size",
        type=int,
        default=900,
        help="Chunk size used for auto-ingestion (default: 900).",
    )
    parser.add_argument(
        "--ingest-overlap",
        type=int,
        default=120,
        help="Chunk overlap used for auto-ingestion (default: 120).",
    )
    parser.add_argument(
        "--resume-file",
        default=None,
        help="Path to a JSON state file used to resume long crawls.",
    )
    parser.add_argument(
        "--resume-reset",
        action="store_true",
        help="Ignore any existing resume file and start a fresh crawl.",
    )
    args = parser.parse_args()

    resume_path = Path(args.resume_file).expanduser().resolve() if args.resume_file else None
    try:
        crawl4ai_ignore_links = _parse_optional_bool(
            args.crawl4ai_ignore_links,
            args.crawl4ai_keep_links,
            "WEB_RESEARCH_CRAWL4AI_IGNORE_LINKS",
            default=False,
        )
        crawl4ai_headless = _parse_optional_bool(
            args.crawl4ai_headless,
            args.crawl4ai_headed,
            "WEB_RESEARCH_CRAWL4AI_HEADLESS",
            default=True,
        )
    except ValueError as exc:
        parser.error(str(exc))

    try:
        settings = get_settings()
        # Require Supabase/OpenAI only when auto-ingesting (embeddings + writes).
        if args.auto_ingest:
            settings.require_groups("supabase", "openai")
    except SettingsError as exc:
        print(f"[!] Environment validation failed: {exc}")
        return

    agent = WebResearchAgent(
        output_dir=Path(args.outdir).expanduser().resolve(),
        query=args.query,
        use_llm_relevance=args.llm_relevance,
        markdown_engine=args.markdown_engine,
        crawl4ai_provider=args.crawl4ai_provider,
        crawl4ai_platform=args.crawl4ai_platform,
        crawl4ai_model=args.crawl4ai_model,
        crawl4ai_api_token=args.crawl4ai_api_token,
        crawl4ai_api_token_env=args.crawl4ai_api_token_env,
        crawl4ai_chunk_token_threshold=args.crawl4ai_chunk_token_threshold,
        crawl4ai_ignore_links=crawl4ai_ignore_links,
        crawl4ai_headless=crawl4ai_headless,
        download_docs=args.download_docs,
        auto_ingest=args.auto_ingest,
        ingest_threshold=args.ingest_threshold,
        ingest_group=args.ingest_group,
        ingest_chunk_size=args.ingest_chunk_size,
        ingest_overlap=args.ingest_overlap,
        resume_file=resume_path,
        resume_reset=args.resume_reset,
    )
    results = await agent.research_from_url(
        args.start_url,
        max_depth=args.max_depth,
        max_pages=args.max_pages,
        max_seconds=args.max_seconds,
    )
    
    # Prepare RAG content
    rag_file = agent.prepare_rag_content(results)
    
    print(f"\n[summary]")
    print(f"  - Total pages processed: {len(results)}")
    if results:
        print(f"  - Average relevance: {sum(r.relevance_score for r in results) / len(results):.2f}")
    else:
        print(f"  - Average relevance: N/A (no relevant pages found)")
    print(f"  - RAG file: {rag_file}")


if __name__ == "__main__":
    asyncio.run(main())
