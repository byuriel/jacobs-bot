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

    # Regression guard. In the first live run, out-of-region in-person calls
    # like this were firing as REMOTE / VIDEO because REMOTE_PATTERNS matched
    # generic page boilerplate ("email your", "submit your materials",
    # "casting profile") in the enriched text. Names a real city 2,400 mi away
    # and is in-person only, so it must be dropped. If this starts passing,
    # someone has loosened REMOTE_PATTERNS again.
    (False, Listing(
        title="Other World — NYC ECC Singers (All Genders)",
        url="https://example.com/nyc-ecc-singers",
        source="Playbill",
        summary=("Equity Chorus Call for singers. New York, NY. Prepare 32 "
                 "bars of a contemporary song. Please email your headshot and "
                 "resume to the casting office, or submit your materials at "
                 "the door. Create a casting profile to apply."))),

    # San Diego moved inside the radius when it widened to 100 mi (96.4 mi).
    (True, Listing(
        title="Moonlight Stage Productions — Vocalist Auditions",
        url="https://example.com/moonlight-vocal",
        source="BroadwayWorld",
        summary="Seeking singers for the 2026 season. San Diego, CA. Paid contract.")),

    # Enriched-text contamination. This listing is in-person in Fort Worth, but
    # its detail page carries a sidebar advertising a *different* call that
    # takes video submissions. Before remote detection was narrowed to
    # own_blob(), that sidebar made this fire as REMOTE / VIDEO.
    (False, Listing(
        title="A Christmas Carol — Fort Worth, TX EPA (08.23.26)",
        url="https://example.com/fort-worth-epa",
        source="Playbill",
        summary="Equity Principal Audition. Fort Worth, TX. In person, by appointment.",
        enriched=("More jobs on Playbill: Inter Alia (Broadway) — Equity video "
                  "submissions. Virtual Auditions for a new musical. Self-tape "
                  "accepted. Submit a video today."))),

    # The mirror case: the listing itself is virtual, so it must still fire
    # even though its own summary is all we now trust.
    (True, Listing(
        title="Norwegian Cruise Line — Virtual Auditions for Vocalists",
        url="https://example.com/ncl-virtual",
        source="Castbee",
        summary=("Seeking vocalists. Virtual auditions; video submission "
                 "accepted from anywhere. Paid contract, per week."))),
]


def main():
    ok = True

    # --- distance sanity ---
    d_la = haversine_miles(*HOME, 34.0522, -118.2437)
    d_sd = haversine_miles(*HOME, 32.7157, -117.1611)
    d_ven = haversine_miles(*HOME, 34.2746, -119.2290)
    d_chi = haversine_miles(*HOME, 41.8781, -87.6298)
    print(f"Fontana -> Los Angeles : {d_la:6.1f} mi  (expect ~46, in radius)")
    print(f"Fontana -> San Diego   : {d_sd:6.1f} mi  (expect ~96, in radius)")
    print(f"Fontana -> Ventura     : {d_ven:6.1f} mi  (expect ~103, OUT of radius)")
    print(f"Fontana -> Chicago     : {d_chi:6.1f} mi  (expect ~1,700)")
    # Ventura is the nearest mapped city outside the radius -- it guards the
    # boundary, so widening the radius without updating this test fails loudly.
    assert d_la < R and d_sd < R and d_ven > R and d_chi > 1500
    print()

    print("place extraction:", extract_places(
        "Auditions by appointment only in Los Angeles, CA on August 18th."))
    print()

    # --- fixture pipeline ---
    print(f"{'FIRE?':<7}{'EXPECT':<8}{'SCORE':<7}{'GEO':<22}TITLE")
    print("-" * 100)
    for expected, listing in FIXTURES:
        v = relevance_check(listing.blob(), cfg)
        # Mirror run(): places come from the full blob, remote detection only
        # from what the listing says about itself.
        g = (geo_check(listing.blob(), HOME, R, remote_text=listing.own_blob())
             if v.keep else None)
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
    embed = build_embed(l0, geo_check(l0.blob(), HOME, R,
                                      remote_text=l0.own_blob()),
                        relevance_check(l0.blob(), cfg))
    print("sample Discord embed:")
    print(json.dumps(embed, indent=2)[:900])
    assert len(embed["title"]) <= 256

    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
