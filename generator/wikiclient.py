"""Thin, polite client for the Old School RuneScape Wiki API.

Every call carries a descriptive User-Agent with a contact address, requests are
serialised with a small delay, and responses are cached on disk so repeated
pipeline runs do not re-fetch unchanged pages.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import requests

API = "https://oldschool.runescape.wiki/api.php"

USER_AGENT = (
    "osrs-bank-tag-layouts/0.1 "
    "(+https://github.com/cha-ndler/osrs-bank-tag-layouts; "
    "contact: 48898494+cha-ndler@users.noreply.github.com)"
)

CACHE_DIR = Path(__file__).parent / "cache"

# The wiki is a volunteer-run service. One request at a time, with a pause.
REQUEST_DELAY_SECONDS = 0.34


class WikiError(RuntimeError):
    pass


class WikiClient:
    def __init__(self, use_cache: bool = True, cache_dir: Path | None = None) -> None:
        self.use_cache = use_cache
        self.cache_dir = cache_dir or CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._last_request = 0.0
        self.stats = {"http": 0, "cache": 0}

    # -- internals ---------------------------------------------------------

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < REQUEST_DELAY_SECONDS:
            time.sleep(REQUEST_DELAY_SECONDS - elapsed)
        self._last_request = time.monotonic()

    def _cache_path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def _request(self, params: dict[str, Any], method: str = "GET") -> dict:
        params = {**params, "format": "json", "formatversion": 2}
        cache_key = f"{method}:{json.dumps(params, sort_keys=True)}"
        path = self._cache_path(cache_key)

        if self.use_cache and path.exists():
            self.stats["cache"] += 1
            return json.loads(path.read_text(encoding="utf-8"))

        last_error: Exception | None = None
        for attempt in range(4):
            self._throttle()
            try:
                if method == "POST":
                    resp = self.session.post(API, data=params, timeout=45)
                else:
                    resp = self.session.get(API, params=params, timeout=45)
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:  # network flake, 5xx, bad JSON
                last_error = exc
                time.sleep(1.5 * (attempt + 1))
                continue

            if "error" in payload:
                # API-level errors are deterministic; retrying will not help.
                raise WikiError(
                    f"{payload['error'].get('code')}: {payload['error'].get('info')}"
                )

            path.write_text(json.dumps(payload), encoding="utf-8")
            self.stats["http"] += 1
            return payload

        raise WikiError(f"request failed after retries: {last_error}")

    # -- public API --------------------------------------------------------

    def embedded_in(self, template: str, namespace: int | None = None) -> list[str]:
        """Every page that transcludes `template`, following continuations.

        Pass `namespace=0` to keep article space only. Personal sandbox drafts
        under `User:` also end in "/Strategies" and are not wiki-endorsed meta.
        """
        titles: list[str] = []
        cont: str | None = None
        while True:
            params: dict[str, Any] = {
                "action": "query",
                "list": "embeddedin",
                "eititle": template,
                "eilimit": 500,
            }
            if namespace is not None:
                params["einamespace"] = namespace
            if cont:
                params["eicontinue"] = cont
            payload = self._request(params)
            titles.extend(p["title"] for p in payload["query"]["embeddedin"])
            cont = payload.get("continue", {}).get("eicontinue")
            if not cont:
                return titles

    def page_wikitext(self, title: str) -> tuple[str, int]:
        """Raw wikitext plus the revision id it came from."""
        payload = self._request(
            {"action": "parse", "page": title, "prop": "wikitext|revid"}
        )
        parse = payload["parse"]
        return parse["wikitext"], int(parse.get("revid", 0))

    def expand_templates(self, text: str) -> str:
        """Render wikitext server-side. Used to run Module:Loadout for us."""
        payload = self._request(
            {
                "action": "expandtemplates",
                "text": text,
                "prop": "wikitext",
                "title": "Sandbox",
            },
            method="POST",
        )
        return payload["expandtemplates"]["wikitext"]

    def bucket(self, query: str) -> list[dict]:
        """Query the wiki's structured `bucket` store (Lua-style syntax)."""
        payload = self._request({"action": "bucket", "query": query})
        return payload.get("bucket", []) or []
