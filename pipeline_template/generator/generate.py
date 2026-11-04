"""Seeded synthetic data for a small eyewear retailer.

One batch per night, written as CSV files under <out>/night=YYYY-MM-DD/<table>.csv:

    customers    new sign-ups that night (night 1 carries the initial customer base)
    products     full product snapshot every night
    orders       orders placed that night (local store time, UTC-05:00, no offset in the value)
    order_items  the lines of those orders
    inventory    on-hand snapshot per product at close of that night
    shipments    shipments that left the warehouse that night

Same seed and nights -> byte-identical files. Uses only the standard library.

    python generate.py --seed 7 --out ../data --start 2026-03-01 --nights 7
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import random
from pathlib import Path

TABLES = ("customers", "products", "orders", "order_items", "inventory", "shipments")

COLUMNS = {
    "customers": ["customer_id", "email", "region", "signup_date"],
    "products": ["product_id", "sku", "name", "category", "list_price"],
    "orders": ["order_id", "customer_id", "customer_email", "order_ts", "status"],
    "order_items": ["order_item_id", "order_id", "product_id", "quantity", "unit_price"],
    "inventory": ["product_id", "snapshot_date", "on_hand"],
    "shipments": ["shipment_id", "order_id", "service", "promised_hours", "shipped_ts"],
}

REGIONS = ["northeast", "southeast", "midwest", "southwest", "west"]
CATEGORIES = {
    # category: (count, price range, quantity range, popularity weight)
    "frames": (14, (79, 249), (1, 1), 4),
    "lenses": (10, (39, 189), (1, 2), 3),
    "sunglasses": (10, (99, 329), (1, 1), 2),
    "contacts": (6, (24, 69), (1, 4), 3),
}
FRAME_WORDS = ["Harbor", "Juniper", "Atlas", "Marlow", "Quincy", "Rowan", "Sable", "Tilde",
               "Wren", "Cleo", "Dune", "Hollis", "Ines", "Ludo", "Noor", "Pike"]
# orders per weekday relative to the mean (Mon..Sun)
WEEKDAY = [0.85, 0.9, 0.95, 1.0, 1.1, 1.3, 1.2]
BASE_ORDERS = 110
INITIAL_CUSTOMERS = 300


def nights(start: str, count: int) -> list:
    first = dt.date.fromisoformat(start)
    return [(first + dt.timedelta(days=i)).isoformat() for i in range(count)]


def _money(value: float) -> str:
    return f"{value:.2f}"


def _ts(value: dt.datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def generate(seed: int, start: str = "2026-03-01", count: int = 7) -> dict:
    """All batches: {night: {table: [row dict, ...]}}."""
    rng = random.Random(seed)
    days = nights(start, count)
    batches: dict = {night: {t: [] for t in TABLES} for night in days}

    products = []
    n = 0
    for category, (size, (low, high), qty, _) in CATEGORIES.items():
        for i in range(size):
            n += 1
            word = FRAME_WORDS[(n * 7 + seed) % len(FRAME_WORDS)]
            products.append({
                "product_id": f"P{n:03d}",
                "sku": f"{category[:3].upper()}-{n:03d}-{rng.randint(100, 999)}",
                "name": f"{word} {category[:-1] if category.endswith('s') else category} {i + 1}",
                "category": category,
                "list_price": _money(rng.randint(low, high) - 0.01 + 0.01 * rng.choice([0, 1])),
            })
    by_category: dict = {}
    for p in products:
        by_category.setdefault(p["category"], []).append(p)
    weights = [CATEGORIES[c][3] for c in by_category]

    customers = []

    def new_customer(night: str, signup: str) -> dict:
        cid = f"C{len(customers) + 1:05d}"
        row = {
            "customer_id": cid,
            "email": f"user{len(customers) + 1:05d}.{rng.randint(10, 99)}@example.com",
            "region": rng.choice(REGIONS),
            "signup_date": signup,
        }
        customers.append(row)
        batches[night]["customers"].append(row)
        return row

    first = dt.date.fromisoformat(days[0])
    for _ in range(INITIAL_CUSTOMERS):
        signup = first - dt.timedelta(days=rng.randint(1, 720))
        new_customer(days[0], signup.isoformat())

    stock = {p["product_id"]: rng.randint(40, 160) for p in products}
    order_n = item_n = ship_n = 0
    pending_shipments = []  # (shipped datetime, row)

    for night in days:
        date = dt.date.fromisoformat(night)
        for _ in range(rng.randint(3, 12)):
            new_customer(night, night)
        batches[night]["products"] = [dict(p) for p in products]

        expected = BASE_ORDERS * WEEKDAY[date.weekday()]
        count_orders = max(20, int(rng.gauss(expected, expected * 0.07)))
        minutes = sorted(rng.randint(8 * 60, 23 * 60 + 59) for _ in range(count_orders))
        sold: dict = {}
        for minute in minutes:
            order_n += 1
            oid = f"O{order_n:06d}"
            customer = rng.choice(customers)
            placed = dt.datetime.combine(date, dt.time()) + dt.timedelta(minutes=minute, seconds=rng.randint(0, 59))
            status = "returned" if rng.random() < 0.05 else "placed"
            batches[night]["orders"].append({
                "order_id": oid,
                "customer_id": customer["customer_id"],
                "customer_email": customer["email"],
                "order_ts": _ts(placed),
                "status": status,
            })
            for _ in range(rng.choice([1, 1, 1, 2, 2, 3])):
                category = rng.choices(list(by_category), weights=weights)[0]
                product = rng.choice(by_category[category])
                low_q, high_q = CATEGORIES[category][2]
                quantity = rng.randint(low_q, high_q)
                price = float(product["list_price"])
                if rng.random() < 0.12:
                    price = round(price * 0.9, 2)
                item_n += 1
                batches[night]["order_items"].append({
                    "order_item_id": f"I{item_n:07d}",
                    "order_id": oid,
                    "product_id": product["product_id"],
                    "quantity": str(quantity),
                    "unit_price": _money(price),
                })
                sold[product["product_id"]] = sold.get(product["product_id"], 0) + quantity
            if status == "placed":
                service = "express" if rng.random() < 0.25 else "standard"
                roll = rng.random()
                if roll < 0.7:
                    hours = rng.uniform(2, 20)
                elif roll < 0.95:
                    hours = rng.uniform(20, 40)
                else:
                    hours = rng.uniform(40, 90)
                ship_n += 1
                shipped = placed + dt.timedelta(minutes=int(hours * 60))
                pending_shipments.append((shipped, {
                    "shipment_id": f"S{ship_n:06d}",
                    "order_id": oid,
                    "service": service,
                    "promised_hours": "24" if service == "express" else "48",
                    "shipped_ts": _ts(shipped),
                }))
        for pid in stock:
            stock[pid] = max(0, stock[pid] - sold.get(pid, 0))
            if stock[pid] < 25 and rng.random() < 0.5:
                stock[pid] += rng.randint(60, 120)  # restock
            batches[night]["inventory"].append({
                "product_id": pid, "snapshot_date": night, "on_hand": str(stock[pid]),
            })

    for shipped, row in sorted(pending_shipments, key=lambda pair: (pair[0], pair[1]["shipment_id"])):
        night = shipped.date().isoformat()
        if night in batches:  # later shipments have not happened yet
            batches[night]["shipments"].append(row)
    return batches


def to_csv(rows: list, columns: list) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def write(batches: dict, out: Path, columns: dict | None = None) -> None:
    """Write batches as CSV. `columns` overrides the header per (night, table) key."""
    columns = columns or {}
    for night, tables in batches.items():
        folder = Path(out) / f"night={night}"
        folder.mkdir(parents=True, exist_ok=True)
        for table, rows in tables.items():
            header = columns.get((night, table)) or COLUMNS[table]
            (folder / f"{table}.csv").write_text(to_csv(rows, header))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--start", default="2026-03-01")
    parser.add_argument("--nights", type=int, default=7)
    args = parser.parse_args()
    write(generate(args.seed, args.start, args.nights), Path(args.out))


if __name__ == "__main__":
    main()
