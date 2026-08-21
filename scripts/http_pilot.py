#!/usr/bin/env python3
"""Resumable HTTP/sitemap/contact/legal crawler for the .ro pilot sample."""

from __future__ import annotations

import argparse
import asyncio
import csv
import ctypes
import gc
import gzip
import hashlib
import io
import ipaddress
import json
import re
import socket
import sqlite3
import ssl
import sys
import time
import urllib.robotparser
import zlib
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import aiohttp
from aiohttp.abc import AbstractResolver
from bs4 import BeautifulSoup
from defusedxml import ElementTree as SafeET

from company_identity import extract_company_identity, sanitize_html_bytes


USER_AGENT_TOKEN = "RODomainObservatory"
USER_AGENT = (
    "RODomainObservatory/0.1 (+https://github.com/web3metatrade/ro-domain-observatory)"
)
HTML_TYPES = ("text/html", "application/xhtml+xml")
SITEMAP_HINTS = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/wp-sitemap.xml",
    "/sitemap.xml.gz",
)

CLASS_PATTERNS: dict[str, tuple[str, ...]] = {
    "contact": (
        "contact", "contacte", "contact-us", "contactati-ne", "contacteaza-ne",
        "get-in-touch", "kapcsolat", "kontakt",
    ),
    "about": (
        "despre", "despre-noi", "cine-suntem", "companie", "echipa", "about",
        "about-us", "company", "rolunk", "uber-uns",
    ),
    "privacy": (
        "confidentialitate", "politica-de-confidentialitate", "privacy", "privacy-policy",
        "protectia-datelor", "data-protection", "adatvedelem", "adatkezeles", "datenschutz",
    ),
    "terms": (
        "termeni", "termeni-si-conditii", "termeni-conditii", "conditii-de-utilizare",
        "terms", "terms-and-conditions", "terms-of-service", "felhasznalasi-feltetelek", "agb",
    ),
    "cookie_policy": (
        "cookie", "cookies", "cookie-policy", "politica-cookie", "politica-de-cookies",
    ),
    "legal_notice": (
        "legal", "informatii-legale", "mentiuni-legale", "legal-notice", "imprint", "impressum",
    ),
    "gdpr": ("gdpr", "rgpd", "protectia-datelor", "data-protection"),
    "consumer_protection": (
        "anpc", "solutionarea-litigiilor", "solutionare-litigii", "litigii", "consumer-protection",
    ),
    "security_contact": ("security", "security-policy", "responsible-disclosure"),
}

FALLBACK_ROUTES: tuple[tuple[str, str], ...] = (
    ("contact", "contact"),
    ("contacte", "contact"),
    ("contact-us", "contact"),
    ("contactati-ne", "contact"),
    ("despre-noi", "about"),
    ("about-us", "about"),
    ("politica-de-confidentialitate", "privacy"),
    ("confidentialitate", "privacy"),
    ("privacy-policy", "privacy"),
    ("gdpr", "gdpr"),
    ("protectia-datelor", "gdpr"),
    ("termeni-si-conditii", "terms"),
    ("termeni-conditii", "terms"),
    ("terms-and-conditions", "terms"),
    ("termeni", "terms"),
    ("politica-cookie", "cookie_policy"),
    ("cookie-policy", "cookie_policy"),
    ("cookies", "cookie_policy"),
    ("informatii-legale", "legal_notice"),
    ("mentiuni-legale", "legal_notice"),
    ("legal", "legal_notice"),
    ("anpc", "consumer_protection"),
    ("solutionarea-litigiilor", "consumer_protection"),
    ("kapcsolat", "contact"),
    ("adatvedelem", "privacy"),
    ("impresszum", "legal_notice"),
    ("kontakt", "contact"),
    ("datenschutz", "privacy"),
    ("impressum", "legal_notice"),
    (".well-known/security.txt", "security_contact"),
)

EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])([a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+)")
PHONE_RE = re.compile(r"(?<!\d)(?:\+40|0040|0)[\s().-]*(?:\d[\s().-]*){8,9}(?!\d)")
CUI_RE = re.compile(r"(?i)\b(?:CUI|CIF|cod(?:ul)?\s+fiscal|VAT)\s*[:#-]?\s*(RO\s*)?([0-9]{2,10})\b")
SPACE_RE = re.compile(r"\s+")
LOCAL_NETWORK_ERROR_MARKERS = (
    "network location cannot be reached",
    "network is unreachable",
    "unreachable network",
    "cannot assign requested address",
    "requested address is not valid in its context",
    "winerror 10051",
    "winerror 10055",
    "winerror 10065",
    "winerror 1231",
)
DEFAULT_RETRY_STATUSES = ("local_network_error", "worker_error")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--batch-size",
        type=int,
        help="Process at most this many pending domains, allowing a fresh process per batch.",
    )
    parser.add_argument("--workers", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=300)
    parser.add_argument("--rps", type=float, default=150.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument("--max-sitemaps", type=int, default=20)
    parser.add_argument("--max-sitemap-urls", type=int, default=10_000)
    parser.add_argument("--max-html-bytes", type=int, default=2_000_000)
    parser.add_argument("--max-sitemap-bytes", type=int, default=52_428_800)
    parser.add_argument("--retry", type=int, default=1)
    parser.add_argument(
        "--origin-only",
        action="store_true",
        help="Stop after finding a usable HTTP origin; defer sitemap/page crawling.",
    )
    parser.add_argument(
        "--final-retry-passes",
        type=int,
        default=1,
        help="After the main pass, rerun retryable site failures this many times.",
    )
    parser.add_argument(
        "--retry-status",
        action="append",
        dest="retry_statuses",
        help=(
            "Site status to retry. Repeat for multiple values. Defaults to "
            "local_network_error and worker_error."
        ),
    )
    parser.add_argument(
        "--prevent-sleep", action="store_true",
        help="On Windows, keep the system awake while this process is running.",
    )
    return parser.parse_args()


def normalized_url(value: str, base: str | None = None) -> str | None:
    try:
        candidate = urljoin(base, value) if base else value
        parts = urlsplit(candidate.strip())
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return None
        if parts.username or parts.password:
            return None
        port = parts.port
        if port and port not in {80, 443}:
            return None
        host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        netloc = host
        if port and port != (443 if parts.scheme.lower() == "https" else 80):
            netloc = f"{host}:{port}"
        path = parts.path or "/"
        return urlunsplit((parts.scheme.lower(), netloc, path, parts.query, ""))
    except (UnicodeError, ValueError):
        return None


def is_domain_host(host: str | None, domain: str) -> bool:
    return bool(host and (host == domain or host.endswith(f".{domain}")))


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def is_local_network_error(error: str | None) -> bool:
    """Identify host-machine network failures that must remain retryable."""
    value = (error or "").lower()
    return any(marker in value for marker in LOCAL_NETWORK_ERROR_MARKERS)


def retry_statuses(args: argparse.Namespace) -> tuple[str, ...]:
    values = getattr(args, "retry_statuses", None) or DEFAULT_RETRY_STATUSES
    return tuple(dict.fromkeys(values))


def origin_failure_error(fetches: list[dict[str, Any]]) -> str:
    """Return a stable, mutually exclusive reason for failed origin probes."""
    classes: set[str] = set()
    for row in fetches:
        status = row.get("status")
        if isinstance(status, int) and status >= 500:
            classes.add("http_5xx")
        error = str(row.get("error") or "")
        prefix = error.partition(":")[0].casefold()
        lowered = error.casefold()
        if prefix in {"clientconnectordnserror", "gaierror", "socket.gaierror"}:
            classes.add("dns")
        elif prefix in {
            "connectiontimeouterror", "timeouterror", "asynciotimeouterror"
        } or "timed out" in lowered:
            classes.add("timeout")
        elif prefix in {
            "clientconnectorcertificateerror", "clientsslerror", "sslerror"
        } or "certificate verify failed" in lowered:
            classes.add("tls")
        elif error:
            classes.add("transport")
    if len(classes) != 1:
        return "origin_mixed_error"
    return {
        "dns": "origin_dns_error",
        "timeout": "origin_timeout",
        "tls": "origin_tls_error",
        "http_5xx": "origin_http_5xx",
        "transport": "origin_transport_error",
    }[next(iter(classes))]


class SafeResolver(AbstractResolver):
    """Resolve only globally routable addresses."""

    def __init__(self) -> None:
        self._resolver = aiohttp.resolver.DefaultResolver()

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_UNSPEC):
        answers = await self._resolver.resolve(host, port, family)
        safe = []
        for answer in answers:
            try:
                if ipaddress.ip_address(answer["host"]).is_global:
                    safe.append(answer)
            except ValueError:
                continue
        if not safe:
            raise OSError(f"unsafe_or_unroutable_address:{host}")
        return safe

    async def close(self) -> None:
        await self._resolver.close()


class RateLimiter:
    def __init__(self, rps: float) -> None:
        self.interval = 1.0 / max(rps, 0.1)
        self.next_at = 0.0
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            now = time.monotonic()
            if self.next_at > now:
                await asyncio.sleep(self.next_at - now)
                now = time.monotonic()
            self.next_at = max(now, self.next_at) + self.interval


async def read_limited(response: aiohttp.ClientResponse, limit: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.content.iter_chunked(64 * 1024):
        remaining = limit + 1 - size
        if remaining <= 0:
            break
        chunks.append(chunk[:remaining])
        size += min(len(chunk), remaining)
        if size > limit:
            break
    body = b"".join(chunks)
    return body[:limit], len(body) > limit


class Fetcher:
    def __init__(self, session: aiohttp.ClientSession, limiter: RateLimiter, args: argparse.Namespace):
        self.session = session
        self.limiter = limiter
        self.args = args

    async def fetch(self, url: str, purpose: str, max_bytes: int) -> dict[str, Any]:
        started = time.monotonic()
        original = url
        current = url
        redirects: list[dict[str, Any]] = []
        last_error: str | None = None
        attempts = self.args.retry + 1

        for attempt in range(1, attempts + 1):
            try:
                for _ in range(8):
                    current = normalized_url(current) or ""
                    if not current:
                        raise ValueError("invalid_url")
                    await self.limiter.wait()
                    async with self.session.get(
                        current,
                        allow_redirects=False,
                        headers={
                            "User-Agent": USER_AGENT,
                            "Accept": "text/html,application/xhtml+xml,application/xml,text/xml,text/plain,application/pdf;q=0.8,*/*;q=0.2",
                            # Avoid server-side compression bombs and malformed compressed
                            # streams. The crawler already enforces byte limits on bodies.
                            "Accept-Encoding": "identity",
                        },
                    ) as response:
                        status = response.status
                        location = response.headers.get("Location")
                        if status in {301, 302, 303, 307, 308} and location:
                            target = normalized_url(location, current)
                            redirects.append({"url": current, "status": status, "location": target or location})
                            if not target:
                                raise ValueError("invalid_redirect")
                            current = target
                            continue

                        body, truncated = await read_limited(response, max_bytes)
                        result = {
                            "purpose": purpose,
                            "url": original,
                            "final_url": current,
                            "status": status,
                            "content_type": response.headers.get("Content-Type", "").split(";", 1)[0].lower(),
                            "bytes": len(body),
                            "duration_ms": round((time.monotonic() - started) * 1000, 3),
                            "sha256": hashlib.sha256(body).hexdigest() if body else None,
                            "error": None,
                            "redirects": redirects,
                            "truncated": truncated,
                            "body": body,
                            "fetched_at": utc_now(),
                        }
                        if status >= 500 and attempt < attempts:
                            last_error = f"http_{status}"
                            await asyncio.sleep(0.25 * attempt)
                            break
                        return result
                else:
                    raise ValueError("redirect_limit")
            except (
                asyncio.TimeoutError,
                aiohttp.ClientError,
                OSError,
                ssl.SSLError,
                ValueError,
                zlib.error,
            ) as exc:
                last_error = f"{type(exc).__name__}:{str(exc)[:240]}"
                if attempt < attempts:
                    await asyncio.sleep(0.25 * attempt)
                    current = original
                    redirects = []

        return {
            "purpose": purpose,
            "url": original,
            "final_url": current or original,
            "status": None,
            "content_type": None,
            "bytes": 0,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "sha256": None,
            "error": last_error,
            "redirects": redirects,
            "truncated": False,
            "body": b"",
            "fetched_at": utc_now(),
        }


def decode_body(body: bytes, content_type: str | None = None) -> str:
    if not body:
        return ""
    for encoding in ("utf-8", "windows-1250", "iso-8859-2", "latin-1"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            pass
    return body.decode("utf-8", errors="replace")


def extract_page(body: bytes, url: str, domain: str) -> dict[str, Any]:
    body = sanitize_html_bytes(body)
    soup = BeautifulSoup(body, "html.parser")
    title = SPACE_RE.sub(" ", soup.title.get_text(" ", strip=True))[:500] if soup.title else None
    html_tag = soup.find("html")
    language = (html_tag.get("lang") or "")[:40] if html_tag else None

    links: list[dict[str, Any]] = []
    for anchor in soup.find_all("a", href=True, limit=5000):
        href = str(anchor.get("href", "")).strip()
        absolute = normalized_url(href, url)
        if not absolute or not is_domain_host(urlsplit(absolute).hostname, domain):
            continue
        text = SPACE_RE.sub(" ", anchor.get_text(" ", strip=True))[:300]
        parent_names = {getattr(parent, "name", None) for parent in anchor.parents}
        location = "footer" if "footer" in parent_names else "nav" if "nav" in parent_names else "body"
        links.append({"url": absolute, "anchor": text, "location": location})

    jsonld: list[Any] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}, limit=30):
        try:
            value = json.loads(script.string or script.get_text())
            jsonld.append(value)
        except (json.JSONDecodeError, TypeError):
            continue

    for tag in soup(("script", "style", "noscript", "svg")):
        tag.decompose()
    text = SPACE_RE.sub(" ", soup.get_text(" ", strip=True))
    clipped_text = text[:500_000]

    emails = {match.lower().rstrip(".") for match in EMAIL_RE.findall(clipped_text)}
    for mail in re.findall(r"(?i)mailto:([^?\"'<>\s]+)", decode_body(body)):
        if EMAIL_RE.fullmatch(mail):
            emails.add(mail.lower())
    phones = {SPACE_RE.sub(" ", match).strip() for match in PHONE_RE.findall(clipped_text)}
    cuis = {f"RO{match[1]}" if match[0] else match[1] for match in CUI_RE.findall(clipped_text)}
    company_identity = extract_company_identity(body, url)

    result = {
        "title": title,
        "language": language or None,
        "text": clipped_text,
        "text_sha256": hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest(),
        "excerpt": clipped_text[:1500],
        "links": links,
        "emails": sorted(emails)[:100],
        "phones": sorted(phones)[:100],
        "cuis": sorted(cuis)[:100],
        "jsonld": jsonld[:30],
        "company_identity": company_identity,
    }
    soup.decompose()
    return result


def classify_candidate(url: str, anchor: str = "", location: str = "body") -> tuple[list[str], int]:
    haystack = f"{urlsplit(url).path} {anchor}".lower().replace("_", "-")
    classes = [name for name, patterns in CLASS_PATTERNS.items() if any(p in haystack for p in patterns)]
    if not classes:
        return [], 0
    score = 70 if anchor else 50
    if location == "footer":
        score += 20
    elif location == "nav":
        score += 10
    return sorted(set(classes)), score


def classify_page(url: str, title: str | None, text: str) -> list[str]:
    path_title = f"{urlsplit(url).path} {title or ''}".lower().replace("_", "-")
    classes = []
    for name, patterns in CLASS_PATTERNS.items():
        if any(pattern in path_title for pattern in patterns):
            classes.append(name)
    return sorted(set(classes))


def extract_security_text(body: bytes) -> dict[str, Any]:
    text = decode_body(body)[:32_768]
    emails = {match.lower().rstrip(".") for match in EMAIL_RE.findall(text)}
    phones = {SPACE_RE.sub(" ", match).strip() for match in PHONE_RE.findall(text)}
    return {
        "title": None,
        "language": None,
        "text": text,
        "text_sha256": hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest(),
        "excerpt": text[:1500],
        "emails": sorted(emails)[:100],
        "phones": sorted(phones)[:100],
        "cuis": [],
        "jsonld": [],
        "company_identity": {
            "url": "", "company_names": [], "cuis": [],
            "registration_numbers": [], "addresses": [],
        },
    }


def decompress_sitemap(body: bytes, limit: int) -> tuple[bytes, bool]:
    if not body.startswith(b"\x1f\x8b"):
        return body, False
    with gzip.GzipFile(fileobj=io.BytesIO(body)) as handle:
        data = handle.read(limit + 1)
    return data[:limit], len(data) > limit


def parse_sitemap(body: bytes, base_url: str) -> tuple[str, list[tuple[str, str | None]], list[str], str | None]:
    try:
        body, expanded_too_large = decompress_sitemap(body, 52_428_800)
        if expanded_too_large:
            return "unknown", [], [], "expanded_size_limit"
    except (OSError, EOFError):
        return "unknown", [], [], "invalid_gzip"

    stripped = body.lstrip()
    if not stripped:
        return "empty", [], [], None
    if not stripped.startswith(b"<"):
        urls = []
        for line in decode_body(body).splitlines():
            value = normalized_url(line.strip(), base_url)
            if value:
                urls.append((value, None))
        return "text", urls, [], None

    try:
        root = SafeET.fromstring(body)
    except Exception as exc:
        return "invalid_xml", [], [], f"{type(exc).__name__}:{str(exc)[:200]}"

    root_name = local_name(root.tag)
    if root_name == "sitemapindex":
        children = []
        for element in root.iter():
            if local_name(element.tag) == "loc" and element.text:
                value = normalized_url(element.text.strip(), base_url)
                if value:
                    children.append(value)
        return "index", [], children, None

    urls: list[tuple[str, str | None]] = []
    if root_name == "urlset":
        for item in root:
            if local_name(item.tag) != "url":
                continue
            loc = None
            lastmod = None
            for child in item:
                name = local_name(child.tag)
                if name == "loc" and child.text:
                    loc = normalized_url(child.text.strip(), base_url)
                elif name == "lastmod" and child.text:
                    lastmod = child.text.strip()[:100]
            if loc:
                urls.append((loc, lastmod))
        return "urlset", urls, [], None

    for element in root.iter():
        name = local_name(element.tag)
        if name == "link":
            value = element.attrib.get("href") or element.text
        elif name in {"loc", "guid"}:
            value = element.text
        else:
            continue
        if value:
            parsed = normalized_url(value.strip(), base_url)
            if parsed:
                urls.append((parsed, None))
    return root_name or "xml", urls, [], None


def fetch_record(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key != "body"}


def robots_policy(fetch: dict[str, Any], origin: str) -> tuple[urllib.robotparser.RobotFileParser | None, list[str], str]:
    status = fetch.get("status")
    if status is None or (500 <= status <= 599):
        return None, [], "disallow_unreachable"
    if 400 <= status <= 499:
        return None, [], "allow_unavailable"
    if not (200 <= status <= 299):
        return None, [], "disallow_other_status"
    text = decode_body(fetch["body"])
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(urljoin(origin, "/robots.txt"))
    parser.parse(text.splitlines())
    sitemaps = []
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip().lower() == "sitemap":
            url = normalized_url(value.strip(), origin)
            if url:
                sitemaps.append(url)
    return parser, list(dict.fromkeys(sitemaps)), "parsed"


async def process_domain(
    item: dict[str, str], fetcher: Fetcher, args: argparse.Namespace
) -> dict[str, Any]:
    domain = item["domain"]
    started_at = utc_now()
    fetches: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    sitemap_rows: list[dict[str, Any]] = []
    discovered_rows: dict[tuple[str, str], dict[str, Any]] = {}
    candidates: dict[str, dict[str, Any]] = {}
    site_error = None

    origin_fetch: dict[str, Any] | None = None
    origin_url = None
    probe_urls = [
        f"https://{domain}/",
        f"https://www.{domain}/",
        f"http://{domain}/",
        f"http://www.{domain}/",
    ]
    probe_tasks = [
        asyncio.create_task(
            fetcher.fetch(probe, "origin_probe", args.max_html_bytes)
        )
        for probe in probe_urls
    ]
    try:
        for completed_probe in asyncio.as_completed(probe_tasks):
            response = await completed_probe
            fetches.append(fetch_record(response))
            if response.get("status") is not None and response["status"] < 500:
                origin_fetch = response
                origin_url = response["final_url"]
                break
    finally:
        for task in probe_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*probe_tasks, return_exceptions=True)

    if not origin_fetch or not origin_url:
        local_network_failure = any(
            is_local_network_error(row.get("error")) for row in fetches
        )
        return {
            "domain": domain,
            "stratum": item.get("stratum"),
            "status": "local_network_error" if local_network_failure else "no_origin",
            "origin_url": None,
            "final_url": None,
            "started_at": started_at,
            "finished_at": utc_now(),
            "error": (
                "local_network_failure"
                if local_network_failure
                else origin_failure_error(fetches)
            ),
            "fetches": fetches,
            "pages": pages,
            "sitemaps": sitemap_rows,
            "urls": [],
        }

    if args.origin_only:
        return {
            "domain": domain,
            "stratum": item.get("stratum"),
            "status": "origin_available",
            "origin_url": origin_url,
            "final_url": origin_url,
            "started_at": started_at,
            "finished_at": utc_now(),
            "error": None,
            "fetches": fetches,
            "pages": [],
            "sitemaps": [],
            "urls": [],
        }

    origin_parts = urlsplit(origin_url)
    canonical_origin = f"{origin_parts.scheme}://{origin_parts.netloc}/"
    robots_url = urljoin(canonical_origin, "/robots.txt")
    robots_fetch = await fetcher.fetch(robots_url, "robots", 524_288)
    fetches.append(fetch_record(robots_fetch))
    robot_parser, sitemap_seeds, robots_state = robots_policy(robots_fetch, canonical_origin)

    def allowed(url: str) -> bool:
        if robots_state.startswith("disallow"):
            return False
        return True if robot_parser is None else robot_parser.can_fetch(USER_AGENT_TOKEN, url)

    homepage_data = None
    if origin_fetch["status"] and 200 <= origin_fetch["status"] < 300 and origin_fetch.get("body"):
        ctype = origin_fetch.get("content_type") or ""
        if ctype in HTML_TYPES or origin_fetch["body"].lstrip().startswith((b"<!DOCTYPE", b"<html", b"<HTML")):
            homepage_data = extract_page(origin_fetch["body"], origin_url, domain)
            pages.append({
                "url": origin_fetch["url"],
                "final_url": origin_url,
                "status": origin_fetch["status"],
                "title": homepage_data["title"],
                "language": homepage_data["language"],
                "classes": [],
                "emails": homepage_data["emails"],
                "phones": homepage_data["phones"],
                "cuis": homepage_data["cuis"],
                "jsonld": homepage_data["jsonld"],
                "company_identity": homepage_data["company_identity"],
                "text_sha256": homepage_data["text_sha256"],
                "excerpt": homepage_data["excerpt"],
                "source": "homepage",
                "score": 100,
                "fetched_at": origin_fetch["fetched_at"],
            })
            for link in homepage_data["links"]:
                classes, score = classify_candidate(link["url"], link["anchor"], link["location"])
                if not classes:
                    continue
                candidate = candidates.setdefault(link["url"], {"classes": set(), "score": 0, "source": "homepage_link"})
                candidate["classes"].update(classes)
                candidate["score"] = max(candidate["score"], score)
                discovered_rows[(link["url"], "homepage_link")] = {
                    "url": link["url"], "source": "homepage_link", "classes": classes,
                    "score": score, "lastmod": None,
                }

    if not sitemap_seeds and not robots_state.startswith("disallow"):
        sitemap_seeds = [urljoin(canonical_origin, hint) for hint in SITEMAP_HINTS]

    sitemap_queue = deque((seed, 0) for seed in sitemap_seeds)
    seen_sitemaps: set[str] = set()
    total_sitemap_urls = 0
    while sitemap_queue and len(seen_sitemaps) < args.max_sitemaps and total_sitemap_urls < args.max_sitemap_urls:
        sitemap_url, depth = sitemap_queue.popleft()
        if sitemap_url in seen_sitemaps or depth > 4 or not allowed(sitemap_url):
            continue
        seen_sitemaps.add(sitemap_url)
        sitemap_fetch = await fetcher.fetch(sitemap_url, "sitemap", args.max_sitemap_bytes)
        fetches.append(fetch_record(sitemap_fetch))
        kind = "unavailable"
        urls: list[tuple[str, str | None]] = []
        children: list[str] = []
        parse_error = sitemap_fetch.get("error")
        if sitemap_fetch.get("status") and 200 <= sitemap_fetch["status"] < 300 and sitemap_fetch.get("body"):
            kind, urls, children, parse_error = parse_sitemap(sitemap_fetch["body"], sitemap_fetch["final_url"])
        remaining = args.max_sitemap_urls - total_sitemap_urls
        urls = urls[:remaining]
        total_sitemap_urls += len(urls)
        sitemap_rows.append({
            "url": sitemap_url,
            "final_url": sitemap_fetch.get("final_url"),
            "status": sitemap_fetch.get("status"),
            "kind": kind,
            "url_count": len(urls),
            "child_count": len(children),
            "depth": depth,
            "truncated": int(bool(sitemap_fetch.get("truncated")) or len(urls) >= remaining),
            "error": parse_error,
        })
        for child in children:
            if child not in seen_sitemaps:
                sitemap_queue.append((child, depth + 1))
        for page_url, lastmod in urls:
            if not is_domain_host(urlsplit(page_url).hostname, domain):
                continue
            classes, score = classify_candidate(page_url)
            discovered_rows[(page_url, "sitemap")] = {
                "url": page_url, "source": "sitemap", "classes": classes,
                "score": score, "lastmod": lastmod,
            }
            if classes:
                candidate = candidates.setdefault(page_url, {"classes": set(), "score": 0, "source": "sitemap"})
                candidate["classes"].update(classes)
                candidate["score"] = max(candidate["score"], score + 10)

    present_classes = set().union(*(candidate["classes"] for candidate in candidates.values())) if candidates else set()
    for route, page_class in FALLBACK_ROUTES:
        if page_class in present_classes and page_class != "security_contact":
            continue
        url = urljoin(canonical_origin, f"/{route}")
        candidate = candidates.setdefault(url, {"classes": set(), "score": 0, "source": "route_guess"})
        candidate["classes"].add(page_class)
        candidate["score"] = max(candidate["score"], 40)
        discovered_rows[(url, "route_guess")] = {
            "url": url, "source": "route_guess", "classes": [page_class],
            "score": 40, "lastmod": None,
        }

    ranked = sorted(candidates.items(), key=lambda pair: (-pair[1]["score"], pair[0]))
    selected: list[tuple[str, dict[str, Any]]] = []
    covered: set[str] = set()
    for url, meta in ranked:
        adds = meta["classes"] - covered
        if adds or len(selected) < min(3, args.max_pages):
            selected.append((url, meta))
            covered.update(meta["classes"])
        if len(selected) >= args.max_pages:
            break

    for page_url, meta in selected:
        if not allowed(page_url):
            continue
        page_fetch = await fetcher.fetch(page_url, "candidate_page", args.max_html_bytes)
        fetches.append(fetch_record(page_fetch))
        if not page_fetch.get("status") or not (200 <= page_fetch["status"] < 300) or not page_fetch.get("body"):
            continue
        ctype = page_fetch.get("content_type") or ""
        is_security_text = urlsplit(page_url).path.endswith("/.well-known/security.txt")
        if is_security_text and ctype == "text/plain":
            page_data = extract_security_text(page_fetch["body"])
            classes = ["security_contact"]
        elif ctype in HTML_TYPES or page_fetch["body"].lstrip().startswith((b"<!DOCTYPE", b"<html", b"<HTML")):
            page_data = extract_page(page_fetch["body"], page_fetch["final_url"], domain)
            classes = classify_page(page_fetch["final_url"], page_data["title"], page_data["text"])
        else:
            continue
        if not classes:
            classes = sorted(meta["classes"])
        pages.append({
            "url": page_url,
            "final_url": page_fetch["final_url"],
            "status": page_fetch["status"],
            "title": page_data["title"],
            "language": page_data["language"],
            "classes": classes,
            "emails": page_data["emails"],
            "phones": page_data["phones"],
            "cuis": page_data["cuis"],
            "jsonld": page_data["jsonld"],
            "company_identity": page_data["company_identity"],
            "text_sha256": page_data["text_sha256"],
            "excerpt": page_data["excerpt"],
            "source": meta["source"],
            "score": meta["score"],
            "soft_404": 0,
            "fetched_at": page_fetch["fetched_at"],
        })

    guessed_by_hash: dict[str, list[dict[str, Any]]] = {}
    normalized_origin = normalized_url(origin_url)
    for page in pages:
        if page["source"] != "route_guess":
            continue
        if page.get("text_sha256"):
            guessed_by_hash.setdefault(page["text_sha256"], []).append(page)
        if normalized_url(page.get("final_url") or "") == normalized_origin:
            page["soft_404"] = 1
    for duplicate_pages in guessed_by_hash.values():
        if len(duplicate_pages) >= 2:
            for page in duplicate_pages:
                page["soft_404"] = 1
    for page in pages:
        if page.get("soft_404"):
            page["classes"] = []
            page["emails"] = []
            page["phones"] = []
            page["cuis"] = []
            page["jsonld"] = []
            page["company_identity"] = {
                "url": page.get("final_url") or page.get("url") or "", "company_names": [],
                "cuis": [], "registration_numbers": [], "addresses": [],
            }

    if robots_state.startswith("disallow"):
        status = "robots_blocked"
        site_error = robots_state
    else:
        status = "complete"

    return {
        "domain": domain,
        "stratum": item.get("stratum"),
        "status": status,
        "origin_url": canonical_origin,
        "final_url": origin_url,
        "started_at": started_at,
        "finished_at": utc_now(),
        "error": site_error,
        "fetches": fetches,
        "pages": pages,
        "sitemaps": sitemap_rows,
        "urls": list(discovered_rows.values()),
    }


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS crawl_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    configuration_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sites (
    domain TEXT PRIMARY KEY,
    stratum TEXT,
    status TEXT NOT NULL,
    origin_url TEXT,
    final_url TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    error TEXT,
    fetch_count INTEGER NOT NULL,
    page_count INTEGER NOT NULL,
    sitemap_count INTEGER NOT NULL,
    sitemap_url_count INTEGER NOT NULL,
    discovered_url_count INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS fetches (
    fetch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    purpose TEXT NOT NULL,
    url TEXT NOT NULL,
    final_url TEXT,
    status INTEGER,
    content_type TEXT,
    bytes INTEGER NOT NULL,
    duration_ms REAL NOT NULL,
    sha256 TEXT,
    error TEXT,
    redirects_json TEXT NOT NULL,
    truncated INTEGER NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sitemaps (
    sitemap_id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    url TEXT NOT NULL,
    final_url TEXT,
    status INTEGER,
    kind TEXT NOT NULL,
    url_count INTEGER NOT NULL,
    child_count INTEGER NOT NULL,
    depth INTEGER NOT NULL,
    truncated INTEGER NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS discovered_urls (
    domain TEXT NOT NULL,
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    classes_json TEXT NOT NULL,
    score INTEGER NOT NULL,
    lastmod TEXT,
    PRIMARY KEY(domain, url, source)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS pages (
    page_id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    url TEXT NOT NULL,
    final_url TEXT,
    status INTEGER,
    title TEXT,
    language TEXT,
    classes_json TEXT NOT NULL,
    emails_json TEXT NOT NULL,
    phones_json TEXT NOT NULL,
    cuis_json TEXT NOT NULL,
    jsonld_json TEXT NOT NULL,
    company_identity_json TEXT NOT NULL DEFAULT '{}',
    text_sha256 TEXT,
    excerpt TEXT,
    source TEXT NOT NULL,
    score INTEGER NOT NULL,
    soft_404 INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fetches_domain ON fetches(domain);
CREATE INDEX IF NOT EXISTS idx_fetches_status ON fetches(status);
CREATE INDEX IF NOT EXISTS idx_pages_domain ON pages(domain);
CREATE INDEX IF NOT EXISTS idx_sitemaps_domain ON sitemaps(domain);
"""


def open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    site_columns = {row[1] for row in connection.execute("PRAGMA table_info(sites)")}
    if "discovered_url_count" not in site_columns:
        connection.execute(
            "ALTER TABLE sites ADD COLUMN discovered_url_count INTEGER NOT NULL DEFAULT 0"
        )
    page_columns = {row[1] for row in connection.execute("PRAGMA table_info(pages)")}
    if "soft_404" not in page_columns:
        connection.execute("ALTER TABLE pages ADD COLUMN soft_404 INTEGER NOT NULL DEFAULT 0")
    if "company_identity_json" not in page_columns:
        connection.execute("ALTER TABLE pages ADD COLUMN company_identity_json TEXT NOT NULL DEFAULT '{}'")
    return connection


def write_result(connection: sqlite3.Connection, result: dict[str, Any]) -> None:
    domain = result["domain"]
    with connection:
        connection.execute("DELETE FROM fetches WHERE domain = ?", (domain,))
        connection.execute("DELETE FROM sitemaps WHERE domain = ?", (domain,))
        connection.execute("DELETE FROM discovered_urls WHERE domain = ?", (domain,))
        connection.execute("DELETE FROM pages WHERE domain = ?", (domain,))
        connection.execute(
            """
            INSERT OR REPLACE INTO sites
            (domain, stratum, status, origin_url, final_url, started_at, finished_at, error,
             fetch_count, page_count, sitemap_count, sitemap_url_count, discovered_url_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                domain, result.get("stratum"), result["status"], result.get("origin_url"),
                result.get("final_url"), result["started_at"], result["finished_at"], result.get("error"),
                len(result["fetches"]), len(result["pages"]), len(result["sitemaps"]),
                sum(row["url_count"] for row in result["sitemaps"]), len(result["urls"]),
            ),
        )
        connection.executemany(
            """
            INSERT INTO fetches
            (domain, purpose, url, final_url, status, content_type, bytes, duration_ms, sha256,
             error, redirects_json, truncated, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    domain, row["purpose"], row["url"], row.get("final_url"), row.get("status"),
                    row.get("content_type"), row["bytes"], row["duration_ms"], row.get("sha256"),
                    row.get("error"), json_text(row.get("redirects", [])), int(bool(row.get("truncated"))),
                    row["fetched_at"],
                )
                for row in result["fetches"]
            ],
        )
        connection.executemany(
            """
            INSERT INTO sitemaps
            (domain, url, final_url, status, kind, url_count, child_count, depth, truncated, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    domain, row["url"], row.get("final_url"), row.get("status"), row["kind"],
                    row["url_count"], row["child_count"], row["depth"], row["truncated"], row.get("error"),
                )
                for row in result["sitemaps"]
            ],
        )
        connection.executemany(
            """
            INSERT OR REPLACE INTO discovered_urls
            (domain, url, source, classes_json, score, lastmod)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (domain, row["url"], row["source"], json_text(row["classes"]), row["score"], row.get("lastmod"))
                for row in result["urls"]
            ],
        )
        connection.executemany(
            """
            INSERT INTO pages
            (domain, url, final_url, status, title, language, classes_json, emails_json, phones_json,
             cuis_json, jsonld_json, company_identity_json, text_sha256, excerpt, source, score,
             soft_404, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    domain, row["url"], row.get("final_url"), row.get("status"), row.get("title"),
                    row.get("language"), json_text(row["classes"]), json_text(row["emails"]),
                    json_text(row["phones"]), json_text(row["cuis"]), json_text(row["jsonld"]),
                    json_text(row.get("company_identity", {})),
                    row.get("text_sha256"), row.get("excerpt"), row["source"], row["score"],
                    int(bool(row.get("soft_404"))), row["fetched_at"],
                )
                for row in result["pages"]
            ],
        )


async def run(args: argparse.Namespace) -> None:
    if args.prevent_sleep and hasattr(ctypes, "windll"):
        # ES_CONTINUOUS | ES_SYSTEM_REQUIRED. The assertion ends with the process.
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    def iter_items():
        with args.sample.open("r", encoding="utf-8", newline="") as handle:
            for index, item in enumerate(csv.DictReader(handle)):
                if args.limit is not None and index >= args.limit:
                    break
                yield item

    selected_count = sum(1 for _ in iter_items())

    connection = open_database(args.database)
    retryable_statuses = retry_statuses(args)
    placeholders = ",".join("?" for _ in retryable_statuses)
    completed = {
        row[0]
        for row in connection.execute(
            "SELECT domain FROM sites "
            f"WHERE status NOT IN ({placeholders})",
            retryable_statuses,
        )
    }
    remaining_count = sum(1 for item in iter_items() if item["domain"] not in completed)
    pending_count = (
        min(remaining_count, args.batch_size)
        if args.batch_size is not None
        else remaining_count
    )
    run_id = connection.execute(
        "INSERT INTO crawl_runs(started_at, configuration_json) VALUES (?, ?)",
        (utc_now(), json_text(vars(args) | {"sample": str(args.sample), "database": str(args.database)})),
    ).lastrowid
    connection.commit()
    print(
        f"Selected={selected_count:,} completed={len(completed):,} "
        f"remaining={remaining_count:,} batch={pending_count:,}",
        flush=True,
    )
    if not pending_count:
        connection.execute("UPDATE crawl_runs SET finished_at = ? WHERE run_id = ?", (utc_now(), run_id))
        connection.commit()
        connection.close()
        return

    timeout = aiohttp.ClientTimeout(total=args.timeout, connect=min(8.0, args.timeout), sock_read=args.timeout)
    resolver = SafeResolver()
    connector = aiohttp.TCPConnector(
        limit=args.concurrency,
        limit_per_host=1,
        ttl_dns_cache=300,
        resolver=resolver,
        ssl=ssl.create_default_context(),
        enable_cleanup_closed=True,
    )
    limiter = RateLimiter(args.rps)
    queue: asyncio.Queue[dict[str, str] | None] = asyncio.Queue(maxsize=max(args.workers * 4, 500))
    output: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=10)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=False) as session:
        fetcher = Fetcher(session, limiter, args)

        async def producer() -> None:
            queued = 0
            for item in iter_items():
                if item["domain"] not in completed:
                    await queue.put(item)
                    queued += 1
                    if queued >= pending_count:
                        break
            for _ in range(args.workers):
                await queue.put(None)

        async def worker() -> None:
            while True:
                item = await queue.get()
                if item is None:
                    queue.task_done()
                    return
                try:
                    result = await process_domain(item, fetcher, args)
                except Exception as exc:
                    error_text = f"{type(exc).__name__}:{str(exc)[:500]}"
                    status = (
                        "content_decode_error"
                        if "decompressing data" in error_text.lower()
                        else "worker_error"
                    )
                    result = {
                        "domain": item["domain"], "stratum": item.get("stratum"), "status": status,
                        "origin_url": None, "final_url": None, "started_at": utc_now(), "finished_at": utc_now(),
                        "error": error_text, "fetches": [], "pages": [],
                        "sitemaps": [], "urls": [],
                    }
                await output.put(result)
                queue.task_done()

        async def writer() -> None:
            started = time.monotonic()
            done = 0
            while True:
                result = await output.get()
                if result is None:
                    output.task_done()
                    return
                try:
                    write_result(connection, result)
                except Exception as exc:
                    print(
                        f"writer_error domain={result.get('domain')} "
                        f"error={type(exc).__name__}:{str(exc)[:500]}",
                        file=sys.stderr,
                        flush=True,
                    )
                    fallback = {
                        "domain": result["domain"],
                        "stratum": result.get("stratum"),
                        "status": "worker_error",
                        "origin_url": None,
                        "final_url": None,
                        "started_at": result.get("started_at", utc_now()),
                        "finished_at": utc_now(),
                        "error": f"writer_error:{type(exc).__name__}:{str(exc)[:400]}",
                        "fetches": [],
                        "pages": [],
                        "sitemaps": [],
                        "urls": [],
                    }
                    try:
                        write_result(connection, fallback)
                    except Exception as fallback_exc:
                        print(
                            f"writer_fallback_error domain={result.get('domain')} "
                            f"error={type(fallback_exc).__name__}:{str(fallback_exc)[:500]}",
                            file=sys.stderr,
                            flush=True,
                        )
                done += 1
                if done % 100 == 0:
                    gc.collect()
                if done % 25 == 0 or done == pending_count:
                    elapsed = max(time.monotonic() - started, 0.001)
                    rate = done / elapsed
                    eta = (pending_count - done) / rate if rate else 0
                    counts = dict(connection.execute("SELECT status, COUNT(*) FROM sites GROUP BY status"))
                    print(
                        f"done={done:,}/{pending_count:,} sites_per_s={rate:.2f} eta_min={eta/60:.1f} statuses={counts}",
                        flush=True,
                    )
                output.task_done()

        worker_tasks = [asyncio.create_task(worker()) for _ in range(args.workers)]
        writer_task = asyncio.create_task(writer())
        producer_task = asyncio.create_task(producer())
        await producer_task
        await queue.join()
        await asyncio.gather(*worker_tasks)
        await output.join()
        await output.put(None)
        await writer_task

    connection.execute("UPDATE crawl_runs SET finished_at = ? WHERE run_id = ?", (utc_now(), run_id))
    connection.commit()
    connection.close()


def main() -> None:
    args = parse_args()
    if args.workers > args.concurrency:
        args.workers = args.concurrency
    for pass_index in range(args.final_retry_passes + 1):
        asyncio.run(run(args))
        connection = open_database(args.database)
        statuses = retry_statuses(args)
        placeholders = ",".join("?" for _ in statuses)
        retryable = connection.execute(
            f"SELECT COUNT(*) FROM sites WHERE status IN ({placeholders})",
            statuses,
        ).fetchone()[0]
        connection.close()
        if not retryable:
            break
        if pass_index >= args.final_retry_passes:
            print(
                f"Retryable failures remaining after final pass: {retryable:,}",
                flush=True,
            )
            break
        print(
            f"Starting final retry pass {pass_index + 1}/"
            f"{args.final_retry_passes} for {retryable:,} domains",
            flush=True,
        )


if __name__ == "__main__":
    main()
