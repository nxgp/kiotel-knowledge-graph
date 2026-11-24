#!/usr/bin/env python3
"""Nightly incremental update of the Kiotel knowledge graph.

    python scripts/nightly-update.py run                      # CI: clone all repos, update what changed
    python scripts/nightly-update.py run --mode check         # CI dry run: clone + assemble, no AI
    python scripts/nightly-update.py run --source local --repos-dir ../kiotel   # use existing clones
    python scripts/nightly-update.py init-state --repos-dir ../kiotel           # write the baseline

What "only update what changed" means here (two levels):
  1. Repo level. The latest commit of every repo is compared with graphify-out/source-state.json
     (the commits the published graph was built from). If nothing moved, the run stops
     immediately: no clone, no AI, nothing to publish.
  2. File level. Graphify keys its semantic cache by file content, so only new or edited docs/
     config files are sent to the AI; everything else is a cache hit. The semantic cache
     (graphify-out/cache/semantic-deep) is carried between runs by the workflow (actions/cache).
     Code files are parsed locally (tree-sitter) and cost no tokens, so the AST cache is NOT
     reused: it stores the absolute path of the build root inside node ids, so a cache made
     under one path produces renamed/missing nodes (and broken bridges) under another.
     The run prints the hit/miss counts and the tokens it actually spent.

Pipeline (same steps as RUNBOOK 'Rebuild'): shallow-clone all repos -> assemble build-root ->
graphify extract (incremental) -> DB/Prisma extractors -> repairs + bridges -> cluster/label ->
graph.html -> metadata -> verify-graph.py -> MCP smoke test -> (only if all passed and
--publish) copy into graphify-out/ and write source-state.json. Anything failing stops the run
before publish, so a bad build never replaces the good graph.

Logging is deliberately quiet: this repo is public and the code being graphed is private, so
only repo names, counts and token totals are printed. Tool output is kept in <work>/logs
(never uploaded). Use --verbose locally to see everything.
"""
import argparse
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
CONFIG_PATH = HERE / "scripts" / "nightly-config.json"
DEFAULT_STATE = HERE / "graphify-out" / "source-state.json"
PY = sys.executable


# Parts of Graphify's cache that embed the absolute build-root path (or stat data of the files
# on the machine that made it). Never carried between runs; see the module docstring.
UNPORTABLE_CACHE = ("ast", "stat-index.json", "last_query_stamp")


class StageError(Exception):
    pass


# ----------------------------------------------------------------------------- helpers
def load_assemble_module():
    spec = importlib.util.spec_from_file_location("assemble", HERE / "scripts" / "assemble-build-root.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_config():
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return cfg


def branch_for(cfg, repo):
    return cfg.get("branches", {}).get(repo, cfg.get("default_branch", "main"))


def git(*args, cwd=None, check=True):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise StageError(f"git {args[0]} failed (exit {r.returncode})")
    return r


def remote_sha(org, repo, branch):
    r = git("ls-remote", f"https://github.com/{org}/{repo}.git", f"refs/heads/{branch}", check=False)
    out = r.stdout.strip()
    if r.returncode != 0 or not out:
        return None
    return out.split()[0]


def local_head(path: Path):
    sha = git("rev-parse", "HEAD", cwd=path, check=False)
    br = git("rev-parse", "--abbrev-ref", "HEAD", cwd=path, check=False)
    if sha.returncode != 0:
        return None, None
    return sha.stdout.strip(), br.stdout.strip()


def load_state(path: Path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def gh_output(**kv):
    """Expose values to later workflow steps (no-op outside GitHub Actions)."""
    f = os.environ.get("GITHUB_OUTPUT")
    if f:
        with open(f, "a", encoding="utf-8") as fh:
            for k, v in kv.items():
                fh.write(f"{k}={v}\n")


def gh_summary(text: str):
    f = os.environ.get("GITHUB_STEP_SUMMARY")
    if f:
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


class Runner:
    """Runs a tool, keeps its full output in a log file, and prints only a safe summary."""

    def __init__(self, logdir: Path, verbose: bool):
        self.logdir, self.verbose = logdir, verbose
        logdir.mkdir(parents=True, exist_ok=True)
        self.n = 0

    def run(self, name, cmd, cwd=None, env=None, ok=(0,)):
        self.n += 1
        logfile = self.logdir / f"{self.n:02d}-{name}.log"
        t0 = time.time()
        r = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        text = (r.stdout or "") + ("\n--- stderr ---\n" + r.stderr if r.stderr else "")
        logfile.write_text(text, encoding="utf-8")
        if self.verbose:
            print(text)
        print(f"  [{'ok' if r.returncode in ok else 'FAIL'}] {name} ({time.time() - t0:.0f}s)")
        if r.returncode not in ok:
            raise StageError(f"stage '{name}' failed with exit {r.returncode} "
                             f"(full log kept on the runner: {logfile.name})")
        return r


# ----------------------------------------------------------------------------- stages
def resolve_current(args, repos, cfg):
    """{repo: {"branch":..., "sha":...}} for the latest code; raises listing unreadable repos."""
    cur, problems = {}, []
    for repo in repos:
        if args.source == "local":
            d = Path(args.repos_dir) / repo
            if not d.is_dir():
                problems.append(f"{repo}: no local clone")
                continue
            sha, br = local_head(d)
            if not sha:
                problems.append(f"{repo}: not a git repo")
                continue
            cur[repo] = {"branch": br, "sha": sha}
        else:
            br = branch_for(cfg, repo)
            sha = remote_sha(cfg["org"], repo, br)
            if not sha:
                problems.append(f"{repo}: cannot read branch '{br}' (missing repo/branch or token lacks access)")
                continue
            cur[repo] = {"branch": br, "sha": sha}
    if problems:
        raise StageError("cannot resolve every repo; refusing to build a partial graph:\n  - "
                         + "\n  - ".join(problems))
    return cur


def diff_state(repos, cur, prev, compare_branch=True):
    prev_repos = (prev or {}).get("repos", {})
    changed = [r for r in repos if prev_repos.get(r, {}).get("sha") != cur[r]["sha"]
               or (compare_branch and prev_repos.get(r, {}).get("branch") != cur[r]["branch"])]
    removed = [r for r in prev_repos if r not in repos]
    return changed, removed


def clone_all(assemble, repos, cur, cfg, dest: Path, runner):
    """Shallow-clone every repo at its branch. Big publish-dump repos use sparse checkout."""
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    built = {}
    for repo in repos:
        br = cur[repo]["branch"]
        url = f"https://github.com/{cfg['org']}/{repo}.git"
        target = dest / repo
        only = assemble.REPOS[repo].get("only")
        if only:
            git("clone", "--quiet", "--depth", "1", "--filter=blob:none", "--sparse",
                "--branch", br, url, str(target))
            pats = ["/README.md"] + ["/" + s for s, _ in only]
            git("sparse-checkout", "set", "--no-cone", *pats, cwd=target)
        else:
            git("clone", "--quiet", "--depth", "1", "--branch", br, url, str(target))
        sha, _ = local_head(target)
        built[repo] = {"branch": br, "sha": sha}
        print(f"  [ok] clone {repo}@{br}")
    return built


def parse_extract_log(text: str):
    info = {}
    m = re.search(r"semantic cache: (\d+) hit / (\d+) miss", text)
    if m:
        info["cache_hit"], info["cache_miss"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"tokens: ([\d,]+) in / ([\d,]+) out", text)
    if m:
        info["tokens_in"], info["tokens_out"] = (int(m.group(1).replace(",", "")),
                                                 int(m.group(2).replace(",", "")))
    m = re.search(r"graph\.json: (\d+) nodes, (\d+) edges, (\d+) communities", text)
    if m:
        info["nodes"], info["edges"], info["communities"] = map(int, m.groups())
    return info


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def smoke_test(runner, out: Path, port_env_base):
    port = free_port()
    env = {**os.environ, "GRAPH_PATH": str(out / "graph.json"), "FILE_SIZES": str(out / "file-sizes.json"),
           "CORPUS_MANIFEST": str(out / "manifest.json"), "BUILD_COST_JSON": str(out / "cost.json"),
           "DASHBOARD_HTML": str(HERE / "mcp" / "dashboard.html"), "GRAPH_VIZ_HTML": str(out / "graph.html"),
           "METRICS_DB": str(out.parent / "smoke-metrics.db"), "PORT": str(port)}
    env.pop("GRAPHIFY_API_KEY", None)
    srv = subprocess.Popen([PY, str(HERE / "mcp" / "metrics_server.py")], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        up = False
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as r:
                    if r.status == 200:
                        up = True
                        break
            except Exception:
                time.sleep(2)
        if not up:
            raise StageError("MCP server did not come up for the smoke test")
        runner.run("smoke-test", [PY, HERE / "scripts" / "smoke-test-mcp.py", f"http://127.0.0.1:{port}"])
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=15)
        except Exception:
            srv.kill()


def build(args, assemble, repos, repos_dir: Path, work: Path, runner, prev_out: Path):
    """Assemble + incremental extract + post-processing + verify + smoke. Returns (out_dir, info)."""
    build_root = work / "build-root"
    out = work / "out"
    env = {**os.environ, "REPOS_DIR": str(repos_dir), "BUILD_ROOT": str(build_root)}

    runner.run("assemble-build-root", [PY, HERE / "scripts" / "assemble-build-root.py"], env=env)

    # seed the semantic/AST cache so Graphify only re-extracts what changed
    (build_root / "graphify-out").mkdir(parents=True, exist_ok=True)
    cache_src = prev_out / "cache"
    if cache_src.is_dir():
        shutil.copytree(cache_src, build_root / "graphify-out" / "cache", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(*UNPORTABLE_CACHE))

    gr = [PY, "-m", "graphify"]
    cmd = gr + ["extract", ".", "--backend", args.backend, "--mode", "deep", "--no-gitignore"]
    r = runner.run("graphify-extract", cmd, cwd=build_root, env=os.environ.copy())
    info = parse_extract_log((r.stdout or "") + (r.stderr or ""))
    print("  extract: " + ", ".join(f"{k}={v}" for k, v in info.items()))

    if out.exists():
        shutil.rmtree(out)
    shutil.move(str(build_root / "graphify-out"), str(out))
    for junk in UNPORTABLE_CACHE:                              # never keep/publish the path-bound parts
        target = out / "cache" / junk
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink()
    for f in (".graphify_labels.json", "cost.json"):          # carried over from the published graph
        if (prev_out / f).exists():
            shutil.copyfile(prev_out / f, out / f)

    g = str(out / "graph.json")
    runner.run("extract-db-schema-nodes", [PY, HERE / "scripts" / "extract-db-schema-nodes.py", g, build_root])
    runner.run("extract-prisma-nodes", [PY, HERE / "scripts" / "extract-prisma-nodes.py", g, build_root])
    runner.run("apply-graph-repairs", [PY, HERE / "scripts" / "apply-graph-repairs.py", g])
    runner.run("cluster", gr + ["cluster-only", build_root, "--graph", g, "--no-viz",
                                f"--backend={args.backend}"], env=os.environ.copy())
    runner.run("label", gr + ["label", build_root, "--graph", g, "--missing-only",
                              f"--backend={args.backend}"], env=os.environ.copy())
    for d in out.glob("20*"):                                  # dated backups graphify leaves behind
        shutil.rmtree(d, ignore_errors=True)
    runner.run("export-graph-html", [PY, HERE / "scripts" / "export-graph-html.py", g])
    runner.run("apply-graph-repairs-again", [PY, HERE / "scripts" / "apply-graph-repairs.py", g])
    meta = [PY, HERE / "scripts" / "write-build-metadata.py", build_root, out]
    if "tokens_in" in info:
        meta += ["--input-tokens", info["tokens_in"], "--output-tokens", info.get("tokens_out", 0)]
    runner.run("write-build-metadata", meta)
    runner.run("verify-graph", [PY, HERE / "scripts" / "verify-graph.py", g, build_root])
    smoke_test(runner, out, None)
    return out, info


def publish(out: Path, state: dict, repo_graph_out: Path, state_path: Path):
    repo_graph_out.mkdir(parents=True, exist_ok=True)
    for item in out.iterdir():
        dst = repo_graph_out / item.name
        if item.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(item, dst)
        else:
            shutil.copyfile(item, dst)
    state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")


# ----------------------------------------------------------------------------- commands
def cmd_init_state(args):
    assemble = load_assemble_module()
    repos = list(assemble.REPOS)
    args.source = "local"
    cfg = load_config()
    cur = resolve_current(args, repos, cfg)
    cur = {r: {"branch": branch_for(cfg, r), "sha": cur[r]["sha"]} for r in repos}
    state = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "note": "baseline written by init-state from local clones", "repos": cur}
    Path(args.state).write_text(json.dumps(state, indent=1), encoding="utf-8")
    print(f"wrote {args.state}: {len(cur)} repos")
    return 0


def cmd_run(args):
    t0 = time.time()
    assemble = load_assemble_module()
    cfg = load_config()
    repos = list(assemble.REPOS)
    work = Path(args.work_dir).resolve()
    repo_graph_out = Path(args.graph_out)
    state_path = Path(args.state) if args.state else repo_graph_out / "source-state.json"
    prev = load_state(state_path)

    print(f"Nightly graph update: {len(repos)} repos, source={args.source}, mode={args.mode}")
    cur = resolve_current(args, repos, cfg)
    changed, removed = diff_state(repos, cur, prev, compare_branch=(args.source == "remote"))
    print(f"  repos with new commits: {len(changed)}/{len(repos)}"
          + (f" -> {', '.join(changed)}" if changed else ""))
    if removed:
        print(f"  repos no longer in the graph: {', '.join(removed)}")
    gh_output(changed="true" if (changed or removed or args.force) else "false",
              changed_repos=",".join(changed), changed_count=len(changed))

    if not (changed or removed or args.force):
        print("Nothing changed since the published graph: no clone, no AI, nothing to do.")
        gh_summary("### Nightly graph update\nNo repo changed since the last graph. Nothing was rebuilt.")
        return 0
    if prev is None:
        print("  (no previous state: treating every repo as changed)")

    runner = Runner(work / "logs", args.verbose)
    if args.source == "remote":
        repos_dir = Path(args.clone_dir) if args.clone_dir else work / "repos"
        built = clone_all(assemble, repos, cur, cfg, repos_dir, runner)
    else:
        repos_dir = Path(args.repos_dir)
        # record the configured branch (not whatever is checked out locally) with the commit
        built = {r: {"branch": branch_for(cfg, r), "sha": cur[r]["sha"]} for r in repos}

    if args.mode == "check":
        env = {**os.environ, "REPOS_DIR": str(repos_dir), "BUILD_ROOT": str(work / "build-root")}
        runner.run("assemble-build-root", [PY, HERE / "scripts" / "assemble-build-root.py"], env=env)
        print("Check mode: every repo was readable and the build root assembled. No AI was used.")
        gh_summary(f"### Nightly graph update (check)\n{len(changed)} repo(s) have new commits; "
                   "clone and assemble succeeded. No AI used.")
        return 0

    # Guard against an accidental full rebuild (about 5M AI tokens): if a graph is already
    # published but the cache is gone (evicted/new runner), refuse unless explicitly allowed.
    cache_dir = repo_graph_out / "cache"
    has_cache = cache_dir.is_dir() and any(c.is_dir() and c.name not in UNPORTABLE_CACHE
                                           and any(c.iterdir()) for c in cache_dir.iterdir())
    if prev is not None and not has_cache and not args.allow_full_rebuild:
        raise StageError("the extraction cache is missing, so this would re-send every doc to the AI "
                         "(about 5M tokens). Restore the cache, or re-run with --allow-full-rebuild "
                         "(workflow input allow_full_rebuild) if that cost is intended.")

    out, info = build(args, assemble, repos, repos_dir, work, runner, repo_graph_out)

    state = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "repos": built,
             "last_run": {"changed_repos": changed, **info}}
    if args.publish:
        publish(out, state, repo_graph_out, state_path)
        print(f"Published into {repo_graph_out} and updated {state_path.name}.")
    else:
        (work / "source-state.json").write_text(json.dumps(state, indent=1), encoding="utf-8")
        print(f"Built and verified in {out} (not published: --publish not set).")

    total = info.get("cache_hit", 0) + info.get("cache_miss", 0)
    msg = (f"{len(changed)} repo(s) changed; AI used on {info.get('cache_miss', '?')} of {total or '?'} "
           f"doc/config files (rest from cache); tokens {info.get('tokens_in', 0):,} in / "
           f"{info.get('tokens_out', 0):,} out; graph {info.get('nodes', '?')} nodes.")
    print(msg)
    gh_summary("### Nightly graph update\n" + msg)
    gh_output(tokens_in=info.get("tokens_in", 0), tokens_out=info.get("tokens_out", 0))
    print(f"Done in {time.time() - t0:.0f}s")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="detect changes and update the graph")
    r.add_argument("--source", choices=["remote", "local"], default="remote")
    r.add_argument("--repos-dir", help="local mode: folder holding the clones (read only)")
    r.add_argument("--clone-dir", help="remote mode: where to clone (default <work-dir>/repos)")
    r.add_argument("--work-dir", default=str(HERE / "nightly-work"))
    r.add_argument("--mode", choices=["full", "check"], default="full")
    r.add_argument("--force", action="store_true", help="rebuild even if no repo changed")
    r.add_argument("--backend", default=os.environ.get("GRAPHIFY_BACKEND", "claude"),
                   help="Graphify AI backend: claude (ANTHROPIC_API_KEY) in CI; claude-cli locally")
    r.add_argument("--publish", action="store_true", help="copy the result into graphify-out/")
    r.add_argument("--allow-full-rebuild", action="store_true")
    r.add_argument("--graph-out", default=str(HERE / "graphify-out"),
                   help="the published graphify-out folder (previous graph/cache in, result out)")
    r.add_argument("--state", default=None, help="default: <graph-out>/source-state.json")
    r.add_argument("--verbose", action="store_true")
    r.set_defaults(fn=cmd_run)
    i = sub.add_parser("init-state", help="write source-state.json from local clones")
    i.add_argument("--repos-dir", required=True)
    i.add_argument("--state", default=str(DEFAULT_STATE))
    i.set_defaults(fn=cmd_init_state)
    args = ap.parse_args()
    if args.cmd == "run" and args.source == "local" and not args.repos_dir:
        ap.error("--source local needs --repos-dir")
    try:
        return args.fn(args)
    except StageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        gh_summary(f"### Nightly graph update FAILED\n{e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
