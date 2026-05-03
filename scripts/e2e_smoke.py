"""End-to-end smoke test for the wargame engine.

Drives every unit type through every available action (MOVE / STRIKE /
OVERWATCH / SCOUT / HOLD), exercises the radar toggle endpoint, and
sanity-checks win conditions. Run after any change touching the engine,
catalog, or server.
"""
from __future__ import annotations

import json
import sys

import requests


API = "http://localhost:5173"


def get_state() -> dict:
    r = requests.get(f"{API}/state.json", timeout=5)
    r.raise_for_status()
    return r.json()


def reroll() -> None:
    requests.post(f"{API}/api/reroll", timeout=10).raise_for_status()


def reset_turn() -> None:
    requests.post(f"{API}/api/turn/reset", timeout=5).raise_for_status()


def post_orders(side: str, orders: list[dict], lock: bool = True) -> dict:
    r = requests.post(
        f"{API}/api/orders",
        json={"side": side, "orders": orders, "lock": lock},
        timeout=5,
    )
    r.raise_for_status()
    return r.json()


def resolve() -> dict:
    r = requests.post(f"{API}/api/resolve", timeout=10)
    r.raise_for_status()
    return r.json()


def toggle_sensor(unit_id: str, sensor_key: str, active: bool) -> dict:
    r = requests.post(
        f"{API}/api/sensor/toggle",
        json={"unit_id": unit_id, "sensor_key": sensor_key, "active": active},
        timeout=5,
    )
    r.raise_for_status()
    return r.json()


def find_unit(state: dict, unit_id: str) -> dict | None:
    return next((u for u in state["units"] if u["id"] == unit_id), None)


def find_first_by_type(state: dict, side: str, utype: str) -> dict | None:
    return next(
        (u for u in state["units"] if u["side"] == side and u["type"] == utype),
        None,
    )


def hexd(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Offset hex distance (odd-r)."""
    def to_cube(c: int, r: int) -> tuple[int, int, int]:
        x = c - (r - (r & 1)) // 2
        z = r
        y = -x - z
        return x, y, z
    ax, ay, az = to_cube(*a)
    bx, by, bz = to_cube(*b)
    return (abs(ax - bx) + abs(ay - by) + abs(az - bz)) // 2


# ----------------------------------------------------------------------
# Tests

ALL_UNIT_TYPES = [
    "infantry", "armor", "missile_launcher", "scout_drone",
    "strike_drone", "fighter", "bomber", "destroyer",
]
PASS = 0
FAIL = 0
errors: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        errors.append(f"{label} — {detail}")
        print(f"  ✗ {label}  {detail}")


# ----------------------------------------------------------------------

def test_state_schema() -> None:
    print("\n[1] state.json schema")
    s = get_state()
    check("name", "name" in s)
    check("turn", "turn" in s)
    check("victory", "victory" in s and "hp_loss_threshold" in s["victory"])
    check("units", isinstance(s.get("units"), list) and len(s["units"]) >= 8)
    check("bases", isinstance(s.get("bases"), list))
    # Sensors all have is_active
    for u in s["units"]:
        for sn in u.get("sensors", []):
            if "is_active" not in sn:
                check(f"sensor {u['id']}/{sn['key']} has is_active", False)
                return
    check("all sensors have is_active", True)
    # Radars start inactive
    radars = [(u["id"], sn) for u in s["units"]
              for sn in u.get("sensors", []) if sn["modality"] == "radar"]
    check("radars start inactive", all(not sn["is_active"] for _, sn in radars))


def test_each_unit_can_move() -> None:
    print("\n[2] every speed > 0 unit can MOVE (uses engine reachable())")
    # Use the engine's own reachable() so we never pick an invalid hex.
    reroll()
    s = get_state()
    # Need direct engine access — load the same scenario locally.
    from engine.scenario import load_scenario
    from engine.movement import reachable as engine_reachable
    eng_state = load_scenario("scenarios/strait_n7.yaml")
    by_id = {u.id: u for u in eng_state.units}

    blue_orders = []
    for u in s["units"]:
        if u["side"] != "blue" or u["speed"] == 0:
            continue
        eng_unit = by_id.get(u["id"])
        if eng_unit is None:
            continue
        reach = engine_reachable(eng_state, eng_unit)
        # Pick the first hex that's NOT the start
        targets = [k for k, c in reach.items() if c > 0]
        if not targets:
            print(f"    (skip {u['id']}: nothing reachable)")
            continue
        tx, ty = targets[0]
        blue_orders.append({
            "kind": "MOVE", "unit_id": u["id"], "target_hex": [tx, ty],
        })
    reset_turn()
    post_orders("blue", blue_orders, lock=True)
    post_orders("red", [], lock=True)
    res = resolve()
    moved = {ev["unit"] for ev in res["events"] if ev["type"] == "move"}
    expected = {o["unit_id"] for o in blue_orders}
    check(
        f"all queued moves resolved ({len(moved)}/{len(expected)})",
        moved == expected,
        f"missing: {expected - moved}",
    )


def test_strike_per_unit_type() -> None:
    print("\n[3] every weaponed unit type can STRIKE (move-into-range then fire)")
    from engine.scenario import load_scenario
    from engine.movement import path_to as eng_path
    weaponed_types = [
        "infantry", "armor", "missile_launcher", "strike_drone",
        "fighter", "bomber", "destroyer",
    ]
    for utype in weaponed_types:
        reroll()
        s = get_state()
        atk = find_first_by_type(s, "blue", utype)
        if atk is None:
            check(f"strike {utype}", False, "no blue unit of type")
            continue
        weapon = atk["weapons"][0]
        # Find an eligible red target by domain
        eligible = [
            e for e in s["units"]
            if e["side"] == "red" and e["domain"] in weapon["target_domains"]
        ]
        if not eligible:
            check(f"strike {utype}", False, "no eligible red target by domain")
            continue
        # Closest eligible target
        eligible.sort(key=lambda e: hexd((atk["col"], atk["row"]), (e["col"], e["row"])))
        tgt = eligible[0]
        # Walk turns: each turn try to MOVE atk closer to tgt then STRIKE if in range.
        # Cap at 10 turns to avoid infinite loops.
        eng_state = load_scenario("scenarios/strait_n7.yaml")
        eng_atk = next(u for u in eng_state.units if u.id == atk["id"])
        eng_tgt = next(u for u in eng_state.units if u.id == tgt["id"])
        fired = False
        for turn in range(10):
            d = hexd((atk["col"], atk["row"]), (tgt["col"], tgt["row"]))
            if d <= weapon["range"]:
                reset_turn()
                post_orders("blue", [{
                    "kind": "STRIKE", "unit_id": atk["id"],
                    "target_hex": [tgt["col"], tgt["row"]],
                }], lock=True)
                post_orders("red", [], lock=True)
                res = resolve()
                ev = next((e for e in res["events"] if e["type"] == "strike"), None)
                fired = ev is not None
                break
            # Otherwise MOVE one step toward target
            path = eng_path(eng_state, eng_atk, tgt["col"], tgt["row"])
            if not path or len(path) < 2:
                break  # can't path
            step = path[1]
            reset_turn()
            post_orders("blue", [{
                "kind": "MOVE", "unit_id": atk["id"], "target_hex": list(step),
            }], lock=True)
            post_orders("red", [], lock=True)
            resolve()
            # Refresh
            s = get_state()
            atk = find_unit(s, atk["id"])
            tgt = find_unit(s, tgt["id"])
            if atk is None or tgt is None:
                break
            eng_state = load_scenario("scenarios/strait_n7.yaml")
            for u in eng_state.units:
                if u.id == atk["id"]:
                    u.col, u.row = atk["col"], atk["row"]
                    eng_atk = u
        check(f"strike {utype} (after closing distance)", fired,
              f"could not fire within 10 turns")


def test_radar_toggle() -> None:
    print("\n[4] radar toggle endpoint")
    reroll()
    s = get_state()
    ml = find_first_by_type(s, "blue", "missile_launcher")
    pre = ml["sensor"]
    radar = next(sn for sn in ml["sensors"] if sn["modality"] == "radar")
    # ON
    res = toggle_sensor(ml["id"], radar["key"], True)
    check("toggle ON returns ok", res.get("ok") is True)
    s = get_state()
    ml = find_unit(s, ml["id"])
    on_summary = ml["sensor"]
    check(
        "summary sensor expands when radar ON",
        on_summary > pre,
        f"pre={pre} on={on_summary}",
    )
    radar = next(sn for sn in ml["sensors"] if sn["modality"] == "radar")
    check("radar SensorRef.is_active reflects toggle", radar["is_active"] is True)
    # OFF
    toggle_sensor(ml["id"], radar["key"], False)
    s = get_state()
    ml = find_unit(s, ml["id"])
    check(
        "summary sensor returns to passive when OFF",
        ml["sensor"] == pre,
        f"after-off={ml['sensor']} expected={pre}",
    )


def test_overwatch_holds_fire() -> None:
    print("\n[5] OVERWATCH + HOLD orders accepted")
    reroll()
    s = get_state()
    fighter = find_first_by_type(s, "blue", "fighter")
    inf = find_first_by_type(s, "blue", "infantry")
    reset_turn()
    post_orders("blue", [
        {"kind": "OVERWATCH", "unit_id": fighter["id"]},
        {"kind": "HOLD", "unit_id": inf["id"]},
    ], lock=True)
    post_orders("red", [], lock=True)
    res = resolve()
    # No errors, turn advanced
    check("OVERWATCH+HOLD resolves cleanly", res.get("ok") is True)


def test_scout_drone_scout() -> None:
    print("\n[6] SCOUT order on scout_drone")
    reroll()
    s = get_state()
    sd = find_first_by_type(s, "blue", "scout_drone")
    if not sd:
        check("scout_drone present", False)
        return
    reset_turn()
    post_orders("blue", [
        {"kind": "SCOUT", "unit_id": sd["id"]},
    ], lock=True)
    post_orders("red", [], lock=True)
    res = resolve()
    reveal = [e for e in res["events"] if e["type"] == "scout_reveal"]
    check("SCOUT emits scout_reveal", len(reveal) == 1)


def test_air_can_cross_water() -> None:
    print("\n[7] air units traverse water + mountain")
    reroll()
    s = get_state()
    fighter = find_first_by_type(s, "blue", "fighter")
    if not fighter:
        check("fighter present", False)
        return
    from engine.scenario import load_scenario
    from engine.movement import reachable as engine_reachable
    eng = load_scenario("scenarios/strait_n7.yaml")
    eng_fig = next(u for u in eng.units if u.id == fighter["id"])
    reach = engine_reachable(eng, eng_fig)
    # Pick the FARTHEST reachable hex to exercise long-range pathing
    far = max(reach.items(), key=lambda kv: kv[1])
    chosen, cost = far
    reset_turn()
    post_orders("blue", [{
        "kind": "MOVE", "unit_id": fighter["id"], "target_hex": list(chosen),
    }], lock=True)
    post_orders("red", [], lock=True)
    res = resolve()
    ev = next((e for e in res["events"] if e["type"] == "move" and e["unit"] == fighter["id"]), None)
    check(
        f"fighter moves to {chosen} (cost {cost})",
        ev is not None and tuple(ev["path"][-1]) == chosen,
        f"event: {ev}",
    )


def test_spent_munitions_removal() -> None:
    print("\n[8] bomber removed after exhausting all 5 bombs")
    reroll()
    s = get_state()
    bom = find_first_by_type(s, "blue", "bomber")
    # Pick any red as target; the engine will whiff or hit but consume ammo
    tgt = next((u for u in s["units"] if u["side"] == "red"), None)
    if not bom or not tgt:
        check("bomber + red target present", False)
        return
    initial_ammo = bom["weapons"][0]["ammo"]
    # Force-target a specific land hex (just whatever's adjacent or in range)
    th = (bom["col"], bom["row"])  # we'll just queue strikes on own hex (whiff, but burns ammo)
    # Range 2 — own hex is 0 distance, OK.
    last_present = True
    for i in range(initial_ammo + 1):
        s = get_state()
        cur = find_unit(s, bom["id"])
        if cur is None:
            last_present = False
            print(f"    bomber gone after {i} strikes")
            break
        reset_turn()
        post_orders("blue", [{
            "kind": "STRIKE", "unit_id": bom["id"], "target_hex": list(th),
        }], lock=True)
        post_orders("red", [], lock=True)
        resolve()
    check(
        "bomber destroyed after spent munitions",
        not last_present,
        f"still present after {initial_ammo + 1} strikes",
    )


def test_kamikaze_self_destruct() -> None:
    print("\n[9] strike_drone dies on fire (kamikaze)")
    reroll()
    s = get_state()
    sd = find_first_by_type(s, "blue", "strike_drone")
    if not sd:
        check("strike_drone present", False)
        return
    # strike own hex (whiff target but still self-destructs, range 1 covers self though...)
    # Actually we changed kamikaze range to 1, so self hex is fine (distance 0 ≤ 1)
    reset_turn()
    post_orders("blue", [{
        "kind": "STRIKE", "unit_id": sd["id"], "target_hex": [sd["col"], sd["row"]],
    }], lock=True)
    post_orders("red", [], lock=True)
    resolve()
    s2 = get_state()
    after = find_unit(s2, sd["id"])
    check("strike_drone gone after self-destruct", after is None)


# ----------------------------------------------------------------------

def main() -> int:
    test_state_schema()
    test_each_unit_can_move()
    test_strike_per_unit_type()
    test_radar_toggle()
    test_overwatch_holds_fire()
    test_scout_drone_scout()
    test_air_can_cross_water()
    test_spent_munitions_removal()
    test_kamikaze_self_destruct()

    print()
    print("=" * 60)
    print(f"PASS: {PASS}    FAIL: {FAIL}")
    if FAIL:
        print()
        print("Failures:")
        for e in errors:
            print(f"  - {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
