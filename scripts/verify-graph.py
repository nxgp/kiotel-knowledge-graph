#!/usr/bin/env python3
"""Verify the Kiotel knowledge graph against the corpus it claims to describe.

Run after every rebuild, before publishing/redeploying:
    python scripts/verify-graph.py graphify-out/graph.json build-root

Checks (all read-only; exits non-zero only on a hard integrity failure):
  1. COVERAGE   - every corpus file has at least one node; lists any that don't
  2. PER-REPO   - node/edge counts per repo, so a silently-missing repo is obvious
  3. INTEGRITY  - dangling edge endpoints (internal ones are real breakage;
                  external library refs are dropped at build by design)
  4. PROVENANCE - EXTRACTED / INFERRED / AMBIGUOUS split
  5. CONNECTIVITY - connected components + which repos share the largest one
                  (this is what makes cross-repo questions answerable)
  6. BRIDGES    - every curated cross-repo bridge still resolves to real nodes
  7. SOURCE SPOT-CHECK - sample EXTRACTED code edges that carry a source_location
                  and confirm the file+line still exists on disk
"""
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPOS = [
    "kiotel_web", "kiotel-pms", "kiotel_hardware", "audio_services",
    "kiotel_space", "kiotel_admin", "kiotel_portal_front", "chatbot",
    "hr_kiotel_backend", "kiotel_lox", "stt_tts_inhouse", "guest_translation",
    "speech_to_text_serverless", "kiotel_pms_autofill",
]


def repo_of(path: str) -> str:
    for r in REPOS:
        if path.startswith(r + "/") or path == r:
            return r
    return "(other)"


def main(graph_path: str, build_root: str) -> int:
    g = json.loads(Path(graph_path).read_text(encoding="utf-8"))
    root = Path(build_root)
    nodes = g["nodes"]
    edges = g.get("links", g.get("edges", []))
    ids = {n["id"] for n in nodes}
    fail = False

    print(f"GRAPH: {len(nodes):,} nodes, {len(edges):,} edges\n")

    # 1 + 2. coverage and per-repo counts
    covered = {n.get("source_file", "") for n in nodes if n.get("source_file")}
    manifest_path = Path(graph_path).parent / "manifest.json"
    if manifest_path.exists():
        corpus = list(json.loads(manifest_path.read_text(encoding="utf-8")).keys())
        missing = sorted(f for f in corpus if f not in covered)
        pct = 100 * (len(corpus) - len(missing)) / len(corpus) if corpus else 0
        print(f"COVERAGE: {len(corpus)-len(missing):,}/{len(corpus):,} corpus files have nodes ({pct:.1f}%)")
        for m in missing[:15]:
            print(f"   no nodes: {m}")
        if len(missing) > 15:
            print(f"   ... and {len(missing)-15} more")
    else:
        print("COVERAGE: manifest.json not found - skipped")
    print()

    node_by_repo = Counter(repo_of(n.get("source_file", "")) for n in nodes)
    edge_by_repo = Counter(repo_of(e.get("source_file", "") or "") for e in edges)
    print("PER-REPO:")
    for r in REPOS:
        n, e = node_by_repo.get(r, 0), edge_by_repo.get(r, 0)
        flag = "  <-- EMPTY" if n == 0 else ""
        print(f"   {r:<28} {n:>6,} nodes  {e:>6,} edges{flag}")
        if n == 0:
            fail = True
    print(f"   {'(other/unattributed)':<28} {node_by_repo.get('(other)', 0):>6,} nodes")
    print()

    # 3. integrity
    dangling = [e for e in edges if e["source"] not in ids or e["target"] not in ids]
    internal_dangling = [
        e for e in dangling
        if any(str(v).startswith(tuple(r.replace("-", "_") for r in REPOS))
               for v in (e["source"], e["target"]) if v not in ids)
    ]
    selfloops = sum(1 for e in edges if e["source"] == e["target"])
    print(f"INTEGRITY: {len(dangling)} dangling edges "
          f"({len(internal_dangling)} internal = real breakage, rest are external libs)")
    print(f"           {selfloops} self-loops")
    if internal_dangling:
        fail = True
        for e in internal_dangling[:10]:
            bad = e["source"] if e["source"] not in ids else e["target"]
            print(f"   INTERNAL DANGLING -> {bad}")
    print()

    # 4. provenance
    prov = Counter(e.get("confidence", "?") for e in edges)
    total = sum(prov.values()) or 1
    print("PROVENANCE:", "  ".join(
        f"{k} {v:,} ({100*v/total:.0f}%)" for k, v in prov.most_common()))
    print()

    # 5. connectivity
    try:
        import networkx as nx
        G = nx.Graph()
        G.add_nodes_from(ids)
        G.add_edges_from((e["source"], e["target"]) for e in edges
                         if e["source"] in ids and e["target"] in ids)
        comps = sorted(nx.connected_components(G), key=len, reverse=True)
        big = comps[0]
        repo_in_big = Counter(repo_of(n.get("source_file", ""))
                              for n in nodes if n["id"] in big)
        print(f"CONNECTIVITY: {len(comps)} components; largest holds "
              f"{len(big):,} nodes ({100*len(big)/len(ids):.0f}%)")
        joined = [r for r in REPOS if repo_in_big.get(r, 0) > 0]
        print(f"   repos in the largest component: {len(joined)}/{len(REPOS)}")
        for r in REPOS:
            c = repo_in_big.get(r, 0)
            if c == 0:
                print(f"   ISOLATED FROM MAIN COMPONENT: {r}")
    except ImportError:
        print("CONNECTIVITY: networkx unavailable - skipped")
    print()

    # 6. bridges still resolve
    bridges_path = Path(__file__).resolve().parent / "bridges.json"
    if bridges_path.exists():
        bridges = json.loads(bridges_path.read_text(encoding="utf-8"))["bridges"]
        broken = [b for b in bridges if b["source"] not in ids or b["target"] not in ids]
        present = sum(1 for b in bridges
                      if any(e["source"] == b["source"] and e["target"] == b["target"]
                             for e in edges))
        print(f"BRIDGES: {len(bridges)} curated; {present} present in graph; "
              f"{len(broken)} reference missing nodes")
        for b in broken[:8]:
            print(f"   BROKEN: {b['source']} -> {b['target']}")
    print()

    # 7. source spot-check
    sample_pool = [
        e for e in edges
        if e.get("confidence") == "EXTRACTED" and e.get("source_location")
        and str(e.get("source_file", "")).endswith((".py", ".ts", ".tsx", ".js", ".cs", ".go"))
    ]
    random.seed(11)
    sample = random.sample(sample_pool, min(15, len(sample_pool)))
    ok = bad = 0
    print(f"SOURCE SPOT-CHECK: verifying {len(sample)} random EXTRACTED edges on disk")
    for e in sample:
        f = root / e["source_file"]
        loc = str(e["source_location"]).lstrip("L")
        if not f.exists():
            print(f"   MISSING FILE: {e['source_file']}")
            bad += 1
            continue
        try:
            n_lines = sum(1 for _ in f.open(encoding="utf-8", errors="replace"))
            if loc.isdigit() and int(loc) <= n_lines:
                ok += 1
            else:
                print(f"   LINE OUT OF RANGE: {e['source_file']}:{loc} (file has {n_lines})")
                bad += 1
        except Exception as exc:
            print(f"   UNREADABLE: {e['source_file']} ({exc})")
            bad += 1
    print(f"   {ok}/{len(sample)} resolve to a real file+line")
    if bad:
        fail = True
    print()

    print("RESULT:", "FAIL - integrity issues above" if fail else "PASS")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main(
        sys.argv[1] if len(sys.argv) > 1 else "graphify-out/graph.json",
        sys.argv[2] if len(sys.argv) > 2 else "build-root",
    ))
