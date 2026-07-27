#!/usr/bin/env python3
"""Promote the kiotel_web DB schema to first-class graph nodes.

The Kiotel team's DB source of truth is:
  - kiotel_web/core/backend/src/db/db_schema_view.ts  (all tables/columns/enums — but it is
    ONE giant comment block, so AST extraction sees zero symbols in it)
  - kiotel_web/core/backend/src/db/migrations/        (61 knex migrations)

This script parses the schema reference and the migration filenames and injects:
  - one node per documented table  (kiotel_web_db_table_<name>, EXTRACTED from the doc line)
  - one node per table that a `*_create_<name>` migration creates but the schema doc does
    NOT document yet (labelled as such — the doc is stale relative to migrations)
  - edges: db_schema_view.ts --contains--> table, migration --references--> table (matched
    by filename segments, longest-match to avoid `tasks` vs `scheduled_tasks_info` clashes)

Idempotent. Run after apply-graph-repairs.py:
    python scripts/extract-db-schema-nodes.py graphify-out/graph.json [build-root]
"""
import json
import re
import sys
from pathlib import Path

SCHEMA_REL = "kiotel_web/core/backend/src/db/db_schema_view.ts"
MIG_REL = "kiotel_web/core/backend/src/db/migrations"
SCHEMA_NODE = "kiotel_web_core_backend_src_db_db_schema_view"


def mig_node_id(stem: str) -> str:
    return "kiotel_web_core_backend_src_db_migrations_" + re.sub(r"[^a-z0-9]+", "_", stem.lower())


def main(graph_path: str, build_root: str) -> None:
    root = Path(build_root)
    schema_file = root / SCHEMA_REL

    tables = {}  # name -> line
    for i, line in enumerate(schema_file.read_text(encoding="utf-8").splitlines(), 1):
        m = re.search(r"TABLE: ([a-z_][a-z0-9_]*)", line)
        if m:
            tables.setdefault(m.group(1), i)

    migs = sorted(p.stem for p in (root / MIG_REL).glob("*.ts"))

    p = Path(graph_path)
    g = json.loads(p.read_text(encoding="utf-8"))
    edge_key = "links" if "links" in g else "edges"
    ids = {n["id"] for n in g["nodes"]}
    have = {(e["source"], e["target"]) for e in g[edge_key]}
    added_n = added_e = 0

    def add_node(nid, label, sf, loc):
        nonlocal added_n
        if nid not in ids:
            g["nodes"].append({"id": nid, "label": label, "file_type": "document",
                               "source_file": sf, "source_location": loc})
            ids.add(nid); added_n += 1

    def add_edge(src, tgt, rel, sf, loc):
        nonlocal added_e
        if (src, tgt) not in have and src in ids and tgt in ids:
            g[edge_key].append({"source": src, "target": tgt, "relation": rel,
                                "confidence": "EXTRACTED", "confidence_score": 1.0,
                                "source_file": sf, "source_location": loc, "weight": 1.0})
            have.add((src, tgt)); added_e += 1

    for name, line in tables.items():
        nid = f"kiotel_web_db_table_{name}"
        add_node(nid, f"DB table: {name}", SCHEMA_REL, f"L{line}")
        add_edge(SCHEMA_NODE, nid, "contains", SCHEMA_REL, f"L{line}")

    # tables created by migrations but missing from the schema doc (doc is stale)
    undocumented = []
    for stem in migs:
        m = re.match(r"\d+_create_([a-z0-9_]+)$", stem)
        if m and m.group(1) not in tables:
            name = m.group(1)
            undocumented.append(name)
            nid = f"kiotel_web_db_table_{name}"
            add_node(nid, f"DB table: {name} (created by migration; NOT yet in db_schema_view.ts)",
                     f"{MIG_REL}/{stem}.ts", "L1")

    # link migrations to the tables their filename names (longest-match wins per overlap)
    all_names = sorted(set(tables) | {u for u in undocumented}, key=len, reverse=True)
    linked = 0
    for stem in migs:
        rest = re.sub(r"^\d+_", "", stem)
        hay = f"_{rest}_"
        matched = []
        for name in all_names:
            if f"_{name}_" in hay and not any(name != m and name in m for m in matched):
                matched.append(name)
        for name in matched:
            mid = mig_node_id(stem)
            if mid in ids:
                add_edge(mid, f"kiotel_web_db_table_{name}", "references",
                         f"{MIG_REL}/{stem}.ts", None)
                linked += 1

    p.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
    print(f"documented tables: {len(tables)}, undocumented (migration-only): {len(undocumented)}")
    if undocumented:
        print("  STALE db_schema_view.ts — missing:", ", ".join(sorted(undocumented)))
    print(f"added {added_n} nodes, {added_e} edges ({linked} migration->table links)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "graphify-out/graph.json",
         sys.argv[2] if len(sys.argv) > 2 else "build-root")
