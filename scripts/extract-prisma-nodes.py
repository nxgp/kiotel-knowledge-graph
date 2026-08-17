#!/usr/bin/env python3
"""Promote Prisma data models to first-class graph nodes.

kiotel-space (36 models / 7 enums) and kiotel-admin (25 models / 14 enums) define their
entire data model in a single `schema.prisma` file. tree-sitter has no Prisma grammar, so
the AST pass produces ZERO nodes for these — the source-of-truth schema of the two largest
new repos was invisible. This mirrors extract-db-schema-nodes.py (which did the same for
kiotel_web's db_schema_view.ts).

For each `model X { ... }` and `enum X { ... }` block it injects:
  - one node  (kiotel_space_prisma_model_<name> / kiotel_admin_prisma_enum_<name>)
  - contains edges: schema.prisma file-node --contains--> each model/enum
  - relation edges: model A --references--> model B where A has a field typed `B` (a
    Prisma relation), captured from the field lines

Idempotent. Run after apply-graph-repairs.py:
    python scripts/extract-prisma-nodes.py graphify-out/graph.json build-root
"""
import json
import re
import sys
from pathlib import Path

SCHEMAS = [
    ("kiotel_space", "kiotel_space/backend/prisma/schema.prisma"),
    ("kiotel_admin", "kiotel_admin/backend/prisma/schema.prisma"),
]

BLOCK_RE = re.compile(r"^(model|enum)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{", re.M)
# a field line like `author  User @relation(...)` or `posts Post[]` -> type token is 2nd word
FIELD_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*)")


def slug(repo, kind, name):
    return f"{repo}_prisma_{kind}_{re.sub(r'[^a-z0-9]+', '_', name.lower())}"


def main(graph_path: str, build_root: str) -> None:
    root = Path(build_root)
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

    def add_edge(s, t, rel, sf):
        nonlocal added_e
        if s in ids and t in ids and (s, t) not in have:
            g[edge_key].append({"source": s, "target": t, "relation": rel,
                                "confidence": "EXTRACTED", "confidence_score": 1.0,
                                "source_file": sf, "source_location": None, "weight": 1.0})
            have.add((s, t)); added_e += 1

    for repo, rel_path in SCHEMAS:
        f = root / rel_path
        if not f.exists():
            print(f"skip: {rel_path} not found")
            continue
        text = f.read_text(encoding="utf-8")
        lines = text.splitlines()
        # the schema.prisma file-node (from AST it has none, so make one)
        file_node = f"{repo}_backend_prisma_schema"
        add_node(file_node, f"{repo} Prisma schema (data model source of truth)",
                 rel_path, "L1")

        # collect all model/enum names + their line + the block body
        blocks = {}
        model_names = set()
        for m in BLOCK_RE.finditer(text):
            kind, name = m.group(1), m.group(2)
            line = text[:m.start()].count("\n") + 1
            # capture block body until the matching close brace (simple brace scan)
            depth = 0; body = []
            for ln in lines[line - 1:]:
                body.append(ln)
                depth += ln.count("{") - ln.count("}")
                if depth <= 0 and "}" in ln:
                    break
            blocks[name] = {"kind": kind, "line": line, "body": body[1:]}
            if kind == "model":
                model_names.add(name)

        for name, info in blocks.items():
            nid = slug(repo, info["kind"], name)
            label = f"{repo} Prisma {info['kind']}: {name}"
            add_node(nid, label, rel_path, f"L{info['line']}")
            add_edge(file_node, nid, "contains", rel_path)

        # relation edges: a model field whose type is another model
        for name, info in blocks.items():
            if info["kind"] != "model":
                continue
            src = slug(repo, "model", name)
            seen_targets = set()
            for ln in info["body"]:
                fm = FIELD_RE.match(ln)
                if not fm:
                    continue
                ftype = fm.group(2)
                if ftype in model_names and ftype != name and ftype not in seen_targets:
                    seen_targets.add(ftype)
                    add_edge(src, slug(repo, "model", ftype), "references", rel_path)

    p.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
    print(f"Prisma: added {added_n} nodes, {added_e} edges")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "graphify-out/graph.json",
         sys.argv[2] if len(sys.argv) > 2 else "build-root")
