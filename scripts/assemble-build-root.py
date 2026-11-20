#!/usr/bin/env python3
"""Assemble the Graphify build root from the current kiotel-team repos.

Replaces the old rsync-based loop in build-graph.sh (rsync is not available on Windows and
the old loop hard-coded 14 local snapshot folders under pre-rename names). Cross-platform,
deterministic, stdlib only.

    python scripts/assemble-build-root.py                    # REPOS_DIR defaults to ../kiotel
    REPOS_DIR=/path/to/clones python scripts/assemble-build-root.py
    python scripts/assemble-build-root.py --dry-run          # report only, copy nothing
    python scripts/assemble-build-root.py kiotel_ui_kit      # refresh ONE repo, keep the others

Every repo is copied to build-root/<repo-name>/ under its CURRENT kiotel-team name, so node
IDs and `source_file` paths in the graph match what developers see on GitHub.

With no repo names the whole build-root is wiped and rebuilt. With one or more repo names only
those repos' folders are replaced and every other repo in build-root is left untouched.

Exclusion layers (the graph should hold source + docs, never deps/binaries/secrets):
  1. COMMON_EXCLUDE_DIRS / COMMON_EXCLUDE_GLOBS apply to every repo
  2. MAX_FILE_BYTES drops anything over 1 MB (data dumps, vendored blobs) as a safety net
  3. REPOS[...] per-repo overrides (extra excluded dirs, or whitelisted sub-trees)
  4. .graphifyignore is dropped in the build root as the second line of defence
"""
import fnmatch
import json
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
BUILD_ROOT = HERE / "build-root"
MAX_FILE_BYTES = 1_000_000

COMMON_EXCLUDE_DIRS = {
    ".git", "node_modules", "dist", "build", ".next", "out", "obj", "bin", "__pycache__",
    ".venv", "venv", ".cache", "coverage", ".turbo", ".pytest_cache", ".mypy_cache",
    ".idea", ".vscode", "db-backup",
}

COMMON_EXCLUDE_GLOBS = [
    # lockfiles / minified / maps
    "package-lock.json", "*-lock.json", "yarn.lock", "pnpm-lock.yaml", "uv.lock", "*.lock",
    "*.min.js", "*.map",
    # secrets: keep *.example templates, drop anything that looks like a real credential
    ".env", "*.env", ".env.*", "*.env.*", "*.secret", "*.secrets", "secrets.*",
    "api_key*", "*apikey*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
    "id_rsa*", "credentials*.json", "*postman_environment.json", "*secret*.json", "*.sqlite", "*.sqlite3", "*.db",
    # binaries / models / media / archives
    "*.dll", "*.exe", "*.pdb", "*.so", "*.dylib", "*.onnx", "*.pt", "*.pth", "*.bin",
    "*.safetensors", "*.dat", "*.data", "*.traineddata", "*.deploy", "*.msi", "*.nupkg",
    "*.mp4", "*.mov", "*.wav", "*.mp3", "*.ico", "*.icns", "*.bmp", "*.jpg", "*.jpeg",
    "*.png", "*.PNG", "*.webp", "*.gif", "*.pdf", "*.resx", "*.zip", "*.tar", "*.gz",
    "*.7z", "*.woff", "*.woff2", "*.ttf", "*.otf", "*.eot", "*.pyc", "*.pyo", ".DS_Store",
    ".gitkeep",
]

# Exact path suffixes (matched against the full path, on a "/" boundary) for generated data
# dumps. This must stay path-specific: a bare "data.ts" glob also dropped real source files,
# e.g. kiotel-pms/core/frontend/lib/data.ts. Only the DB backup dump is excluded.
EXCLUDE_PATH_SUFFIXES = (
    "db/backup/data.ts",
)

# Repos in the kiotel-team org: name -> overrides.
#   exclude_dirs : extra directory names to drop anywhere in the repo
#   only         : if set, copy ONLY these (src, dest) sub-trees instead of the whole repo
#   catalog      : special handling (kiotel_dev_docs)
REPOS = {
    # --- kiosk platform ---
    "kiosk_dashboard": {},
    "kiosk_dashboard_windows_client": {
        # The repo is a ClickOnce publish dump (~8k files, 700 MB of vendored DLLs/OCR
        # models). Take only the docs, the helper container, release scripts, and the real
        # app source (kiosk_Source) — same contract as the old kiotel_hardware special case.
        "only": [
            ("docs", "docs"),
            ("multi-L-TTS", "multi-L-TTS"),
            ("release/build_release.ps1", "release/build_release.ps1"),
            ("release/build_release.sh", "release/build_release.sh"),
            ("Application Files/core_kiosk/kiosk_Source", "kiosk_Source"),
        ],
        "exclude_dirs": {"models", "Properties"},
    },
    "kiosk_pms_autofill_extension": {},
    "kiosk_ai_services": {},
    "kiotel_transcription_service": {"exclude_dirs": {"models"}},
    "kiosk_ai_avatar": {"exclude_dirs": {"data", "generated", "results"}},
    # --- identity + workspace ---
    "kiotel_admin_central": {},
    "kiotel-space": {},
    "kiotel_communications_services": {},
    "kiotel_internal_operations_portal": {},
    "kiotel_internal_operations_backend": {},
    "kiotel_ui_kit": {},
    # --- integrations ---
    "kiotel_pms_aggregator_service": {},
    "kiotel_payment_aggregator_service": {},
    "kiotel_channel_manager": {},
    "kiotel-pms": {},
    "kiotel_lox": {},
    "kiotel_chatbot_service": {"exclude_dirs": {"audio"}},
    # --- API collections (3,400+ Bruno YAML files -> one catalog per service) ---
    "kiotel_dev_docs": {"catalog": True},
}


def excluded_file(name: str) -> bool:
    # keep documented templates (.env.example, dev.env.example, *.sample) — never real env files
    if name.endswith((".example", ".sample", ".template")):
        return False
    return any(fnmatch.fnmatch(name, g) for g in COMMON_EXCLUDE_GLOBS)


def excluded_path(fp: Path) -> bool:
    posix = fp.as_posix()
    return any(posix.endswith("/" + s) for s in EXCLUDE_PATH_SUFFIXES)


def copy_tree(src: Path, dest: Path, extra_dirs: set, stats: dict, dry: bool) -> None:
    skip_dirs = COMMON_EXCLUDE_DIRS | extra_dirs
    for dp, dns, fns in os.walk(src):
        dns[:] = [d for d in dns if d not in skip_dirs]
        rel = Path(dp).relative_to(src)
        for fn in fns:
            fp = Path(dp) / fn
            if excluded_file(fn) or excluded_path(fp):
                stats["skipped_pattern"] += 1
                continue
            try:
                size = fp.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                stats["skipped_big"] += 1
                stats["big_examples"].append(str(fp.relative_to(src)))
                continue
            stats["files"] += 1
            stats["bytes"] += size
            if not dry:
                out = dest / rel / fn
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(fp, out)


def copy_one(src: Path, dest: Path, stats: dict, dry: bool) -> None:
    if not src.is_file() or excluded_file(src.name):
        return
    stats["files"] += 1
    stats["bytes"] += src.stat().st_size
    if not dry:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)


def build_dev_docs_catalog(src: Path, dest: Path, stats: dict, dry: bool) -> None:
    """kiotel_dev_docs is Bruno OpenCollection YAML: one file per request, with pre-request
    scripts and bodies that would swamp the graph. Reduce each service collection to a
    single markdown catalog: folder -> METHOD url — request name. That is exactly what you
    want for 'what endpoints does service X expose'."""
    coll = src / "collections"
    if not coll.is_dir():
        return
    out_lines = {}
    for svc in sorted(p for p in coll.iterdir() if p.is_dir()):
        lines = [
            f"# {svc.name} — API catalog (Bruno collection)",
            "",
            f"Source: kiotel_dev_docs/collections/{svc.name}. One line per saved request: "
            "`METHOD url — name`, grouped by folder. Generated by scripts/assemble-build-root.py.",
            "",
        ]
        n = 0
        for dp, dns, fns in os.walk(svc):
            dns.sort()
            rel = Path(dp).relative_to(svc)
            reqs = []
            for fn in sorted(fns):
                if not fn.endswith((".yml", ".yaml")) or fn in ("folder.yml", "opencollection.yml"):
                    continue
                text = (Path(dp) / fn).read_text(encoding="utf-8", errors="replace")
                name = re.search(r"^\s*name:\s*(.+)$", text, re.M)
                meth = re.search(r"^\s*method:\s*(\w+)", text, re.M)
                url = re.search(r"^\s*url:\s*(.+)$", text, re.M)
                if not (meth and url):
                    continue
                reqs.append(
                    f"- `{meth.group(1).upper()} {url.group(1).strip().strip(chr(34))}` — "
                    f"{(name.group(1).strip().strip(chr(34)) if name else fn[:-4])}")
                n += 1
            if reqs:
                lines.append(f"## {'/'.join(rel.parts) or '(root)'}")
                lines += reqs + [""]
        lines.insert(3, f"{n} requests.")
        out_lines[svc.name] = "\n".join(lines) + "\n"
    for name, text in out_lines.items():
        stats["files"] += 1
        stats["bytes"] += len(text)
        if not dry:
            p = dest / f"{name}.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
    ws = src / "workspace.yml"
    copy_one(ws, dest / "workspace.yml", stats, dry)


def main() -> int:
    dry = "--dry-run" in sys.argv
    repos_dir = Path(os.environ.get("REPOS_DIR", HERE.parent / "kiotel")).resolve()
    only_repos = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not repos_dir.is_dir():
        print(f"ERROR: REPOS_DIR not found: {repos_dir}", file=sys.stderr)
        return 1

    # Pre-flight: fail BEFORE touching build-root if anything the build needs is absent. A
    # graph built from a partial set of repos looks fine but silently lacks whole repos.
    unknown = [r for r in only_repos if r not in REPOS]
    if unknown:
        print(f"ERROR: unknown repo name(s): {', '.join(unknown)} "
              f"(known: {', '.join(REPOS)})", file=sys.stderr)
        return 2
    problems = []
    for name, cfg in REPOS.items():
        if only_repos and name not in only_repos:
            continue
        src = repos_dir / name
        if not src.is_dir():
            problems.append(f"{name}: repo folder not found under {repos_dir}")
            continue
        for s, _ in cfg.get("only", ()):
            if not (src / s).exists():
                problems.append(f"{name}: required path not found: {s}")
        if cfg.get("catalog") and not (src / "collections").is_dir():
            problems.append(f"{name}: collections/ folder not found")
    if problems:
        print("ERROR: build aborted, build-root was NOT modified:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2

    if not dry:
        if only_repos:
            # Partial run: replace ONLY the named repos' folders and keep every other repo
            # already in build-root. (Wiping the whole root here would leave a graph input
            # with just the one repo.)
            BUILD_ROOT.mkdir(parents=True, exist_ok=True)
            print(f"› Refreshing {', '.join(only_repos)} in {BUILD_ROOT} (other repos kept)")
            for name in only_repos:
                if (BUILD_ROOT / name).exists():
                    shutil.rmtree(BUILD_ROOT / name)
        else:
            # Full run: start clean so nothing stale (a removed repo, a deleted file) survives.
            print(f"› Resetting build root: {BUILD_ROOT}")
            if BUILD_ROOT.exists():
                shutil.rmtree(BUILD_ROOT)
            BUILD_ROOT.mkdir(parents=True)

    total = {"files": 0, "bytes": 0}
    missing = []
    print(f"{'repo':<40}{'files':>8}{'MB':>9}{'>1MB':>6}{'pattern':>9}")
    for name, cfg in REPOS.items():
        if only_repos and name not in only_repos:
            continue
        src = repos_dir / name
        if not src.is_dir():
            missing.append(name)
            print(f"{name:<40}  MISSING under {repos_dir}")
            continue
        dest = BUILD_ROOT / name
        stats = {"files": 0, "bytes": 0, "skipped_big": 0, "skipped_pattern": 0, "big_examples": []}
        extra = set(cfg.get("exclude_dirs", ()))
        if cfg.get("catalog"):
            build_dev_docs_catalog(src, dest, stats, dry)
        elif cfg.get("only"):
            for s, d in cfg["only"]:
                sp = src / s
                if sp.is_dir():
                    copy_tree(sp, dest / d, extra, stats, dry)
                elif sp.is_file():
                    copy_one(sp, dest / d, stats, dry)
                else:
                    print(f"WARN: {name}: whitelisted path not found: {s}", file=sys.stderr)
            for top in ("README.md",):
                copy_one(src / top, dest / top, stats, dry)
        else:
            copy_tree(src, dest, extra, stats, dry)
        total["files"] += stats["files"]
        total["bytes"] += stats["bytes"]
        print(f"{name:<40}{stats['files']:>8}{stats['bytes']/1e6:>9.1f}"
              f"{stats['skipped_big']:>6}{stats['skipped_pattern']:>9}")
        for b in stats["big_examples"][:3]:
            print(f"      >1MB skipped: {b}")

    if not dry:
        shutil.copyfile(HERE / ".graphifyignore", BUILD_ROOT / ".graphifyignore")
    print(f"\nTOTAL {total['files']} files, {total['bytes']/1e6:.1f} MB"
          + ("  (dry run — nothing copied)" if dry else f"  -> {BUILD_ROOT}"))
    if missing:
        print(f"MISSING repos: {', '.join(missing)}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
