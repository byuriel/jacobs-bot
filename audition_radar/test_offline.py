#!/usr/bin/env python3
"""
Offline logic tests. No network. Run after editing filters:
    python3 test_offline.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yaml
from audition_radar import (Listing, geo_check, relevance_check, build_embed,
                            haversine_miles, extract_places, db_connect,
                            is_new, mark_seen)

cfg = yaml.safe_load(open(Path(__file__).parent / "config.yaml"))
HOME = (cfg["home"]["lat"], cfg["home"]["lon"])
R = cfg["home"]["radius_miles"]

FIXTURES = [
    # (should_fire, listing)
    (True, Listing(
        title="Royal Caribbean Entertainment | Vocalists, Dancers, and Hip Hop Dancers | Los Angeles, CA",
        url="https://playbill.com/job/rcl-vocalists-la/abc123",
        source="Playbill",
        summary=("Seeking Vocalists: Strong singers with professional performance "
                 "background. Male Presenting: Tenors and Bari-tenors with strong "
                 "falsetto extension. Prepare one 32-bar cut of a contemporary "
                 "song, preferably pop/rock. Auditions by appointment only in "
                 "Los Angeles, CA. $3,300.00 - $5,500.00 per month."))),

    (True, Listing(
        title="NCLH Vocalists — submit reel",
        url="https://pearsoncasting.com/nclh-vocal-2026",
        source="AuditionsFree",
        summary=("Seeking vocalists for Norwegian Cruise Line. Contracts year-round. "
                 "$1,300 per week. Self-submit your picture, CV and reel to "
                 "audition@pearsoncasting.com. Video submission accepted."))),

    (True, Listing(
        title="Musical Theatre West — Principal Auditions",
        url="https://example.com/mtw-principals",
        source="BroadwayWorld",
        summary="Open call for principals. Long Beach, CA. Paid contract.")),

    (False, Listing(
        title="Norwegian Cruise Line Dancer Call",
        url="https://example.com/nyc-dancers",
        source="AuditionsFree",
        summary=("Open call for dancers only. New York, NY. Please learn the "
                 "across the floor and contemporary combination."))),

    (False, Listing(
        title="Community production seeks volunteers",
        url="https://example.com/volunteer",
        source="AuditionsFree",
        summary="Unpaid community production in Riverside, CA. Volunteer only.")),

    (False, Listing(
        title="Equity Principal Audition — Chicago, IL",
        url="https://example.com/chicago-epa",
        source="Playbill",
        summary="EPA for principals. Chicago, IL. In-person only, no video.")),

    (True, Listing(
        title="Disney Live Entertainment vocalist submission",
        url="https://example.com/dle-vocal",
        source="Playbill",
        summary=("Seeking vocalists for Disneyland Resort. Anaheim, CA. "
                 "Paid, AGVA agreement."))),
]


def main():
    ok = True

    # --- distance sanity ---
    d_la = haversine_miles(*HOME, 34.0522, -118.2437)
    d_sd = haversine_miles(*HOME, 32.7157, -117.1611)
    d_chi = haversine_miles(*HOME, 41.8781, -87.6298)
    print(f"Fontana -> Los Angeles : {d_la:6.1f} mi  (expect ~46, in radius)")
    print(f"Fontana -> San Diego   : {d_sd:6.1f} mi  (expect ~95, OUT of radius)")
    print(f"Fontana -> Chicago     : {d_chi:6.1f} mi  (expect ~1,740)")
    assert d_la < R and d_sd > R and d_chi > 1500
    print()

    print("place extraction:", extract_places(
        "Auditions by appointment only in Los Angeles, CA on August 18th."))
    print()

    # --- fixture pipeline ---
    print(f"{'FIRE?':<7}{'EXPECT':<8}{'SCORE':<7}{'GEO':<22}TITLE")
    print("-" * 100)
    for expected, listing in FIXTURES:
        v = relevance_check(listing.blob(), cfg)
        g = geo_check(listing.blob(), HOME, R) if v.keep else None
        fired = bool(v.keep and g and g.matched)
        status = "PASS" if fired == expected else "**FAIL**"
        if fired != expected:
            ok = False
        geo_txt = f"{g.reason} {g.miles or ''}".strip() if g else "-"
        print(f"{str(fired):<7}{str(expected):<8}{v.score:<7}{geo_txt:<22}"
              f"{listing.title[:48]}  {status}")
    print()

    # --- dedup ---
    import audition_radar
    audition_radar.DB_PATH = Path("/tmp/test_seen.sqlite3")
    if audition_radar.DB_PATH.exists():
        audition_radar.DB_PATH.unlink()
    con = db_connect()
    l = FIXTURES[0][1]
    first, _ = is_new(con, l.key()), mark_seen(con, l)
    con.commit()
    second = is_new(con, l.key())
    print(f"dedup: first sighting new={first}, second sighting new={second}")
    assert first and not second
    # same listing, different tracking params -> still deduped
    dupe = Listing(title=l.title, url=l.url + "?utm_source=x", source=l.source)
    print(f"dedup: same URL with ?utm params treated as new={is_new(con, dupe.key())}")
    print()

    # --- discord payload ---
    l0 = FIXTURES[0][1]
    embed = build_embed(l0, geo_check(l0.blob(), HOME, R),
                        relevance_check(l0.blob(), cfg))
    print("sample Discord embed:")
    print(json.dumps(embed, indent=2)[:900])
    assert len(embed["title"]) <= 256

    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
