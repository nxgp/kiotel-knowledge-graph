#!/usr/bin/env python3
"""Write the two small metadata files the MCP image bakes in alongside graph.json:

  graphify-out/file-sizes.json  {relative path -> bytes} of every file in build-root. The metrics
                                dashboard uses it to estimate tokens saved per cited file.
  graphify-out/cost.json        real token counts of each extraction run (appended).

    python scripts/write-build-metadata.py build-root graphify-out \
        --input-tokens 5126645 --output-tokens 392882 [--files 4837]

Token counts come from the `[graphify extract] tokens: N in / M out` line printed at the end
of `graphify extract`. Omit the --*-tokens flags to refresh file-sizes.json only.
"""
import argparse
import datetime
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("build_root")
    ap.add_argument("out_dir")
    ap.add_argument("--input-tokens", type=int)
    ap.add_argument("--output-tokens", type=int, default=0)
    ap.add_argument("--files", type=int)
    a = ap.parse_args()

    root, out = Path(a.build_root), Path(a.out_dir)
    sizes = {}
    n = 0
    for p in sorted(root.rglob("*")):
        if p.is_file() and "graphify-out" not in p.relative_to(root).parts:
            sizes[p.relative_to(root).as_posix()] = p.stat().st_size
            n += 1
    (out / "file-sizes.json").write_text(json.dumps(sizes), encoding="utf-8")
    print(f"file-sizes.json: {n} files")

    if a.input_tokens is not None:
        cp = out / "cost.json"
        cost = json.loads(cp.read_text(encoding="utf-8-sig")) if cp.exists() else {"runs": []}
        cost["runs"].append({
            "date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "input_tokens": a.input_tokens,
            "output_tokens": a.output_tokens,
            "files": a.files or n,
        })
        cost["total_input_tokens"] = sum(r["input_tokens"] for r in cost["runs"])
        cost["total_output_tokens"] = sum(r["output_tokens"] for r in cost["runs"])
        cp.write_text(json.dumps(cost, indent=2), encoding="utf-8")
        print(f"cost.json: {len(cost['runs'])} run(s)")


if __name__ == "__main__":
    main()
