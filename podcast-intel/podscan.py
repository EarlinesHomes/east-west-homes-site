"""Thin Podscan client. Every Podscan-specific detail lives here.

VERIFY endpoint paths and field names against https://podscan.fm/docs before the
first live run. Paths come from config.yaml, and field lookups go through `_pick`, which
tries several likely names, so small API differences only need edits in this file.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import requests


def _pick(d: dict, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return default


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class Podscan:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        key = os.environ.get("PODSCAN_API_KEY")
        if not key:
            raise SystemExit("Set PODSCAN_API_KEY (environment variable) to use Podscan.")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {key}", "Accept": "application/json"})

    def _get(self, path: str, **params) -> dict:
        r = self.s.get(self.cfg["base_url"] + path, params=params, timeout=60)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _episodes(payload: dict) -> list[dict]:
        if isinstance(payload, list):
            return payload
        return _pick(payload, "episodes", "data", "results", default=[]) or []

    @staticmethod
    def normalize(ep: dict, show_name: str = "") -> dict:
        podcast = ep.get("podcast") or {}
        return {
            "id": str(_pick(ep, "episode_id", "id")),
            "title": _pick(ep, "episode_title", "title", default="(untitled)"),
            "show": _pick(podcast, "podcast_name", "name", default=show_name),
            "podcast_id": _pick(podcast, "podcast_id", "id", default=_pick(ep, "podcast_id")),
            "posted_at": _parse_dt(_pick(ep, "posted_at", "published_at", "created_at")),
            "url": _pick(ep, "episode_url", "url", "audio_url", default=""),
            "transcript": _pick(ep, "episode_transcript", "transcript", default=""),
        }

    def new_episodes(self, podcast_id: str, since: datetime, show_name: str = "") -> list[dict]:
        path = self.cfg["episodes_for_podcast"].format(podcast_id=podcast_id)
        payload = self._get(path, order_by="posted_at", order_dir="desc", per_page=25)
        out = []
        for raw in self._episodes(payload):
            ep = self.normalize(raw, show_name)
            if ep["posted_at"] and ep["posted_at"] >= since:
                out.append(ep)
        return out

    def with_transcript(self, ep: dict) -> dict:
        if ep.get("transcript"):
            return ep
        payload = self._get(self.cfg["episode"].format(episode_id=ep["id"]))
        full = self.normalize(_pick(payload, "episode", "data", default=payload), ep["show"])
        ep["transcript"] = full["transcript"]
        return ep

    def search_episodes(self, query: str, per_page: int = 25) -> list[dict]:
        payload = self._get(self.cfg["search_episodes"], query=query, per_page=per_page)
        return [self.normalize(e) for e in self._episodes(payload)]
