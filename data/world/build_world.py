"""Build the Wayfarer Supply Co. world as a deterministic SQLite database.

Run:  python data/world/build_world.py

Determinism is the whole point. Two rules hold everywhere in this file:

  1. Every random draw comes from a Random seeded off facts.yaml.
  2. Nothing reads the system clock. "Now" is world.now from facts.yaml.

Break either one and the committed corpus stops matching the committed
expected outcomes, which silently corrupts every lab downstream.
"""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FACTS_PATH = ROOT / "data" / "world" / "facts.yaml"
DB_PATH = ROOT / "data" / "world" / "wayfarer.db"

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required.  pip install -r requirements.txt")


# ---------------------------------------------------------------------------
# Vocabulary. Fixed lists, never randomly generated, so names stay stable.
# ---------------------------------------------------------------------------

BRANDS = [
    "Grantline", "Kestrel Ridge", "Northfall", "Alder & Pine", "Cairn Works",
    "Tidewater", "Summit Line", "Hollowpeak",
]

CATEGORIES = {
    "tent":      ["Ridgeline 2P", "Basecamp 4P", "Spire Ultralight", "Vestibule Pro"],
    "pack":      ["Traverse 55", "Daybreak 22", "Haul 70", "Summit Pack 40"],
    "footwear":  ["Scree Mid GTX", "Riverbed Low", "Alpine Approach", "Tundra Boot"],
    "sleep":     ["Down 20F Bag", "Synthetic 30F Bag", "Insulated Pad", "Quilt 40F"],
    "stove":     ["Pocket Stove", "Windshield Burner", "Canister Stove Duo"],
    "fuel":      ["Isobutane 230g", "Isobutane 450g", "White Gas 1L"],
    "apparel":   ["Storm Shell", "Fleece Mid", "Sun Hoody", "Trail Pant"],
    "hydration": ["Filter Bottle", "Gravity Filter", "Insulated Flask"],
}

# Categories whose items are hazardous and cannot travel by air.
HAZMAT_CATEGORIES = {"fuel", "stove"}

FIRST_NAMES = [
    "Ana", "Ben", "Cara", "Dev", "Elin", "Femi", "Gus", "Hana", "Ivo", "Jun",
    "Kira", "Liam", "Mira", "Nils", "Oona", "Piotr", "Quinn", "Rhea", "Sami",
    "Tova", "Uma", "Viktor", "Wren", "Xiu", "Yara", "Zane",
]

LAST_NAMES = [
    "Alvarez", "Bhatt", "Chen", "Dahl", "Eze", "Ferrari", "Gomes", "Haugen",
    "Ibrahim", "Jansen", "Kowalski", "Lindqvist", "Moreau", "Nakamura",
    "Oyelaran", "Petrov", "Quiroga", "Rasmussen", "Silva", "Takahashi",
    "Ustinov", "Vargas", "Whitfield", "Xu", "Yildiz", "Zhao",
]

RETURN_REASONS = [
    "wrong_size", "not_as_described", "changed_mind", "arrived_damaged",
    "defect", "zipper_failure", "seam_failure", "ordinary_wear",
]

SCHEMA = """
CREATE TABLE customers (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    email        TEXT NOT NULL UNIQUE,
    tier         TEXT NOT NULL CHECK (tier IN ('standard','silver','gold')),
    region       TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE products (
    id              INTEGER PRIMARY KEY,
    sku             TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    brand           TEXT NOT NULL,
    category        TEXT NOT NULL,
    price_cents     INTEGER NOT NULL,
    is_final_sale   INTEGER NOT NULL DEFAULT 0,
    is_hazmat       INTEGER NOT NULL DEFAULT 0,
    warranty_months INTEGER NOT NULL,
    is_recalled     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE orders (
    id              INTEGER PRIMARY KEY,
    customer_id     INTEGER NOT NULL REFERENCES customers(id),
    placed_at       TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('pending','shipped','delivered','cancelled')),
    shipping_region TEXT NOT NULL,
    total_cents     INTEGER NOT NULL
);

CREATE TABLE order_items (
    id                 INTEGER PRIMARY KEY,
    order_id           INTEGER NOT NULL REFERENCES orders(id),
    product_id         INTEGER NOT NULL REFERENCES products(id),
    qty                INTEGER NOT NULL,
    unit_price_cents   INTEGER NOT NULL,
    condition_reported TEXT CHECK (condition_reported IN ('new','opened','used','damaged'))
);

CREATE TABLE shipments (
    id           INTEGER PRIMARY KEY,
    order_id     INTEGER NOT NULL REFERENCES orders(id),
    carrier      TEXT NOT NULL,
    tracking     TEXT NOT NULL,
    shipped_at   TEXT,
    delivered_at TEXT,
    status       TEXT NOT NULL CHECK (status IN ('pending','in_transit','delivered','lost'))
);

CREATE TABLE returns (
    id            INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL REFERENCES orders(id),
    order_item_id INTEGER NOT NULL REFERENCES order_items(id),
    opened_at     TEXT NOT NULL,
    reason        TEXT NOT NULL,
    state         TEXT NOT NULL CHECK (state IN ('open','approved','denied','refunded')),
    refund_cents  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_items_order     ON order_items(order_id);
CREATE INDEX idx_ship_order      ON shipments(order_id);
CREATE INDEX idx_returns_item    ON returns(order_item_id);
"""


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def build(facts: dict) -> dict:
    gen = facts["generation"]
    rng = random.Random(facts["world"]["seed"])
    now = parse_iso(facts["world"]["now"])
    regions = facts["shipping"]["regions"]
    carriers = facts["shipping"]["carriers"]
    lifetime_brands = set(facts["warranty"]["lifetime_brands"])
    recalled_skus = set(facts["escalation"]["recalled_skus"])
    tier_windows = facts["returns"]["tier_window_days"]
    edge = gen["edge_case_targets"]

    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)

    # --- customers ---------------------------------------------------------
    tiers, weights = zip(*gen["tier_weights"].items())
    customers = []
    seen_emails = set()
    for cid in range(1, gen["customers"] + 1):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        email = f"{first}.{last}{cid}".lower() + "@example.com"
        assert email not in seen_emails
        seen_emails.add(email)
        created = now - timedelta(days=rng.randint(40, 1400))
        customers.append((
            cid, f"{first} {last}", email,
            rng.choices(tiers, weights=weights, k=1)[0],
            rng.choice(regions), iso(created),
        ))
    conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?)", customers)

    # --- products ----------------------------------------------------------
    # Built as dicts, not positional tuples. An earlier version indexed these
    # by position and read prod[6] (is_final_sale) where prod[5] (price_cents)
    # was meant, which priced every line item at 0 or 1 cent. Nothing crashed.
    # Every refund silently computed to $0.00 and the whole corpus would have
    # shipped that way. Named fields make that class of bug impossible.
    cat_names = [(c, n) for c, names in CATEGORIES.items() for n in names]
    products = []
    for pid in range(1, gen["products"] + 1):
        category, base = cat_names[(pid - 1) % len(cat_names)]
        brand = BRANDS[(pid * 3) % len(BRANDS)]
        # Nudge brand distribution toward the lifetime target.
        if rng.random() < edge["lifetime_brand_pct"]:
            brand = rng.choice(sorted(lifetime_brands))
        sku = f"WS-{category[:3].upper()}-{pid:04d}"
        price = rng.choice([2900, 4900, 7900, 12900, 18900, 24900, 34900, 52900])
        is_hazmat = 1 if category in HAZMAT_CATEGORIES else 0
        is_final = 1 if rng.random() < edge["final_sale_pct"] else 0
        warranty = 0 if brand in lifetime_brands else facts["warranty"]["default_months"]
        products.append({
            "id": pid, "sku": sku, "name": f"{brand} {base}", "brand": brand,
            "category": category, "price_cents": price,
            "is_final_sale": is_final, "is_hazmat": is_hazmat,
            "warranty_months": warranty,
            "is_recalled": 1 if sku in recalled_skus else 0,
        })

    PRODUCT_COLS = ("id", "sku", "name", "brand", "category", "price_cents",
                    "is_final_sale", "is_hazmat", "warranty_months", "is_recalled")
    conn.executemany(
        "INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?)",
        [tuple(p[c] for c in PRODUCT_COLS) for p in products],
    )

    prod_by_id = {p["id"]: p for p in products}
    cust_by_id = {c[0]: c for c in customers}
    recalled_ids = [p["id"] for p in products if p["is_recalled"]]

    # Guard: a recalled SKU that does not exist in the catalog would silently
    # produce zero recall scenarios, and the gap would not surface until a
    # learner wondered why the escalation failure mode never fires.
    generated_skus = {p["sku"] for p in products}
    missing = sorted(recalled_skus - generated_skus)
    if missing:
        raise SystemExit(
            f"facts.yaml lists recalled SKUs that the catalog never generates: {missing}\n"
            f"Fix escalation.recalled_skus, or the recall scenarios will be empty."
        )

    # --- orders, items, shipments -----------------------------------------
    orders, items, shipments = [], [], []
    item_id = 0
    forced_recall_orders = set(
        rng.sample(range(1, gen["orders"] + 1), edge["recalled_item_orders"])
    ) if recalled_ids else set()

    for oid in range(1, gen["orders"] + 1):
        cust = cust_by_id[rng.randint(1, gen["customers"])]
        tier = cust[3]
        window = tier_windows[tier]

        # Choose delivery age first, then work backwards, so the share of
        # orders sitting outside the return window is controlled rather than
        # emergent. This is what makes the corpus interesting to analyze.
        outside = rng.random() < edge["delivered_outside_window_pct"]
        if outside:
            days_since_delivery = rng.randint(window + 1, edge["max_delivery_age_days"])
        else:
            days_since_delivery = rng.randint(0, max(window - 1, 1))

        transit_days = rng.randint(2, 9)
        handling_days = rng.randint(0, 3)
        delivered_at = now - timedelta(days=days_since_delivery, hours=rng.randint(0, 23))
        shipped_at = delivered_at - timedelta(days=transit_days)
        placed_at = shipped_at - timedelta(days=handling_days)

        roll = rng.random()
        if roll < 0.80:
            status, ship_status = "delivered", "delivered"
        elif roll < 0.90:
            status, ship_status = "shipped", "in_transit"
            delivered_at = None
        elif roll < 0.97:
            status, ship_status = "pending", "pending"
            delivered_at, shipped_at = None, None
        else:
            status, ship_status = "cancelled", "pending"
            delivered_at, shipped_at = None, None

        n_items = rng.randint(1, gen["max_items_per_order"])
        chosen = rng.sample(range(1, gen["products"] + 1), n_items)
        if oid in forced_recall_orders:
            chosen[0] = rng.choice(recalled_ids)

        total = 0
        for pid in chosen:
            item_id += 1
            prod = prod_by_id[pid]
            qty = rng.randint(1, 2)
            unit = prod["price_cents"]
            total += unit * qty
            if status == "delivered":
                cond_roll = rng.random()
                if cond_roll < edge["damaged_condition_pct"]:
                    cond = "damaged"
                elif cond_roll < 0.42:
                    cond = "used"
                elif cond_roll < 0.70:
                    cond = "opened"
                else:
                    cond = "new"
            else:
                cond = None
            items.append((item_id, oid, pid, qty, unit, cond))

        orders.append((oid, cust[0], iso(placed_at), status, cust[4], total))
        shipments.append((
            oid, oid, rng.choice(carriers), f"1Z{rng.randrange(10**9, 10**10)}",
            iso(shipped_at) if shipped_at else None,
            iso(delivered_at) if delivered_at else None,
            ship_status,
        ))

    conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?)", orders)
    conn.executemany("INSERT INTO order_items VALUES (?,?,?,?,?,?)", items)
    conn.executemany("INSERT INTO shipments VALUES (?,?,?,?,?,?,?)", shipments)

    # --- a handful of pre-existing returns --------------------------------
    delivered_items = [
        it for it in items
        if it[5] is not None and orders[it[1] - 1][3] == "delivered"
    ]
    returns = []
    for rid, it in enumerate(rng.sample(delivered_items, min(120, len(delivered_items))), start=1):
        opened = now - timedelta(days=rng.randint(1, 60))
        state = rng.choice(["open", "approved", "denied", "refunded"])
        refund = it[4] * it[3] if state == "refunded" else 0
        returns.append((rid, it[1], it[0], iso(opened), rng.choice(RETURN_REASONS), state, refund))
    conn.executemany("INSERT INTO returns VALUES (?,?,?,?,?,?,?)", returns)

    conn.commit()

    stats = {
        "customers": len(customers),
        "products": len(products),
        "orders": len(orders),
        "order_items": len(items),
        "shipments": len(shipments),
        "returns": len(returns),
        "final_sale_products": sum(1 for p in products if p["is_final_sale"]),
        "hazmat_products": sum(1 for p in products if p["is_hazmat"]),
        "lifetime_products": sum(1 for p in products if p["brand"] in lifetime_brands),
        "recalled_products": len(recalled_ids),
        "min_item_price_cents": min(it[4] for it in items),
        "median_order_total_cents": sorted(o[5] for o in orders)[len(orders) // 2],
        "delivered_orders": sum(1 for o in orders if o[3] == "delivered"),
        "pending_orders": sum(1 for o in orders if o[3] == "pending"),
        "damaged_items": sum(1 for it in items if it[5] == "damaged"),
    }
    conn.close()

    # Sanity assertions on the generated world. These are cheap, and each one
    # corresponds to a bug that actually shipped into an earlier revision.
    if stats["min_item_price_cents"] <= 0:
        raise SystemExit(
            "line items priced at zero. Every refund would compute to $0.00 and "
            "the authorization limit would never be exercised."
        )
    if stats["recalled_products"] == 0:
        raise SystemExit("no recalled products generated. Recall scenarios would be empty.")
    if stats["delivered_orders"] < 100:
        raise SystemExit("too few delivered orders to support return scenarios.")

    return stats


def main() -> None:
    facts = yaml.safe_load(FACTS_PATH.read_text())
    stats = build(facts)

    digest = hashlib.sha256(DB_PATH.read_bytes()).hexdigest()
    stats["sha256"] = digest

    out = ROOT / "data" / "world" / "world_stats.json"
    out.write_text(json.dumps(stats, indent=2) + "\n")

    width = max(len(k) for k in stats)
    print(f"built {DB_PATH.relative_to(ROOT)}")
    for k, v in stats.items():
        print(f"  {k.ljust(width)}  {v}")


if __name__ == "__main__":
    main()
