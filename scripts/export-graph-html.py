#!/usr/bin/env python3
"""Write graphify-out/graph.html as an AGGREGATED community view.

The full graph (36k nodes) is far above Graphify's 5,000-node limit for a node-level HTML
visualization, so `graphify cluster-only` skips graph.html. This script asks Graphify's own
exporter for the aggregated meta-graph instead (one node per community, edges = number of
cross-community edges) — the same view the dashboard's Graph tab embeds.

    python scripts/export-graph-html.py [graphify-out/graph.json]

Run after the repairs + cluster-only, before building the Docker image.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import networkx as nx
from graphify.exporters.html import to_html


def main(graph_path: str) -> int:
    gp = Path(graph_path)
    g = json.loads(gp.read_text(encoding="utf-8"))
    G = nx.Graph()
    communities = defaultdict(list)
    for n in g["nodes"]:
        G.add_node(n["id"])
        if n.get("community") is not None:
            communities[int(n["community"])].append(n["id"])
    G.add_edges_from((e["source"], e["target"]) for e in g.get("links", g.get("edges", []))
                     if e["source"] in G and e["target"] in G)
    labels_path = gp.parent / ".graphify_labels.json"
    labels = {}
    if labels_path.exists():
        labels = {int(k): v for k, v in json.loads(labels_path.read_text(encoding="utf-8")).items()}
    out = gp.parent / "graph.html"
    to_html(G, dict(communities), str(out), community_labels=labels, node_limit=5000)
    print(f"wrote {out} ({out.stat().st_size/1e6:.2f} MB), {len(communities)} communities")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "graphify-out/graph.json"))
