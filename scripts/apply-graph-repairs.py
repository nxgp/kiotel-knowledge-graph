#!/usr/bin/env python3
"""Apply the curated Kiotel graph repairs to a graphify output.

Usage:
    python scripts/apply-graph-repairs.py graphify-out/graph.json          # post-build graph
    python scripts/apply-graph-repairs.py <extraction.json>                # mid-pipeline extraction

Why this exists: graphify's semantic cache replays the original agent outputs on every
rebuild/--update, which re-introduces the issues fixed by hand in the first build
(agent-guessed node IDs, unresolved internal imports, falsified AMBIGUOUS edges). Run this
after every rebuild, before publishing / repackaging the Docker image.

What it does, in order:
  1. remaps known-bad endpoint IDs (aliases) and adds missing symbol nodes
  2. deletes edges that were verified FALSE against source (see mcp/claude-code-mcp.md)
  3. upgrades edges that were verified TRUE against source to EXTRACTED
  4. injects the curated concept->code bridge edges from scripts/bridges.json
     (these are what join the four repos into one connected component)
  5. patches node metadata errors, drops self-loops and exact duplicate edges

Idempotent: running it twice changes nothing the second time.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGES = json.loads((HERE / "bridges.json").read_text(encoding="utf-8"))["bridges"]
# Cross-repo bridges from the 15-agent deep-read (verified file+line evidence).
# These join the 14 repos into one queryable component. Strip the _-prefixed
# annotation keys before use (they document the evidence, not graph fields).
_XREPO = json.loads((HERE / "cross-repo-bridges.json").read_text(encoding="utf-8"))["bridges"]
CROSS_REPO_BRIDGES = [{k: v for k, v in b.items() if not k.startswith("_")} for b in _XREPO]

ALIASES = {
    "ref_kiotel_shared": "kiotel_web_core_shared_src_index",
    "kiotel_web_core_frontend_public_next": "kiotel_web_core_frontend_public_next_nextjs_logo",
    "kiotel_web_core_frontend_public_vercel_vercel_logo": "kiotel_web_core_frontend_public_vercel",
    "kiotel_web_core_frontend_public_file_file_icon": "kiotel_web_core_frontend_public_file",
    "kiotel_web_core_frontend_public_globe_globe_icon": "kiotel_web_core_frontend_public_globe",
    "kiotel_web_core_frontend_app_page_page": "kiotel_web_core_frontend_src_app_page",
    "kiotel_web_core_frontend_app_page": "kiotel_web_core_frontend_src_app_page",
}

# (source, target) pairs verified FALSE against source code — always delete.
FALSIFIED = {
    ("kiotel_pms_core_frontend_public_icons_icon_512", "kiotel_pms_core_frontend_public_sw"),
    ("kiotel_pms_core_frontend_public_sw", "kiotel_pms_core_frontend_public_icons_icon_192"),
    ("kiotel_web_core_frontend_src_app_page", "kiotel_web_core_frontend_public_file"),
    ("kiotel_web_core_frontend_src_app_page", "kiotel_web_core_frontend_public_next_nextjs_logo"),
    ("kiotel_web_core_frontend_src_app_page", "kiotel_web_core_frontend_public_vercel"),
}

# (source, target) -> (source_file, source_location) verified TRUE — upgrade to EXTRACTED.
VERIFIED = {
    ("audio_services_stt_tts_using_api_only_readme_audio_translate_service",
     "audio_services_k_trans_pipe_main_readme_elevenlabs_scribe_stt"): (None, None),
    ("kiotel_web_core_backend_public_welcomepage_welcome_page",
     "kiotel_hardware_docs_architecture_webview2_thin_shell"):
        ("kiotel_hardware/kiosk_Source/kiosk/frmMainScreen.cs", "L464"),
}

# Nodes graphify cannot extract (symbol-less configs) but that carry real system wiring.
EXTRA_NODES = [
    {"id": "kiotel_web_core_backend_src_config_env_env",
     "label": "env (validated config object)", "file_type": "code",
     "source_file": "kiotel_web/core/backend/src/config/env.ts", "source_location": "L1"},
    # NOTE: id carries a _json suffix — the bare stem collides with AppSettings.cs (the
    # C# class that loads this file).
    {"id": "kiotel_hardware_kiosk_source_kiosk_appsettings_json",
     "label": "Kiosk runtime settings (appsettings.json) — BackendUrl + AudioServiceUrl point at the platform",
     "file_type": "document",
     "source_file": "kiotel_hardware/kiosk_Source/kiosk/appsettings.json", "source_location": "L1"},
    {"id": "kiotel_hardware_kiosk_source_backupoptions",
     "label": "Device provisioning backup (backupoptions.json) — COM port + agent identity snapshot",
     "file_type": "document",
     "source_file": "kiotel_hardware/kiosk_Source/backupoptions.json", "source_location": "L1"},
    {"id": "kiotel_pms_deploy_spaces_kiotel_storage_policy",
     "label": "Spaces bucket policy (kiotel-storage) — public read for property logos/images",
     "file_type": "document",
     "source_file": "kiotel-pms/deploy/spaces/kiotel-storage-policy.json", "source_location": "L1"},
    # wave-2 symbol-less artifacts worth being queryable (JSON configs / trained schemas that
    # AST/semantic passes skip but carry real meaning — esp. the chatbot's NL->SQL schema
    # behind a critical security finding).
    {"id": "chatbot_services_customer_module_schema_schema_context",
     "label": "chatbot NL->SQL trained schema (schema_context.json) — includes sensitive kiosk tables: app_secrets, device_oauth, ext_oauth_codes, external_api_clients",
     "file_type": "document",
     "source_file": "chatbot/services/customer_module/schema/schema_context.json", "source_location": "L1"},
    {"id": "chatbot_services_customer_module_schema_training_examples",
     "label": "chatbot NL->SQL training examples (question->SQL pairs)", "file_type": "document",
     "source_file": "chatbot/services/customer_module/schema/training_examples.json", "source_location": "L1"},
    {"id": "chatbot_services_customer_module_schema_documentation",
     "label": "chatbot NL->SQL schema documentation", "file_type": "document",
     "source_file": "chatbot/services/customer_module/schema/documentation.json", "source_location": "L1"},
    {"id": "kiotel_lox_kiotel_lox_application_appsettings_json",
     "label": "Lox Windows agent settings (appsettings.json) — API base + device config", "file_type": "document",
     "source_file": "kiotel_lox/kiotel_lox_application/appsettings.json", "source_location": "L1"},
    {"id": "kiotel_pms_autofill_managed_schema",
     "label": "Autofill enterprise-policy schema (managed_schema.json) — apiBase/dashboardBase overrides", "file_type": "document",
     "source_file": "kiotel_pms_autofill/managed_schema.json", "source_location": "L1"},
]

EXTRA_EDGES = [
    # appsettings.json literally hardcodes the platform + audio service URLs
    {"source": "kiotel_hardware_kiosk_source_kiosk_appsettings_json",
     "target": "audio_services_docs_system_kiotel_web",
     "relation": "references", "confidence": "EXTRACTED", "confidence_score": 1.0,
     "source_file": "kiotel_hardware/kiosk_Source/kiosk/appsettings.json", "source_location": "L2", "weight": 1.0},
    {"source": "kiotel_hardware_kiosk_source_kiosk_appsettings_json",
     "target": "audio_services_readme_audio_ingest_service",
     "relation": "references", "confidence": "EXTRACTED", "confidence_score": 1.0,
     "source_file": "kiotel_hardware/kiosk_Source/kiosk/appsettings.json", "source_location": "L3", "weight": 1.0},
    # AppSettings.cs is the loader of appsettings.json
    {"source": "kiotel_hardware_kiosk_source_kiosk_appsettings",
     "target": "kiotel_hardware_kiosk_source_kiosk_appsettings_json",
     "relation": "references", "confidence": "EXTRACTED", "confidence_score": 1.0,
     "source_file": "kiotel_hardware/kiosk_Source/kiosk/AppSettings.cs", "source_location": None, "weight": 1.0},
    {"source": "kiotel_hardware_kiosk_source_backupoptions",
     "target": "kiotel_hardware_kiosk_source_kiosk_devicemanager_kiosk_devicemanager",
     "relation": "conceptually_related_to", "confidence": "INFERRED", "confidence_score": 0.75,
     "source_file": "kiotel_hardware/kiosk_Source/backupoptions.json", "source_location": None, "weight": 1.0},
    {"source": "kiotel_pms_deploy_spaces_kiotel_storage_policy",
     "target": "kiotel_pms_deploy_readme_kiotel_storage_spaces",
     "relation": "references", "confidence": "EXTRACTED", "confidence_score": 1.0,
     "source_file": "kiotel-pms/deploy/spaces/kiotel-storage-policy.json", "source_location": None, "weight": 1.0},
    # audio docs' session_ai_audit concept IS the kiotel_web DB table (extract-db-schema-nodes.py)
    {"source": "audio_services_docs_data_model_session_ai_audit",
     "target": "kiotel_web_db_table_session_ai_audit",
     "relation": "semantically_similar_to", "confidence": "INFERRED", "confidence_score": 0.95,
     "source_file": "audio_services/docs/DATA_MODEL.md", "source_location": None, "weight": 1.0},
    # wire the wave-2 artifact nodes so they aren't orphans
    {"source": "chatbot_services_customer_module_src_db", "target": "chatbot_services_customer_module_schema_schema_context",
     "relation": "references", "confidence": "EXTRACTED", "confidence_score": 1.0,
     "source_file": "chatbot/services/customer_module/src/db.py", "source_location": None, "weight": 1.0},
    {"source": "chatbot_services_customer_module_schema_training_examples", "target": "chatbot_services_customer_module_schema_schema_context",
     "relation": "references", "confidence": "EXTRACTED", "confidence_score": 1.0,
     "source_file": "chatbot/services/customer_module/schema/training_examples.json", "source_location": None, "weight": 1.0},
    {"source": "chatbot_services_customer_module_schema_documentation", "target": "chatbot_services_customer_module_schema_schema_context",
     "relation": "references", "confidence": "EXTRACTED", "confidence_score": 1.0,
     "source_file": "chatbot/services/customer_module/schema/documentation.json", "source_location": None, "weight": 1.0},
    # the chatbot's trained schema names the platform's sensitive tables (the security finding, made queryable)
    {"source": "chatbot_services_customer_module_schema_schema_context", "target": "kiotel_web_core_backend_src_db_db_schema_view",
     "relation": "shares_data_with", "confidence": "EXTRACTED", "confidence_score": 1.0,
     "source_file": "chatbot/services/customer_module/schema/schema_context.json", "source_location": None, "weight": 1.0},
    {"source": "kiotel_pms_autofill_managed_schema", "target": "kiotel_pms_autofill_background",
     "relation": "references", "confidence": "EXTRACTED", "confidence_score": 1.0,
     "source_file": "kiotel_pms_autofill/managed_schema.json", "source_location": None, "weight": 1.0},
    {"source": "kiotel_lox_kiotel_lox_application_program", "target": "kiotel_lox_kiotel_lox_application_appsettings_json",
     "relation": "references", "confidence": "EXTRACTED", "confidence_score": 1.0,
     "source_file": "kiotel_lox/kiotel_lox_application/Program.cs", "source_location": None, "weight": 1.0},
]

# node id -> metadata patches (verified against source)
NODE_PATCHES = {
    "audiofetcher": {"source_file": "audio_services/app/audit.py", "source_location": "L18"},
    "kiotel_web_core_frontend_public_globe": {"source_file": "kiotel_web/core/frontend/public/globe.svg"},
}


def main(path: str) -> None:
    p = Path(path)
    g = json.loads(p.read_text(encoding="utf-8"))
    edge_key = "links" if "links" in g else "edges"
    edges, nodes = g[edge_key], g["nodes"]
    ids = {n["id"] for n in nodes}
    stats = {"alias": 0, "fallback": 0, "falsified": 0, "upgraded": 0,
             "bridges": 0, "nodes_added": 0, "patched": 0, "dropped": 0}

    for n in EXTRA_NODES:
        if n["id"] not in ids:
            nodes.append(dict(n)); ids.add(n["id"]); stats["nodes_added"] += 1

    def fallback(mid):
        parts = mid.split("_")
        for cut in range(len(parts) - 1, 3, -1):
            cand = "_".join(parts[:cut])
            if cand in ids:
                return cand
        return None

    internal = ("kiotel_web", "kiotel_pms", "kiotel_hardware", "audio_services")
    for e in edges:
        for side in ("source", "target"):
            v = e[side]
            if v in ids:
                continue
            if v in ALIASES and ALIASES[v] in ids:
                e[side] = ALIASES[v]; stats["alias"] += 1
            elif v.startswith(internal):
                fb = fallback(v)
                if fb:
                    e[side] = fb; stats["fallback"] += 1

    kept = []
    for e in edges:
        st = (e["source"], e["target"])
        if st in FALSIFIED:
            stats["falsified"] += 1; continue
        if st in VERIFIED and e.get("confidence") != "EXTRACTED":
            e["confidence"] = "EXTRACTED"; e["confidence_score"] = 1.0
            sf, sl = VERIFIED[st]
            if sf: e["source_file"], e["source_location"] = sf, sl
            stats["upgraded"] += 1
        kept.append(e)
    edges = kept

    have = {(e["source"], e["target"], e.get("relation")) for e in edges}
    for b in BRIDGES + EXTRA_EDGES:
        k = (b["source"], b["target"], b.get("relation"))
        if k in have or b["source"] not in ids or b["target"] not in ids:
            continue
        edges.append(dict(b)); have.add(k); stats["bridges"] += 1

    # Cross-repo bridges (deep-read verified). Report any whose endpoints are
    # absent so a broken bridge is loud, not silently dropped.
    stats["xrepo_bridges"] = 0
    stats["xrepo_broken"] = 0
    for b in CROSS_REPO_BRIDGES:
        k = (b["source"], b["target"], b.get("relation"))
        if b["source"] not in ids or b["target"] not in ids:
            stats["xrepo_broken"] += 1
            print(f"  [xrepo BROKEN] {b['source']} -> {b['target']} "
                  f"({'src' if b['source'] not in ids else 'tgt'} missing)")
            continue
        if k in have:
            continue
        edges.append(dict(b)); have.add(k); stats["xrepo_bridges"] += 1

    for n in nodes:
        patch = NODE_PATCHES.get(n["id"])
        if patch and any(n.get(k) != v for k, v in patch.items()):
            n.update(patch); stats["patched"] += 1

    seen, uniq = set(), []
    for e in edges:
        if e["source"] == e["target"]:
            stats["dropped"] += 1; continue
        k = (e["source"], e["target"], e.get("relation"), e.get("source_file"), e.get("source_location"))
        if k in seen:
            stats["dropped"] += 1; continue
        seen.add(k); uniq.append(e)

    g[edge_key] = uniq
    p.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
    print(f"{p}: {len(nodes)} nodes, {len(uniq)} edges | " +
          ", ".join(f"{k}={v}" for k, v in stats.items() if v))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "graphify-out/graph.json")
