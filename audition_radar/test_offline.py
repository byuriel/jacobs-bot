#!/usr/bin/env python3
"""
Offline logic tests. No network. Run after editing filters:
    python3 test_offline.py

Beyond the fixture table there is an invariant audit at the bottom: with
california_only and require_voice_match both on, *nothing* may fire unless it
resolves to California AND names his voice type. That is asserted directly
against every fixture and against a corpus of real titles pulled from live
runs, rather than being inferred from a handful of hand-picked cases.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yaml
from audition_radar import (Listing, geo_check, relevance_check, build_embed,
                            haversine_miles, extract_places, db_connect,
                            is_new, mark_seen, decide, mentioned_states,
                            voice_check, CITIES)

cfg = yaml.safe_load(open(Path(__file__).parent / "config.yaml"))
HOME = (cfg["home"]["lat"], cfg["home"]["lon"])
R = cfg["home"]["radius_miles"]
STRICT = (cfg["filters"].get("california_only")
          and cfg["filters"].get("require_voice_match"))

FIXTURES = [
    # ---- the only shape that survives strict mode: California + his voice --

    (True, Listing(
        title=("Royal Caribbean Entertainment | Vocalists, Dancers, and Hip Hop "
               "Dancers | Los Angeles, CA"),
        url="https://playbill.com/job/rcl-vocalists-la/abc123",
        source="Playbill",
        summary=("Seeking Vocalists: Strong singers with professional performance "
                 "background. Male Presenting: Tenors and Bari-tenors with strong "
                 "falsetto extension. Prepare one 32-bar cut of a contemporary "
                 "song, preferably pop/rock. Auditions by appointment only in "
                 "Los Angeles, CA. $3,300.00 - $5,500.00 per month."))),

    (True, Listing(
        title="Disneyland Resort — male vocalists, baritone",
        url="https://example.com/dlr-baritone",
        source="Playbill",
        summary=("Seeking male vocalists, baritone and bass-baritone, for a "
                 "new show. Anaheim, CA. Paid, AGVA agreement."))),

    (True, Listing(
        title="Musical Theatre West — seeking a baritone principal",
        url="https://example.com/mtw-baritone",
        source="BroadwayWorld",
        summary="Open call for a baritone principal. Long Beach, CA. Paid contract.")),

    # SOCAL REGION path: no city resolves, but the region is named and his
    # voice type is stated.
    (True, Listing(
        title="Open call for baritones — Inland Empire holiday revue",
        url="https://example.com/ie-baritone",
        source="Castbee",
        summary=("Seeking baritones and tenors for a paid holiday revue across "
                 "the Inland Empire."))),

    # ---- California, but no voice type stated -> GATE 2 drops it ----------
    # Every one of these fired before require_voice_match was turned on. They
    # are the cost of strict mode, and it is a real cost: all four are jobs a
    # baritone could take.

    (False, Listing(
        title="Musical Theatre West — Principal Auditions",
        url="https://example.com/mtw-principals",
        source="BroadwayWorld",
        summary="Open call for principals. Long Beach, CA. Paid contract.")),

    (False, Listing(
        title="Disney Live Entertainment vocalist submission",
        url="https://example.com/dle-vocal",
        source="Playbill",
        summary=("Seeking vocalists for Disneyland Resort. Anaheim, CA. "
                 "Paid, AGVA agreement."))),

    (False, Listing(
        title="Moonlight Stage Productions — Vocalist Auditions",
        url="https://example.com/moonlight-vocal",
        source="BroadwayWorld",
        summary="Seeking singers for the 2026 season. San Diego, CA. Paid contract.")),

    (False, Listing(
        title="Vocalist auditions — Rialto, CA",
        url="https://example.com/rialto-vocal",
        source="Playbill",
        summary="Seeking vocalists for a paid holiday revue. Rialto, CA.")),

    # ---- cruise: his voice type, but not California -> GATE 1 drops it ----
    # These fired under the previous config, which gave cruise a worldwide
    # exemption. california_only overrides that by design.

    (False, Listing(
        title="Princess Cruises — Tenors and Bari-tenors",
        url="https://example.com/princess-baritone",
        source="Open Auditions UK — Cruise",
        summary=("Seeking male vocalists: tenors and bari-tenors with strong "
                 "falsetto. London, UK. Paid contract."))),

    (False, Listing(
        title=("CRUISE - Vocalists & Dancers, Signature Production Shows, "
               "Royal Caribbean"),
        url="https://www.openauditions.uk/cruise-vocalists-dancers-royal-caribbean",
        source="Open Auditions UK — Cruise",
        summary="Open audition for vocalists. London, UK. Paid contract.")),

    (False, Listing(
        title="NCLH Vocalists — submit reel",
        url="https://pearsoncasting.com/nclh-vocal-2026",
        source="Castbee",
        summary=("Seeking vocalists for Norwegian Cruise Line. Contracts year-round. "
                 "$1,300 per week. Self-submit your picture, CV and reel to "
                 "audition@pearsoncasting.com. Video submission accepted."))),

    (False, Listing(
        title="Cruise ship vocalists wanted — production show cast",
        url="https://example.com/generic-cruise",
        source="Entertainers Worldwide — Singer/Cruise",
        summary=("Seeking cruise ship vocalists for production shows. "
                 "Contracts 6 months. Paid per week."))),

    # ---- wrong voice / wrong gender --------------------------------------

    (False, Listing(
        title="Seeking Sopranos and Mezzos — female presenting ensemble",
        url="https://example.com/soprano-call",
        source="Playbill",
        summary=("Seeking sopranos and mezzo-sopranos, female presenting, "
                 "for a new musical. Los Angeles, CA. Paid."))),

    (False, Listing(
        title="Female Lead Actor for Western Thriller (Stage)",
        url="https://example.com/female-lead",
        source="Playbill",
        summary="Seeking a female lead actor for a new stage thriller. Paid.")),

    # ---- relevance / hard excludes, unchanged ----------------------------

    (False, Listing(
        title="Norwegian Cruise Line Dancer Call",
        url="https://example.com/nyc-dancers",
        source="Castbee",
        summary=("Open call for dancers only. New York, NY. Please learn the "
                 "across the floor and contemporary combination."))),

    (False, Listing(
        title="Community production seeks volunteers",
        url="https://example.com/volunteer",
        source="Castbee",
        summary="Unpaid community production in Riverside, CA. Volunteer only.")),

    # ---- geography: every one of these reached Discord at some point ------

    (False, Listing(
        title="Equity Principal Audition — Chicago, IL",
        url="https://example.com/chicago-epa",
        source="Playbill",
        summary="EPA for baritones. Chicago, IL. In-person only, no video.")),

    (False, Listing(
        title="Other World — NYC ECC Singers (All Genders)",
        url="https://example.com/nyc-ecc-singers",
        source="Playbill",
        summary=("Equity Chorus Call for baritones. New York, NY. Prepare 32 "
                 "bars. Please email your headshot and resume to the casting "
                 "office, or submit your materials at the door."))),

    (False, Listing(
        title="A Christmas Carol — Fort Worth, TX EPA (08.23.26)",
        url="https://example.com/fort-worth-epa",
        source="Playbill",
        summary="EPA for baritones. Fort Worth, TX. In person, by appointment.",
        enriched=("More jobs on Playbill: Inter Alia (Broadway) — Equity video "
                  "submissions. Virtual Auditions for a new musical. Self-tape "
                  "accepted."))),

    (False, Listing(
        title=("Six Flags Holiday in the Park — NJ Local Auditions: Singers, "
               "Actors, Dancers"),
        url="https://example.com/six-flags-nj",
        source="Playbill",
        summary=("Paid seasonal performer contracts for baritones. New Jersey. "
                 "Local auditions, in person."))),

    (False, Listing(
        title=("Faydra - NYC EPA (08.27.26) Manhattan Theatre Club "
               "New York, NY US 08/27/2026"),
        url="https://example.com/faydra-nyc-epa",
        source="Playbill",
        summary="Equity Principal Audition for baritones. Paid.")),

    (False, Listing(
        title="Farmers Alley Theatre 2026-27 Season — Kalamazoo, MI EPA",
        url="https://example.com/kalamazoo-epa",
        source="Playbill",
        summary="EPA for baritones. Michigan. Paid.")),

    (False, Listing(
        title="Music Theater Heritage 2027 Season — Kansas City, MO EPA",
        url="https://example.com/kc-epa",
        source="Playbill",
        summary="EPA for baritones. Kansas City, MO. Paid.")),

    (False, Listing(
        title="New Play Reading — Astoria, NY",
        url="https://example.com/astoria-reading",
        source="Playbill",
        summary="Seeking baritones. Astoria, NY.")),

    (False, Listing(
        title="Virtual Auditions — Seattle Public Theater",
        url="https://example.com/seattle-virtual",
        source="Playbill",
        summary=("Virtual auditions for baritones. Seattle, WA. Video "
                 "submission accepted."))),

    (False, Listing(
        title="Paid Extras Needed for a series shooting in Tennessee",
        url="https://example.com/tn-extras",
        source="Castbee",
        summary="Open call for baritones. Tennessee. Paid day rate.")),

    # Region word present, but the listing is plainly elsewhere. Orange County
    # also exists in Florida.
    (False, Listing(
        title="Orange County open call for baritones",
        url="https://example.com/oc-florida",
        source="Castbee",
        summary="Seeking baritones. Orange County, Florida. Paid.")),

    # No location stated at all.
    (False, Listing(
        title="Seeking baritones for a new musical — self-tape submissions",
        url="https://example.com/nowhere-vocal",
        source="Playbill",
        summary="Seeking baritones. Self-tape submissions accepted. Paid.")),

    (False, Listing(
        title="Paid Background Extras — open call",
        url="https://example.com/extras-broward",
        source="Castbee",
        summary=("Open call for paid background extras on a TV show. Broward "
                 "County. No experience needed."))),
]


# Real titles observed in live GitHub Actions runs. Every one of them reached
# the hit list at some point. Under strict mode none may fire: they are all
# either out of California, missing a voice type, or both.
REAL_WORLD_TITLES = [
    "Performer Paid Six Flags Holiday in the Park -- NJ Local Auditions Singers Actors Dancers",
    "Performer Paid Other World - NYC ECC Singers (Morning & Afternoon - All Genders)",
    "Performer Paid Virtual Auditions for I Never Asked for a GoFundMe at Seattle Public Theater",
    "Performer Paid A Christmas Story, the Musical (Non-Union Tour) Virtual Open Calls",
    "Performer Paid Inter Alia (Broadway) - Equity video submissions (08.12.26)",
    "Performer Paid Inter Alia (Broadway) - NYC EPA (08.11.26) & (08.12.26)",
    "Performer Paid Delaware Theatre Company 2026-27 Season - NYC EPA (08.24.26)",
    "Performer Paid East Lynne Theatre Company 2026 Season - Cape May, NJ EPA",
    "Performer Paid A Christmas Carol - Fort Worth, TX EPA (08.23.26)",
    "Performer Paid Valleyfair's Halloween Season 2026 - ValleyScare",
    "Performer Paid Arizona Broadway Theatre - Pippin Ensemble Submissions",
    "Performer Paid Come From Away Actors Submissions Titusville Playhouse",
    "Performer New Play Reading Carpe Diem Productions Astoria, NY US",
    "Performer Paid Nude Models - School Of Visual Arts",
    "Performer Paid The REV on Tour- Male Non-Eq Actors for TYA productions",
    "Performer Paid Faydra - NYC EPA (08.27.26) Manhattan Theatre Club New York, NY US",
    "Performer Paid Farmers Alley Theatre 2026-27 Season Chicago EPA",
    "Performer Paid Farmers Alley Theatre 2026-27 Season Kalamazoo, MI EPA",
    "Performer Paid Female Lead Actor for Western Thriller (Stage)",
    "Performer Paid Music Theater Heritage 2027 Season Kansas City, MO EPA",
    "BET+'s The Ms. Pat Show Casting Paid Extras in Georgia",
    "Paid Extras Needed for “9-1-1: Nashville” in Tennessee",
    "ABC TV Show “RJ Decker” Casting Paid Background Roles in Broward County Florida",
    "American Immersion Theater Casting Actors in Detroit for Murder Mystery Shows",
    "“The Price Is Right” Contestant Search Coming to South Florida",
    "NHL Hockey Fans Who Are Cancer Survivors in New York City",
    "Paid Acting Opportunity in Baton Rouge, Louisiana – The Dinner Detective",
    "Paid Actor Auditions in Lexington, South Carolina for Training Video Project",
    "Hamilton Broadway and Tour Now Accepting Video Auditions for Performers",
    "Cruise Commercial Casting Real Families and Couples in Los Angeles",
]


def main():
    ok = True

    # --- distance sanity ---
    d_la = haversine_miles(*HOME, 34.0522, -118.2437)
    d_sd = haversine_miles(*HOME, 32.7157, -117.1611)
    d_ven = haversine_miles(*HOME, 34.2746, -119.2290)
    print(f"Fontana -> Los Angeles : {d_la:6.1f} mi  (expect ~46, in radius)")
    print(f"Fontana -> San Diego   : {d_sd:6.1f} mi  (expect ~96, in radius)")
    print(f"Fontana -> Ventura     : {d_ven:6.1f} mi  (expect ~103, OUT of radius)")
    assert d_la < R and d_sd < R and d_ven > R
    print()

    # --- IN RADIUS must imply California ---
    inside = [c for c, (la, lo) in CITIES.items()
              if haversine_miles(HOME[0], HOME[1], la, lo) <= R]
    non_ca = [c for c in inside if not c.endswith(", ca")]
    print(f"mapped cities within {R} mi : {len(inside)}")
    print(f"  of which non-California   : {non_ca or 'none'}")
    assert not non_ca, f"IN RADIUS no longer implies California: {non_ca}"
    print()

    print(f"strict mode (california_only AND require_voice_match): {STRICT}")
    print()

    # --- fixture pipeline ---
    print(f"{'FIRE?':<7}{'EXPECT':<8}{'SCORE':<7}{'GEO':<24}TITLE")
    print("-" * 104)
    for expected, listing in FIXTURES:
        fired, g, v = decide(listing, cfg, HOME, R)
        status = "PASS" if fired == expected else "**FAIL**"
        if fired != expected:
            ok = False
        geo_txt = f"{g.reason} {g.miles or ''}".strip() if g else "-"
        print(f"{str(fired):<7}{str(expected):<8}{v.score:<7}{geo_txt:<24}"
              f"{listing.title[:44]}  {status}")
    print()

    # --- the invariant, asserted rather than inferred ---
    if STRICT:
        fired_count = 0
        for _, listing in FIXTURES:
            fired, g, v = decide(listing, cfg, HOME, R)
            if not fired:
                continue
            fired_count += 1
            if g.reason not in ("IN RADIUS", "SOCAL REGION"):
                print(f"INVARIANT BREACH (geo={g.reason}): {listing.title[:60]}")
                ok = False
            if "VOICE MATCH" not in v.tags:
                print(f"INVARIANT BREACH (no voice match): {listing.title[:60]}")
                ok = False
        print(f"invariant: {fired_count} fixture(s) fired, all California + voice match")

        # --- real-world sweep -------------------------------------------
        leaked = []
        for title in REAL_WORLD_TITLES:
            l = Listing(title=title, url="https://example.com/x", source="Playbill",
                        summary=title)
            fired, g, v = decide(l, cfg, HOME, R)
            if fired:
                leaked.append((title, g.reason))
        print(f"real-world sweep: {len(REAL_WORLD_TITLES)} titles seen in live "
              f"runs, {len(leaked)} would fire")
        for t, why in leaked:
            print(f"  LEAK [{why}]: {t[:70]}")
            ok = False
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
    dupe = Listing(title=l.title, url=l.url + "?utm_source=x", source=l.source)
    print(f"dedup: same URL with ?utm params treated as new={is_new(con, dupe.key())}")
    print()

    # --- discord payload ---
    l0 = FIXTURES[0][1]
    fired, g0, v0 = decide(l0, cfg, HOME, R)
    embed = build_embed(l0, g0, v0)
    print("sample Discord embed:")
    print(json.dumps(embed, indent=2)[:500])
    assert len(embed["title"]) <= 256

    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
