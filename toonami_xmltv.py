#!/usr/bin/env python3
"""Toonami Aftermath four-channel XMLTV guide for Jellyfin (Python 3, stdlib only).

API response: [{"name": "Toonami Aftermath East", "media": [{"name": ...,
"startDate": "...Z", "info": {...}}, ...]}, ...]

Use: python3 toonami_xmltv.py generate | serve | all
For offline verification: python3 toonami_xmltv.py generate --input-json response.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

API_BASE = os.getenv("TA_API_BASE", "https://api.toonamiaftermath.com").rstrip("/")
OUTPUT_DIR = Path(os.getenv("TA_OUTPUT_DIR", "/opt/toonami-xmltv/www"))
XMLTV_FILE = OUTPUT_DIR / "toonami.xml"
DIAGNOSTIC_FILE = OUTPUT_DIR / "last-api-response.json"
HOST = os.getenv("TA_HTTP_HOST", "0.0.0.0")
PORT = int(os.getenv("TA_HTTP_PORT", "8787"))
HORIZON_HOURS = max(1, int(os.getenv("TA_HORIZON_HOURS", "24")))
# Multiple requests are needed because each API response supplies only ~10 future shows.
# Keep probes closer together than the returned schedule's length.
SAMPLE_MINUTES = max(15, int(os.getenv("TA_SAMPLE_MINUTES", "180")))
REQUEST_DELAY = max(0.0, float(os.getenv("TA_REQUEST_DELAY", "0.5")))
HTTP_TIMEOUT = max(1, int(os.getenv("TA_HTTP_TIMEOUT", "20")))

CHANNELS = (
    ("Toonami Aftermath East", "toonami-aftermath-east", "Toonami Aftermath"),
    ("Toonami Aftermath West", "toonami-aftermath-west", "Toonami Aftermath"),
    ("Snickelodeon East", "snickelodeon-east", "Snickelodeon"),
    ("Snickelodeon West", "snickelodeon-west", "Snickelodeon"),
    ("Movies", "toonami-aftermath-movies", "Movies"),
)
CHANNEL_BY_NAME = {name.casefold(): (cid, group) for name, cid, group in CHANNELS}
LOG = logging.getLogger("toonami-xmltv")
logging.basicConfig(level=os.getenv("TA_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_start(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def read_api(at: datetime):
    query = urllib.parse.urlencode({"startDate": iso_utc(at)})
    url = API_BASE + "/channelsCurrentMedia?" + query
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "ToonamiXMLTV/2.0 Jellyfin EPG",
    })
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8-sig"))


def collect(payload, records):
    """Read top-level channel.media records, not recursively sampled metadata."""
    if not isinstance(payload, list):
        raise ValueError("Expected a top-level JSON array of channels")
    for channel in payload:
        if not isinstance(channel, dict):
            continue
        match = CHANNEL_BY_NAME.get(str(channel.get("name", "")).strip().casefold())
        if not match:
            continue
        channel_id, group = match
        media = channel.get("media", [])
        if not isinstance(media, list):
            continue
        for item in media:
            if not isinstance(item, dict):
                continue
            start = parse_start(item.get("startDate"))
            if start is None:
                continue
            info = item.get("info") if isinstance(item.get("info"), dict) else {}
            title = info.get("fullname") or item.get("name")
            if not isinstance(title, str) or not title.strip():
                continue
            sub_title = info.get("episode")
            image = info.get("image")
            entry = {
                "start": start,
                "title": title.strip(),
                "subtitle": sub_title.strip() if isinstance(sub_title, str) else None,
                "episode_number": item.get("episodeNumber"),
                "block": item.get("blockName"),
                "type": item.get("mediaType"),
                "year": info.get("year"),
                "image": image if isinstance(image, str) and image.startswith(("http://", "https://")) else None,
                "group": group,
            }
            # Deduplicate overlapping calls; prefer the entry with more metadata.
            existing = records[channel_id].get(start)
            if existing is None or sum(bool(v) for v in entry.values()) > sum(bool(v) for v in existing.values()):
                records[channel_id][start] = entry


def collect_schedule(start, end, fixture=None):
    records = {cid: {} for _, cid, _ in CHANNELS}
    if fixture:
        with open(fixture, "r", encoding="utf-8-sig") as f:
            collect(json.load(f), records)
        return records

    probe = start
    successes = 0
    while probe <= end:
        try:
            payload = read_api(probe)
            if successes == 0:
                OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                DIAGNOSTIC_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            collect(payload, records)
            successes += 1
        except Exception as exc:
            LOG.warning("API request at %s failed: %s", iso_utc(probe), exc)
        probe += timedelta(minutes=SAMPLE_MINUTES)
        if probe <= end and REQUEST_DELAY:
            time.sleep(REQUEST_DELAY)
    if successes == 0:
        raise RuntimeError("Every API request failed; refusing to overwrite the existing XMLTV guide")
    return records


def xml_stamp(dt):
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def add_text(parent, tag, value, **attributes):
    if value is None or str(value).strip() == "":
        return
    node = ET.SubElement(parent, tag, attributes)
    node.text = str(value)


def build_xml(records, start, end):
    root = ET.Element("tv", {
        "source-info-name": "Toonami Aftermath API",
        "source-info-url": API_BASE,
        "generator-info-name": "toonami-xmltv-generator 2.0",
    })
    for name, cid, _ in CHANNELS:
        channel = ET.SubElement(root, "channel", {"id": cid})
        add_text(channel, "display-name", name)

    count = 0
    for name, cid, group in CHANNELS:
        entries = sorted(records[cid].values(), key=lambda p: p["start"])
        for i, p in enumerate(entries):
            # For an event without a following event, do not fabricate a stop time.
            # XMLTV allows omitted stop; Jellyfin may display it without a duration.
            next_start = entries[i + 1]["start"] if i + 1 < len(entries) else None
            if p["start"] > end or (next_start is not None and next_start < start):
                continue
            attrs = {"channel": cid, "start": xml_stamp(p["start"])}
            if next_start is not None and next_start > p["start"]:
                attrs["stop"] = xml_stamp(next_start)
            prog = ET.SubElement(root, "programme", attrs)
            add_text(prog, "title", p["title"], lang="en")
            add_text(prog, "sub-title", p["subtitle"], lang="en")
            add_text(prog, "category", "Children's" if cid.startswith("snickelodeon") else group, lang="en")
            if p["block"]:
                add_text(prog, "desc", "Programming block: " + str(p["block"]), lang="en")
            if str(p["episode_number"]).isdigit():
                add_text(prog, "episode-num", str(p["episode_number"]), system="onscreen")
            if isinstance(p["year"], int) and 1900 <= p["year"] <= 2100:
                add_text(prog, "date", str(p["year"]))
            if p["image"]:
                ET.SubElement(prog, "icon", {"src": p["image"]})
            count += 1
        LOG.info("%s: %d fetched events", name, len(entries))
    return ET.ElementTree(root), count


def write_xml(tree):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="toonami-", suffix=".xml", dir=OUTPUT_DIR)
    os.close(fd)
    try:
        tree.write(tmp, encoding="utf-8", xml_declaration=True)
        ET.parse(tmp)
        os.replace(tmp, XMLTV_FILE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def generate(fixture=None):
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=1)
    end = now + timedelta(hours=HORIZON_HOURS)
    if fixture:
        # The fixture is historical, so use its event timestamps for an offline test.
        raw = json.loads(Path(fixture).read_text(encoding="utf-8-sig"))
        dates = [parse_start(m.get("startDate")) for c in raw if isinstance(c, dict)
                 for m in c.get("media", []) if isinstance(m, dict)]
        dates = [d for d in dates if d]
        if dates:
            start, end = min(dates) - timedelta(minutes=1), max(dates) + timedelta(hours=1)
    LOG.info("Fetching schedules from %s through %s", iso_utc(start), iso_utc(end))
    records = collect_schedule(start, end, fixture)
    tree, count = build_xml(records, start, end)
    if count == 0:
        LOG.error("No valid programmes found; keeping the previous XMLTV file")
        return 2
    write_xml(tree)
    LOG.info("Wrote %d programmes to %s", count, XMLTV_FILE)
    return 0


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(OUTPUT_DIR), **kwargs)


def serve():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    LOG.info("Serving %s at http://%s:%s/", OUTPUT_DIR, HOST, PORT)
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("generate", "serve", "all"), default="all")
    parser.add_argument("--input-json", help="Test from saved API response rather than make HTTP requests")
    args = parser.parse_args()
    if args.command in ("generate", "all"):
        rc = generate(args.input_json)
        if rc:
            raise SystemExit(rc)
    if args.command in ("serve", "all"):
        serve()
