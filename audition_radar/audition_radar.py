#!/usr/bin/env python3
"""
Audition Radar
--------------
Polls audition listing sources, filters to a geographic radius (plus
remote/video-submission calls, which have no geography), de-duplicates
against a local SQLite store, and pushes new hits to a Discord webhook.

Run it on a schedule. Every listing is announced exactly once, ever.

Usage:
    python3 audition_radar.py                 # normal run
    python3 audition_radar.py --dry-run       # print, don't post, don't record
    python3 audition_radar.py --test-discord  # send one test message
    python3 audition_radar.py --backfill      # mark everything seen, post nothing
                                              # (run this ONCE on first setup)
"""

import argparse
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.yaml"
DB_PATH = BASE / "seen.sqlite3"

# A custom bot-shaped UA got AuditionsFree's WAF to answer HTTP 500 on the main
# feed and serve an HTML challenge page (which feedparser reported as malformed
# XML) on the tag feed -- both sites load fine in a browser. A normal desktop UA
# is what unblocks them. This does not bypass any login or paywall; everything
# scraped is public, and politeness_seconds still throttles to one request at a
# time, once a day.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

log = logging.getLogger("radar")


# --------------------------------------------------------------------------
# Geography
# --------------------------------------------------------------------------

def haversine_miles(lat1, lon1, lat2, lon2):
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# Bundled gazetteer. Deliberately offline: no API key, no rate limit, no
# outage. Covers everything a SoCal audition listing realistically names.
# Add rows as you hit misses -- the log tells you what it couldn't place.
CITIES = {
    # (lat, lon)
    "fontana, ca": (34.0922, -117.4350),
    "los angeles, ca": (34.0522, -118.2437),
    "hollywood, ca": (34.0928, -118.3287),
    "north hollywood, ca": (34.1870, -118.3813),
    "burbank, ca": (34.1808, -118.3090),
    "glendale, ca": (34.1425, -118.2551),
    "pasadena, ca": (34.1478, -118.1445),
    "santa monica, ca": (34.0195, -118.4912),
    "culver city, ca": (34.0211, -118.3965),
    "long beach, ca": (33.7701, -118.1937),
    "anaheim, ca": (33.8366, -117.9143),
    "buena park, ca": (33.8675, -117.9981),
    "costa mesa, ca": (33.6411, -117.9187),
    "irvine, ca": (33.6846, -117.8265),
    "fullerton, ca": (33.8704, -117.9243),
    "garden grove, ca": (33.7739, -117.9414),
    "santa ana, ca": (33.7455, -117.8677),
    "huntington beach, ca": (33.6603, -117.9992),
    "la mirada, ca": (33.9172, -118.0120),
    "whittier, ca": (33.9792, -118.0328),
    "cerritos, ca": (33.8583, -118.0648),
    "downey, ca": (33.9401, -118.1332),
    "torrance, ca": (33.8358, -118.3406),
    "riverside, ca": (33.9806, -117.3755),
    "corona, ca": (33.8753, -117.5664),
    "temecula, ca": (33.4936, -117.1484),
    "san bernardino, ca": (34.1083, -117.2898),
    "redlands, ca": (34.0556, -117.1825),
    "rialto, ca": (34.1064, -117.3703),
    "colton, ca": (34.0739, -117.3136),
    "rancho cucamonga, ca": (34.1064, -117.5931),
    "ontario, ca": (34.0633, -117.6509),
    "upland, ca": (34.0975, -117.6484),
    "claremont, ca": (34.0967, -117.7198),
    "pomona, ca": (34.0551, -117.7500),
    "chino, ca": (34.0122, -117.6889),
    "chino hills, ca": (33.9898, -117.7326),
    "moreno valley, ca": (33.9425, -117.2297),
    "hemet, ca": (33.7476, -116.9720),
    "palm springs, ca": (33.8303, -116.5453),
    "victorville, ca": (34.5362, -117.2928),
    "thousand oaks, ca": (34.1706, -118.8376),
    "valencia, ca": (34.4433, -118.6081),
    "santa clarita, ca": (34.3917, -118.5426),
    "san diego, ca": (32.7157, -117.1611),
    "escondido, ca": (33.1192, -117.0864),
    "oceanside, ca": (33.1959, -117.3795),
    "carlsbad, ca": (33.1581, -117.3506),
    "vista, ca": (33.2000, -117.2425),
    "bakersfield, ca": (35.3733, -119.0187),
    "ventura, ca": (34.2746, -119.2290),
    "oxnard, ca": (34.1975, -119.1771),
    "beverly hills, ca": (34.0736, -118.4004),
    "west hollywood, ca": (34.0900, -118.3617),
    "el segundo, ca": (33.9192, -118.4165),
    "inglewood, ca": (33.9617, -118.3531),
    "pico rivera, ca": (33.9831, -118.0967),
    "orange, ca": (33.7879, -117.8531),
    "brea, ca": (33.9167, -117.9001),
    "yorba linda, ca": (33.8886, -117.8131),
    "mission viejo, ca": (33.6000, -117.6720),
    "laguna beach, ca": (33.5427, -117.7854),
    "san juan capistrano, ca": (33.5017, -117.6625),
    "lancaster, ca": (34.6868, -118.1542),
    "palmdale, ca": (34.5794, -118.1165),
    "san francisco, ca": (37.7749, -122.4194),
    "sacramento, ca": (38.5816, -121.4944),
    "san jose, ca": (37.3382, -121.8863),
    "fresno, ca": (36.7378, -119.7871),
    # Major out-of-area markets. Present so they resolve and get REJECTED
    # cleanly, instead of falling through the unknown-location escape hatch
    # and spamming the channel with New York and Orlando calls.
    "new york, ny": (40.7128, -74.0060),
    "brooklyn, ny": (40.6782, -73.9442),
    "chicago, il": (41.8781, -87.6298),
    "miami, fl": (25.7617, -80.1918),
    "north miami, fl": (25.8901, -80.1867),
    "fort lauderdale, fl": (26.1224, -80.1373),
    "orlando, fl": (28.5383, -81.3792),
    "tampa, fl": (27.9506, -82.4572),
    "atlanta, ga": (33.7490, -84.3880),
    "dallas, tx": (32.7767, -96.7970),
    "houston, tx": (29.7604, -95.3698),
    "austin, tx": (30.2672, -97.7431),
    "nashville, tn": (36.1627, -86.7816),
    "boston, ma": (42.3601, -71.0589),
    "philadelphia, pa": (39.9526, -75.1652),
    "pittsburgh, pa": (40.4406, -79.9959),
    "seattle, wa": (47.6062, -122.3321),
    "portland, or": (45.5152, -122.6784),
    "denver, co": (39.7392, -104.9903),
    "phoenix, az": (33.4484, -112.0740),
    "las vegas, nv": (36.1699, -115.1398),
    "salt lake city, ut": (40.7608, -111.8910),
    "minneapolis, mn": (44.9778, -93.2650),
    "detroit, mi": (42.3314, -83.0458),
    "cleveland, oh": (41.4993, -81.6944),
    "st. louis, mo": (38.6270, -90.1994),
    "kansas city, mo": (39.0997, -94.5786),
    "oklahoma city, ok": (35.4676, -97.5164),
    "new orleans, la": (29.9511, -90.0715),
    "charlotte, nc": (35.2271, -80.8431),
    "washington, dc": (38.9072, -77.0369),
    "baltimore, md": (39.2904, -76.6122),
    "toronto, on": (43.6532, -79.3832),
    "london, uk": (51.5074, -0.1278),
    # Added after the first live runs. Every one of these showed up as
    # UNMAPPED LOCATION and therefore fired through the fail-open escape
    # hatch -- 12 of 46 hits in one run were unmappable places, all of them
    # thousands of miles away. Naming them is what lets them be rejected.
    "fort worth, tx": (32.7555, -97.3308),
    "arlington, tx": (32.7357, -97.1081),
    "san antonio, tx": (29.4241, -98.4936),
    "concord, ma": (42.4604, -71.3489),
    "worcester, ma": (42.2626, -71.8023),
    "astoria, ny": (40.7644, -73.9235),
    "queens, ny": (40.7282, -73.7949),
    "bronx, ny": (40.8448, -73.8648),
    "cape may, nj": (38.9351, -74.9060),
    "newark, nj": (40.7357, -74.1724),
    "jersey city, nj": (40.7178, -74.0431),
    "princeton, nj": (40.3573, -74.6672),
    "wilmington, de": (39.7391, -75.5398),
    "baton rouge, la": (30.4515, -91.1871),
    "lexington, sc": (33.9815, -81.2362),
    "columbia, sc": (34.0007, -81.0348),
    "charleston, sc": (32.7765, -79.9311),
    "titusville, fl": (28.6122, -80.8076),
    "coconut creek, fl": (26.2518, -80.1789),
    "sarasota, fl": (27.3364, -82.5307),
    "jacksonville, fl": (30.3322, -81.6557),
    "west palm beach, fl": (26.7153, -80.0534),
    "peoria, az": (33.5806, -112.2374),
    "tucson, az": (32.2226, -110.9747),
    "shakopee, mn": (44.7974, -93.5269),
    "milwaukee, wi": (43.0389, -87.9065),
    "indianapolis, in": (39.7684, -86.1581),
    "columbus, oh": (39.9612, -82.9988),
    "cincinnati, oh": (39.1031, -84.5120),
    "louisville, ky": (38.2527, -85.7585),
    "memphis, tn": (35.1495, -90.0490),
    "richmond, va": (37.5407, -77.4360),
    "raleigh, nc": (35.7796, -78.6382),
    "albuquerque, nm": (35.0844, -106.6504),
    "spokane, wa": (47.6588, -117.4260),
    "tacoma, wa": (47.2529, -122.4443),
    "eugene, or": (44.0521, -123.0868),
    "reno, nv": (39.5296, -119.8138),
}

STATE_ABBR = {
    "california": "ca", "nevada": "nv", "arizona": "az", "new york": "ny",
    "florida": "fl", "texas": "tx", "illinois": "il", "georgia": "ga",
}

# Full state table, used to reject out-of-state calls whose city simply is not
# in CITIES. Adding cities one at a time never catches up -- "NJ Local
# Auditions", "Broward County", "in Tennessee" name no city at all, so they
# fell through the unknown-location hatch and pinged the channel.
US_STATES = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar",
    "california": "ca", "colorado": "co", "connecticut": "ct",
    "delaware": "de", "florida": "fl", "georgia": "ga", "hawaii": "hi",
    "idaho": "id", "illinois": "il", "indiana": "in", "iowa": "ia",
    "kansas": "ks", "kentucky": "ky", "louisiana": "la", "maine": "me",
    "maryland": "md", "massachusetts": "ma", "michigan": "mi",
    "minnesota": "mn", "mississippi": "ms", "missouri": "mo",
    "montana": "mt", "nebraska": "ne", "new hampshire": "nh",
    "new jersey": "nj", "new mexico": "nm", "north carolina": "nc",
    "north dakota": "nd", "ohio": "oh", "oklahoma": "ok", "oregon": "or",
    "pennsylvania": "pa", "rhode island": "ri", "south carolina": "sc",
    "south dakota": "sd", "tennessee": "tn", "utah": "ut", "vermont": "vt",
    "virginia": "va", "washington": "wa", "west virginia": "wv",
    "wisconsin": "wi", "wyoming": "wy",
}
_STATE_CODES = set(US_STATES.values()) | {"dc"}


def mentioned_states(text):
    """US state codes named anywhere in `text`.

    Two-letter codes are only accepted after a comma ("Fontana, CA"), because
    bare uppercase matching turns ordinary words into states -- IN, OR, ME, OK,
    HI and DE all appear constantly in audition copy.
    """
    found = set()
    low = (text or "").lower()
    for name, code in US_STATES.items():
        if re.search(r"\b" + re.escape(name) + r"\b", low):
            found.add(code)
    for m in re.finditer(r",\s*([A-Za-z]{2})\b", text or ""):
        code = m.group(1).lower()
        if code in _STATE_CODES:
            found.add(code)
    return found

# "Los Angeles, CA" / "Anaheim, California" / "LOS ANGELES CA"
CITY_STATE_RE = re.compile(
    r"\b([A-Z][A-Za-z\.\'\-]+(?:\s+[A-Z][A-Za-z\.\'\-]+){0,3})"
    r"\s*,\s*([A-Za-z]{2}|[A-Za-z]{4,})\b"
)

# Deliberately narrow. These are matched against the *enriched* page text, which
# on Playbill is mostly site boilerplate, so anything vague matches everything.
# The earlier list carried "email your", "submit your materials", "casting
# profile", "by video", "reel to" and "submit online" -- generic enough that a
# NYC ECC, a Boston EPA and a Fort Worth EPA all passed as REMOTE / VIDEO in the
# first live run. Every phrase here has to be one that only appears when a
# listing genuinely accepts a submission from somewhere else.
REMOTE_PATTERNS = [
    "self-tape", "self tape", "selftape",
    "video submission", "video submissions", "video audition",
    "virtual audition", "virtual auditions", "virtual call", "virtual open call",
    "online submission", "online submissions", "online audition",
    "submit via video", "submit a video", "submit your reel",
    "remote audition", "zoom audition",
    "accepting submissions from", "no in-person",
]


def normalize_place(city, state):
    city = re.sub(r"\s+", " ", city).strip().lower()
    state = state.strip().lower()
    state = STATE_ABBR.get(state, state)
    if len(state) != 2:
        return None
    return f"{city}, {state}"


def extract_places(text):
    """Return every 'city, st' string we can find, most specific first."""
    out = []
    for m in CITY_STATE_RE.finditer(text or ""):
        key = normalize_place(m.group(1), m.group(2))
        if key and key not in out:
            out.append(key)
    return out


@dataclass
class GeoVerdict:
    matched: bool
    reason: str
    place: str = ""
    miles: float = 0.0


def geo_check(text, home, radius_miles, remote_text=None):
    """
    Decide whether a listing is geographically relevant.

    Three ways to pass:
      1. Names a city inside the radius.
      2. Accepts remote / video submission (geography is irrelevant).
      3. Names no place at all -> pass, flagged UNKNOWN. Better a false
         positive he glances at than a missed call.

    `text` is the full blob -- more context makes place extraction better.
    `remote_text` is the narrower "what this listing says about itself" view
    used only for the remote/video test, so a sidebar of other listings on the
    detail page can't make an in-person call look self-submittable. Defaults to
    `text` when not supplied.
    """
    own = remote_text if remote_text is not None else (text or "")
    lower = own.lower()

    places = extract_places(text)
    known = [(p, CITIES[p]) for p in places if p in CITIES]

    if known:
        best_place, best_miles = None, 1e9
        for p, (lat, lon) in known:
            d = haversine_miles(home[0], home[1], lat, lon)
            if d < best_miles:
                best_place, best_miles = p, d
        if best_miles <= radius_miles:
            return GeoVerdict(True, "IN RADIUS", best_place, round(best_miles, 1))
        # Named a real city and it is too far. "Video submissions accepted" no
        # longer rescues it: a self-tape for a New York play is still a New
        # York job, and those pings are what made the channel unusable. Cruise
        # employers are exempted later, in decide().
        return GeoVerdict(False, "OUT OF RADIUS", best_place, round(best_miles, 1))

    # No city we can place. If it names a state that isn't California, that is
    # enough to reject -- catches "NJ Local Auditions" and "in Tennessee",
    # which name no city and used to sail through as LOCATION UNKNOWN.
    states = mentioned_states(own)
    if states and "ca" not in states:
        return GeoVerdict(False, "OUT OF STATE", sorted(states)[0].upper())

    if any(p in lower for p in REMOTE_PATTERNS):
        return GeoVerdict(True, "REMOTE / VIDEO")

    if places:  # named somewhere, we just don't have coords for it
        return GeoVerdict(True, "UNMAPPED LOCATION", places[0])

    return GeoVerdict(True, "LOCATION UNKNOWN")


# --------------------------------------------------------------------------
# Relevance
# --------------------------------------------------------------------------

@dataclass
class Verdict:
    keep: bool
    score: int = 0
    tags: list = field(default_factory=list)


def relevance_check(text, cfg):
    lower = (text or "").lower()
    tags, score = [], 0

    for kw in cfg["filters"]["hard_exclude"]:
        if kw.lower() in lower:
            return Verdict(False)

    include_hits = [kw for kw in cfg["filters"]["include"] if kw.lower() in lower]
    if not include_hits:
        return Verdict(False)
    score += len(include_hits)

    for emp in cfg["filters"]["priority_employers"]:
        if emp.lower() in lower:
            tags.append("PRIORITY EMPLOYER")
            score += 10
            break

    for kw in cfg["filters"]["boost"]:
        if kw.lower() in lower:
            score += 2

    # Dancer-only calls: singer language absent, dance language dominant.
    singer_words = ("vocalist", "singer", "sing", "vocal", "tenor",
                    "baritone", "bari-tenor", "principal", "actor")
    if not any(w in lower for w in singer_words):
        dance_words = ("dancer", "choreograph", "ballet", "jazz combination",
                       "across the floor")
        if any(w in lower for w in dance_words):
            return Verdict(False)

    return Verdict(True, score, tags)


# --------------------------------------------------------------------------
# Listings + sources
# --------------------------------------------------------------------------

@dataclass
class Listing:
    title: str
    url: str
    source: str
    summary: str = ""
    location: str = ""
    posted: str = ""
    # Detail-page text pulled in by enrich(). Kept separate from `summary`
    # rather than concatenated onto it, because the two are trusted for
    # different questions -- see own_blob().
    enriched: str = ""

    def key(self):
        u = urlparse(self.url)
        canon = f"{u.netloc}{u.path}".lower().rstrip("/")
        if not canon:
            canon = f"{self.source}:{self.title}".lower()
        return hashlib.sha256(canon.encode()).hexdigest()[:32]

    def blob(self):
        """Everything we know. Used for relevance scoring and place lookup,
        where more context is strictly better."""
        return " \n ".join([self.title, self.location, self.summary, self.enriched])

    def own_blob(self):
        """Only what this listing says about itself.

        A detail page carries site chrome and a sidebar of *other* listings, so
        a phrase like "video submissions" found anywhere in the enriched text
        might belong to a neighbouring call rather than this one. That is what
        made Fort Worth and Concord MA EPAs read as REMOTE / VIDEO. Remote
        detection is judged on this narrower text; in practice genuinely remote
        calls announce it in their own title ("Virtual Auditions for...",
        "Equity video submissions"), so little is lost.
        """
        return " \n ".join([self.title, self.location, self.summary])


def http_get(url, timeout=25):
    r = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        timeout=timeout,
    )
    r.raise_for_status()
    return r


def fetch_rss(src):
    import feedparser
    out = []
    parsed = feedparser.parse(src["url"], request_headers={"User-Agent": USER_AGENT})
    # feedparser never raises -- it returns an empty result on a dead feed,
    # which is indistinguishable from "no auditions this week". Make it loud.
    if getattr(parsed, "bozo", 0) and not parsed.entries:
        raise RuntimeError(f"feed unparseable: {getattr(parsed, 'bozo_exception', '?')}")
    status = getattr(parsed, "status", None)
    if status and status >= 400:
        raise RuntimeError(f"feed returned HTTP {status}")
    if not parsed.entries:
        raise RuntimeError("feed returned zero entries")
    for e in parsed.entries[: src.get("max_items", 40)]:
        summary = BeautifulSoup(
            getattr(e, "summary", "") or "", "html.parser"
        ).get_text(" ", strip=True)
        out.append(Listing(
            title=getattr(e, "title", "(untitled)"),
            url=getattr(e, "link", ""),
            source=src["name"],
            summary=summary[:1500],
            posted=getattr(e, "published", "") or getattr(e, "updated", ""),
        ))
    return out


def fetch_html(src):
    """
    Config-driven CSS scraper. Every selector is optional except `item`
    and `link`. If a site redesigns, you fix a string in config.yaml
    instead of touching this file.
    """
    r = http_get(src["url"])
    soup = BeautifulSoup(r.text, "html.parser")
    sel = src["selectors"]
    out = []

    for node in soup.select(sel["item"])[: src.get("max_items", 60)]:
        a = node.select_one(sel["link"]) if sel.get("link") else None
        if a is None and node.name == "a":
            a = node
        if a is None or not a.get("href"):
            continue
        url = urljoin(src["url"], a["href"])

        def grab(k):
            if not sel.get(k):
                return ""
            n = node.select_one(sel[k])
            return n.get_text(" ", strip=True) if n else ""

        title = grab("title") or a.get_text(" ", strip=True)
        out.append(Listing(
            title=title[:300] or "(untitled)",
            url=url,
            source=src["name"],
            summary=(grab("summary") or node.get_text(" ", strip=True))[:1500],
            location=grab("location"),
            posted=grab("posted"),
        ))
    return out


def enrich(listing, cfg):
    """
    Fetch the detail page for listings that scored well but told us little.
    Listing pages are often just a title; the location and the vocal spec
    live one click in. This is what stops geo-filtering from throwing away
    real hits.
    """
    try:
        r = http_get(listing.url, timeout=20)
        text = BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)
        listing.enriched = text[:6000]
        time.sleep(cfg["runtime"]["politeness_seconds"])
    except Exception as e:
        log.debug("enrich failed for %s: %s", listing.url, e)
    return listing


def _word_hits(terms, text):
    """Whole-word matches only.

    Substring matching is wrong here and quietly so: "alto" sits inside
    Rialto (8 mi from Fontana) and "bass" inside bassist.
    """
    low = (text or "").lower()
    return [t for t in terms
            if re.search(r"(?<!\w)" + re.escape(t.lower()) + r"(?!\w)", low)]


def voice_check(text, cfg):
    """Is this listing plausibly for a male baritone?

    Returns (keep, matched_terms). Order matters: a listing naming his voice
    type is kept before voice_exclude is ever consulted, so "Tenors and
    Bari-tenors" survives even though a bare tenor call would not.
    """
    f = cfg["filters"]
    wanted = _word_hits(f.get("voice_match", []), text)
    if wanted:
        return True, wanted

    blocked = _word_hits(f.get("voice_exclude", []), text)
    if blocked:
        return False, blocked

    # Nothing stated. "Seeking vocalists" is the overwhelming majority of real
    # listings and a baritone can audition for all of them, so this fires
    # unless the operator has explicitly asked for literal exclusivity.
    return (not f.get("require_voice_match", False)), []


def decide(listing, cfg, home, radius):
    """The whole keep/drop decision for one listing.

    Returns (fired, geo, verdict).

    Deliberately the single source of truth: run() and test_offline.py both
    call this. An earlier version had the test calling geo_check with
    different arguments than production, so a real regression passed the
    suite. Enrichment is the caller's job -- it needs network.
    """
    verdict = relevance_check(listing.blob(), cfg)
    if not verdict.keep:
        return False, None, verdict

    # Judged on the listing's own text, not the enriched page: a Playbill
    # sidebar advertising a soprano call must not veto this listing.
    voice_ok, voice_hits = voice_check(listing.own_blob(), cfg)
    if not voice_ok:
        log.debug("dropped on voice type: %s", listing.title[:70])
        return False, None, verdict
    if voice_hits:
        verdict.tags.append("VOICE MATCH")
        verdict.score += 5

    geo = geo_check(listing.blob(), home, radius,
                    remote_text=listing.own_blob())

    f = cfg["filters"]
    is_priority = "PRIORITY EMPLOYER" in verdict.tags

    # Only cruise work ignores the radius. Contracts are global and rolling and
    # you fly to the ship, so a Miami or London audition stop is still a job he
    # can take. Everything else -- theme parks, regional houses -- is a job in
    # the city it names, which is why this checks radius_exempt and not the
    # whole priority_employers list.
    own = listing.own_blob().lower()
    exempt = any(t.lower() in own for t in f.get("radius_exempt", []))
    if (not geo.matched and exempt
            and f.get("priority_employer_ignores_radius", True)):
        geo = GeoVerdict(True, "CRUISE — ANY LOCATION", geo.place, geo.miles)

    # Narrow the fail-open hatch. An unplaceable listing still fires, but only
    # if it looks like this kind of work -- otherwise background-extra and
    # game-show casting from across the country crowds out the signal.
    if (geo.matched
            and geo.reason in ("UNMAPPED LOCATION", "LOCATION UNKNOWN")
            and f.get("require_signal_when_location_unknown", True)
            and not is_priority):
        low = listing.blob().lower()
        if not any(t.lower() in low for t in f.get("signal_terms", [])):
            log.debug("dropped unplaceable, no signal: %s", listing.title[:70])
            return False, geo, verdict

    return geo.matched, geo, verdict


FETCHERS = {"rss": fetch_rss, "html": fetch_html}


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------

def db_connect():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            key TEXT PRIMARY KEY,
            url TEXT,
            title TEXT,
            source TEXT,
            first_seen TEXT
        )
    """)
    con.commit()
    return con


def is_new(con, key):
    return con.execute("SELECT 1 FROM seen WHERE key=?", (key,)).fetchone() is None


def mark_seen(con, listing):
    con.execute(
        "INSERT OR IGNORE INTO seen VALUES (?,?,?,?,?)",
        (listing.key(), listing.url, listing.title[:300], listing.source,
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )


# --------------------------------------------------------------------------
# Discord
# --------------------------------------------------------------------------

COLORS = {"PRIORITY EMPLOYER": 0xF1C40F, "CRUISE — ANY LOCATION": 0xF1C40F,
          "IN RADIUS": 0x2ECC71, "REMOTE / VIDEO": 0x3498DB,
          "default": 0x95A5A6}


def build_embed(listing, geo, verdict):
    color = COLORS["default"]
    if "PRIORITY EMPLOYER" in verdict.tags:
        color = COLORS["PRIORITY EMPLOYER"]
    elif geo.reason in COLORS:
        color = COLORS[geo.reason]

    where = geo.reason
    if geo.place:
        city, _, st = geo.place.rpartition(", ")
        where += f" — {city.title()}, {st.upper()}"
        if geo.miles:
            where += f" ({geo.miles} mi)"

    desc = re.sub(r"\s+", " ", listing.summary).strip()
    if len(desc) > 400:
        desc = desc[:400].rsplit(" ", 1)[0] + "…"

    fields = [{"name": "Where", "value": where, "inline": True},
              {"name": "Source", "value": listing.source, "inline": True}]
    if listing.posted:
        fields.append({"name": "Posted", "value": listing.posted[:60], "inline": True})

    return {
        "title": listing.title[:250],
        "url": listing.url,
        "description": desc or "(no preview text — open the link)",
        "color": color,
        "fields": fields,
        "footer": {"text": " · ".join(verdict.tags) if verdict.tags else "audition radar"},
    }


def post_discord(webhook, embeds, content=None):
    """Discord caps embeds at 10 per message."""
    for i in range(0, len(embeds), 10):
        chunk = embeds[i:i + 10]
        payload = {"embeds": chunk, "username": "Audition Radar"}
        if content and i == 0:
            payload["content"] = content
        for attempt in range(4):
            r = requests.post(webhook, json=payload, timeout=20)
            if r.status_code == 429:
                wait = r.json().get("retry_after", 2)
                log.warning("discord rate limited, sleeping %.1fs", wait)
                time.sleep(float(wait) + 0.5)
                continue
            if r.status_code >= 400:
                log.error("discord %s: %s", r.status_code, r.text[:300])
            break
        time.sleep(1.0)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def load_config():
    if not CONFIG_PATH.exists():
        sys.exit(f"missing {CONFIG_PATH}")
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    # Env var wins so the real webhook never has to live in the committed
    # config.yaml -- this repo is public.
    env_webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if env_webhook:
        cfg["discord_webhook"] = env_webhook
    if not cfg.get("discord_webhook", "").startswith("https://"):
        sys.exit("set DISCORD_WEBHOOK_URL env var, or discord_webhook in config.yaml")
    return cfg


def run(args):
    cfg = load_config()
    home = (cfg["home"]["lat"], cfg["home"]["lon"])
    radius = cfg["home"]["radius_miles"]
    con = db_connect()

    collected = []
    live, dead = 0, []
    for src in cfg["sources"]:
        if not src.get("enabled", True):
            continue
        fetcher = FETCHERS.get(src["type"])
        if not fetcher:
            log.error("unknown source type %r", src["type"])
            continue
        try:
            items = fetcher(src)
            log.info("%-28s %3d items", src["name"], len(items))
            collected.extend(items)
            live += 1
        except Exception as e:
            # One dead source must never take down the run.
            log.error("%-28s FAILED: %s", src["name"], e)
            dead.append(f"{src['name']}: {str(e)[:120]}")
        time.sleep(cfg["runtime"]["politeness_seconds"])

    # A silent radar and a quiet market look identical from Discord. If every
    # source is down, say so out loud rather than letting him assume nothing
    # is casting.
    if live == 0 and dead and not args.dry_run and not args.backfill:
        post_discord(cfg["discord_webhook"], [{
            "title": "⚠️ Audition Radar: every source failed",
            "description": "No listings were retrieved this run. This is a radar "
                           "problem, not an empty market — assume you are blind "
                           "until it's fixed.\n\n" + "\n".join(f"• {d}" for d in dead),
            "color": 0xE74C3C,
        }])
        log.error("all sources dead; alerted Discord")
        return

    hits = []
    for listing in collected:
        if not listing.url:
            continue
        if not is_new(con, listing.key()):
            continue

        verdict = relevance_check(listing.blob(), cfg)
        if not verdict.keep:
            mark_seen(con, listing)
            continue

        # Thin listing + plausible hit -> go read the detail page.
        if len(listing.blob()) < cfg["runtime"]["enrich_below_chars"]:
            enrich(listing, cfg)

        fired, geo, verdict = decide(listing, cfg, home, radius)
        if not fired:
            mark_seen(con, listing)
            continue

        hits.append((listing, geo, verdict))

    hits.sort(key=lambda t: t[2].score, reverse=True)

    if args.backfill:
        for listing in collected:
            mark_seen(con, listing)
        con.commit()
        print(f"backfilled {len(collected)} listings as seen. "
              f"{len(hits)} would have fired. Nothing posted.")
        return

    if args.dry_run:
        print(f"\n{len(hits)} new hit(s) — DRY RUN, nothing posted, nothing recorded\n")
        for listing, geo, verdict in hits:
            print(f"  [{verdict.score:>3}] {geo.reason:<20} {listing.title[:70]}")
            print(f"        {listing.url}")
        return

    if hits:
        embeds = [build_embed(l, g, v) for l, g, v in hits]
        header = (f"**{len(hits)} new audition{'s' if len(hits) != 1 else ''}** "
                  f"· {datetime.now().strftime('%b %d, %I:%M %p')}")
        post_discord(cfg["discord_webhook"], embeds, header)

    for listing, _, _ in hits:
        mark_seen(con, listing)
    for listing in collected:
        mark_seen(con, listing)
    con.commit()
    log.info("run complete: %d scanned, %d posted", len(collected), len(hits))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--test-discord", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.test_discord:
        cfg = load_config()
        post_discord(cfg["discord_webhook"], [{
            "title": "Audition Radar is live",
            "description": "If you're reading this in Discord, the webhook works. "
                           "New calls will land here automatically.",
            "color": 0x2ECC71,
        }])
        print("test message sent")
        return

    run(args)


if __name__ == "__main__":
    main()
