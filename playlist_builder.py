#!/usr/bin/env python3
"""Compile multiple upstream M3U playlists into one deterministic TiviMate playlist."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BUILDER_VERSION = 1
USER_AGENT = "tivimate-playlist-builder/1.0 (+https://github.com/)"
MAX_SOURCE_BYTES = 25 * 1024 * 1024
TRANSIENT_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}
GROUP_ATTRIBUTE_RE = re.compile(
    r"\s+group-title\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s,]+)",
    flags=re.IGNORECASE,
)
TVG_NAME_RE = re.compile(
    r"\s+tvg-name\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s,]+))",
    flags=re.IGNORECASE,
)
TVG_ID_RE = re.compile(
    r"\s+tvg-id\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s,]+))",
    flags=re.IGNORECASE,
)


class BuildError(RuntimeError):
    """Raised when a source cannot be safely incorporated."""


@dataclass(frozen=True)
class Entry:
    """One M3U channel entry and its attached directives."""

    name: str
    lines: tuple[str, ...]
    source_url: str
    source_index: int
    entry_index: int


@dataclass(frozen=True)
class SourceResult:
    """Parsed source plus audit information."""

    url: str
    sha256: str
    byte_count: int
    entries: tuple[Entry, ...]


def first_match_value(regex: re.Pattern[str], text: str) -> str:
    match = regex.search(text)
    if not match:
        return ""
    for value in match.groups():
        if value is not None:
            return value.strip()
    return ""


def split_extinf(line: str) -> tuple[str, str]:
    """Split an EXTINF line at the first comma outside a quoted attribute."""
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote is not None:
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == ",":
            return line[:index], line[index + 1 :]
    raise BuildError(f"Malformed EXTINF line without a display-name comma: {line[:180]}")


def rewrite_extinf_group(line: str, group_name: str) -> tuple[str, str]:
    metadata, display_name = split_extinf(line)
    replacement = f' group-title="{group_name}"'
    if GROUP_ATTRIBUTE_RE.search(metadata):
        metadata = GROUP_ATTRIBUTE_RE.sub(replacement, metadata, count=1)
    else:
        metadata += replacement

    name = display_name.strip()
    if not name:
        name = first_match_value(TVG_NAME_RE, metadata)
    if not name:
        name = first_match_value(TVG_ID_RE, metadata)
    if not name:
        name = "Unnamed channel"
    return f"{metadata},{display_name}", name


def alphabetical_key(name: str) -> tuple[tuple[int, object], ...]:
    """Case-insensitive, accent-insensitive natural alphabetical key."""
    normalized = unicodedata.normalize("NFKD", name.casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.strip()
    parts = re.split(r"(\d+)", normalized)
    key: list[tuple[int, object]] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return tuple(key)


def download_source(url: str, attempts: int = 5, timeout_seconds: int = 45) -> bytes:
    """Download one source with bounded retries and no silent partial success."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/plain, application/vnd.apple.mpegurl, */*;q=0.1",
                "Cache-Control": "no-cache",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                data = response.read(MAX_SOURCE_BYTES + 1)
                if len(data) > MAX_SOURCE_BYTES:
                    raise BuildError(
                        f"Source exceeds the {MAX_SOURCE_BYTES // (1024 * 1024)} MiB safety limit: {url}"
                    )
                if not data:
                    raise BuildError(f"Source returned an empty file: {url}")
                return data
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in TRANSIENT_HTTP_CODES:
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_error = exc

        if attempt < attempts:
            time.sleep(min(2 ** (attempt - 1), 12))

    raise BuildError(f"Unable to download source after {attempts} attempts: {url}: {last_error}")


def parse_source(data: bytes, url: str, source_index: int, group_name: str) -> SourceResult:
    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise BuildError(f"Source is not valid UTF-8: {url}: {exc}") from exc

    lines = [line.rstrip("\r") for line in text.splitlines()]
    first_nonempty = next((line.strip() for line in lines if line.strip()), "")
    if not first_nonempty.startswith("#EXTM3U"):
        raise BuildError(f"Source is not an extended M3U playlist: {url}")

    entries: list[Entry] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line.startswith("#EXTINF"):
            index += 1
            continue

        rewritten_extinf, name = rewrite_extinf_group(lines[index].strip(), group_name)
        block = [rewritten_extinf]
        index += 1
        stream_url: str | None = None

        while index < len(lines):
            candidate = lines[index].strip()
            index += 1
            if not candidate:
                continue
            if candidate.startswith("#EXTINF"):
                raise BuildError(f"Channel entry has no stream URL before the next EXTINF: {url}")
            if candidate.startswith("#"):
                if candidate.upper().startswith("#EXTGRP:"):
                    candidate = f"#EXTGRP:{group_name}"
                block.append(candidate)
                continue
            stream_url = candidate
            block.append(stream_url)
            break

        if stream_url is None:
            raise BuildError(f"Channel entry has no stream URL at end of source: {url}")

        entries.append(
            Entry(
                name=name,
                lines=tuple(block),
                source_url=url,
                source_index=source_index,
                entry_index=len(entries),
            )
        )

    if not entries:
        raise BuildError(f"Source contains no channel entries: {url}")

    return SourceResult(
        url=url,
        sha256=hashlib.sha256(data).hexdigest(),
        byte_count=len(data),
        entries=tuple(entries),
    )


def validate_config(config: dict[str, Any]) -> None:
    epg_url = config.get("epg_url")
    groups = config.get("groups")
    if not isinstance(epg_url, str) or not epg_url.startswith(("http://", "https://")):
        raise BuildError("Configuration requires a valid epg_url")
    if not isinstance(groups, list) or not groups:
        raise BuildError("Configuration requires a non-empty groups list")

    seen_groups: set[str] = set()
    seen_urls: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            raise BuildError("Every group must be an object")
        name = group.get("name")
        sources = group.get("sources")
        if not isinstance(name, str) or not name.strip():
            raise BuildError("Every group requires a non-empty name")
        if name in seen_groups:
            raise BuildError(f"Duplicate group name: {name}")
        seen_groups.add(name)
        if not isinstance(sources, list) or not sources:
            raise BuildError(f"Group has no sources: {name}")
        for url in sources:
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                raise BuildError(f"Invalid source URL in group {name}: {url!r}")
            if url in seen_urls:
                raise BuildError(f"Source URL is configured more than once: {url}")
            seen_urls.add(url)


def build(config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    validate_config(config)
    epg_url: str = config["epg_url"]
    groups: list[dict[str, Any]] = config["groups"]

    output_lines = [f'#EXTM3U url-tvg="{epg_url}" x-tvg-url="{epg_url}"']
    manifest_sources: list[dict[str, Any]] = []
    manifest_groups: list[dict[str, Any]] = []
    source_index = 0
    total_channels = 0

    for group_position, group in enumerate(groups, start=1):
        group_name: str = group["name"]
        group_entries: list[Entry] = []
        group_sources: list[str] = group["sources"]

        for url in group_sources:
            raw = download_source(url)
            result = parse_source(raw, url, source_index, group_name)
            source_index += 1
            group_entries.extend(result.entries)
            manifest_sources.append(
                {
                    "group": group_name,
                    "url": result.url,
                    "sha256": result.sha256,
                    "bytes": result.byte_count,
                    "channels": len(result.entries),
                }
            )
            print(f"Fetched {len(result.entries):5d} channels: {url}", file=sys.stderr)

        group_entries.sort(
            key=lambda entry: (
                alphabetical_key(entry.name),
                entry.source_index,
                entry.entry_index,
            )
        )
        for entry in group_entries:
            output_lines.extend(entry.lines)

        total_channels += len(group_entries)
        manifest_groups.append(
            {
                "position": group_position,
                "name": group_name,
                "source_count": len(group_sources),
                "channel_count": len(group_entries),
            }
        )

    playlist = "\n".join(output_lines) + "\n"
    manifest = {
        "builder_version": BUILDER_VERSION,
        "epg_url": epg_url,
        "group_count": len(groups),
        "source_count": len(manifest_sources),
        "channel_count": total_channels,
        "playlist_sha256": hashlib.sha256(playlist.encode("utf-8")).hexdigest(),
        "groups": manifest_groups,
        "sources": manifest_sources,
    }
    return playlist, manifest


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("sources.json"))
    parser.add_argument("--output", type=Path, default=Path("output/playlist.m3u"))
    parser.add_argument("--manifest", type=Path, default=Path("output/manifest.json"))
    args = parser.parse_args()

    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        playlist, manifest = build(config)
        if manifest["source_count"] != 46 or manifest["group_count"] != 18:
            raise BuildError(
                "Safety check failed: expected exactly 46 sources and 18 groups, "
                f"got {manifest['source_count']} sources and {manifest['group_count']} groups"
            )
        atomic_write(args.output, playlist)
        atomic_write(args.manifest, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        print(
            f"Built {manifest['channel_count']} channels from "
            f"{manifest['source_count']} sources in {manifest['group_count']} groups.",
            file=sys.stderr,
        )
        return 0
    except (BuildError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
