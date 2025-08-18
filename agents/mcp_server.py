#!/usr/bin/env python3
"""
Agentic Healer (Vertex AI + safe fallbacks)
"""

import os
import re
import sys
import json
import difflib
import tempfile
import subprocess as sp
from pathlib import Path
import xml.etree.ElementTree as ET

# ---- Paths & constants -------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]  # repo root
APP_DIR = ROOT / "app"
SERVER = APP_DIR / "api" / "server.js"
TESTS_DIR = APP_DIR / "__tests__"
JUNIT_CANDIDATES = [ROOT / "ci-logs" / "junit.xml", APP_DIR / "junit.xml"]

MAX_FILE_BYTES = 20000     # limit snippet size per file for prompt
MAX_TOTAL_PROMPT = 800000  # overall safety cap

# ---- Shell helpers -----------------------------------------------------------

def sh(cmd, cwd=None, check=True, capture=False):
    print("$", " ".join(cmd))
    return sp.run(cmd, cwd=cwd, check=check, text=True,
                  stdout=sp.PIPE if capture else None,
                  stderr=sp.STDOUT if capture else None)

def ensure_git_identity():
    actor = os.environ.get("GH_BOT_ACTOR", "agentic-bot")
    email = os.environ.get("GH_BOT_EMAIL", "agentic-bot@example.com")
    sh(["git", "config", "user.name", actor])
    sh(["git", "config", "user.email", email])

def current_sha_short():
    r = sh(["git", "rev-parse", "--short", "HEAD"], capture=True)
    return (r.stdout or "").strip() or "local"

# ---- JUnit parsing / repo context -------------------------------------------

def find_junit():
    for p in JUNIT_CANDIDATES:
        if p.exists():
            return p
    return None

def read_text_limited(p: Path, lim=MAX_FILE_BYTES):
    try:
        b = p.read_bytes()
        if len(b) > lim:
            return b[:lim].decode(errors="replace") + f"\n/* ...truncated {len(b)-lim} bytes ... */\n"
        return b.decode(errors="replace")
    except Exception as e:
        return f"/* error reading {p}: {e} */"

def junit_summary(junit_path: Path) -> str:
    try:
        root = ET.parse(junit_path).getroot()
        fails = root.findall(".//failure")
        out = []
        for f in fails:
            if f.text:
                out.append(f.text.strip())
        return "\n\n---\n\n".join(out) if out else "(no <failure> nodes found)"
    except Exception as e:
        return f"(could not parse junit: {e})"

def repo_tree_under(dirpath: Path) -> str:
    lines = []
    for p in sorted(dirpath.rglob("*")):
        if p.is_file():
            rel = p.relative_to(ROOT)
            lines.append(str(rel))
    return "\n".join(lines)

def important_files() -> list[Path]:
    # High-value targets for a small Node/Vite app
    candidates = [
        SERVER,
        TESTS_DIR / "api.test.js",
        APP_DIR / "package.json",
        APP_DIR / "Dockerfile",
        APP_DIR / "web" / "vite.config.*",
        APP_DIR / "web" / "index.html",
        APP_DIR / "web" / "src" / "main.*",
        APP_DIR / "web" / "package.json",
    ]
    # Expand glob-like ones
    expanded = []
    for c in candidates:
        if "*" in str(c):
            for m in ROOT.glob(str(c)):
                if m.exists():
                    expanded.append(m)
        else:
            if c.exists():
                expanded.append(c)
    # Also include any server-side routers/controllers
    for extra in APP_DIR.rglob("*.js"):
        # Keep small set
        if "node_modules" in str(extra):
            continue
        if extra.name in ("server.js",):
            continue
        # Optionally include a few more files
        if len(expanded) < 20:
            expanded.append(extra)
    # dedupe preserving order
    seen = set()
    ordered = []
    for p in expanded:
        if p not in seen:
            ordered.append(p)
            seen.add(p)
    return ordered[:24]

# ---- Vertex AI (Gemini) ------------------------------------------------------

def vertex_enabled() -> bool:
    return all([
        os.environ.get("GCP_PROJECT_ID"),
        os.environ.get("GCP_LOCATION"),
        os.environ.get("VERTEX_MODEL"),
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
    ])

def vertex_generate_patch(junit_text: str, tree_str: str, file_blobs: dict[str, str]) -> tuple[str|None, str|None]:
    """
    Ask Gemini to output a unified diff patch and a commit message.
    Returns (patch_text, commit_message) or (None, None) if generation failed.
    """
    try:
        # Lazy import to avoid hard dependency during local tests
        from vertexai import init as vertex_init
        from vertexai.generative_models import GenerativeModel

        project = os.environ["GCP_PROJECT_ID"]
        location = os.environ["GCP_LOCATION"]
        model_name = os.environ["VERTEX_MODEL"]

        vertex_init(project=project, location=location)
        model = GenerativeModel(model_name)

        # Build prompt gradually (respect size limits)
        parts = []
        parts.append("You are an expert CI/CD fixer bot. Given junit failure(s) and a repo snapshot, produce a minimal code patch (unified diff format) that makes tests pass without introducing secrets or unrelated changes.")
        parts.append("\n### JUNIT FAILURES\n")
        parts.append(junit_text[:200000])  # cap junit
        parts.append("\n### REPO FILE TREE (subset)\n")
        parts.append(tree_str[:200000])    # cap tree

        parts.append("\n### FILE SNIPPETS (trimmed)\n")
        used = 0
        for rel, content in file_blobs.items():
            snippet = f"\n--- FILE: {rel} ---\n{content}\n"
            if used + len(snippet) > MAX_TOTAL_PROMPT:
                break
            parts.append(snippet)
            used += len(snippet)

        parts.append("""
### OUTPUT FORMAT (IMPORTANT)
Return ONLY this structure, no commentary:
<BEGIN_COMMIT_MESSAGE>
<one-line concise commit title>
<END_COMMIT_MESSAGE>
<BEGIN_PATCH>
*** Unified diff across repo root. Use paths relative to repo root. ***
<END_PATCH>

Rules:
- Keep the diff minimal and focused on the failing tests.
- Do NOT create or modify credential files (no *.json keys, no secrets).
- If a route is shadowed by SPA catch-all, reorder so API routes come before it.
- If a field name is wrong, fix the field name rather than the test unless the test is clearly incorrect.
- Pass `npm ci && npm test` in ./app.
""")

        prompt = "".join(parts)

        resp = model.generate_content(prompt)
        text = resp.text or ""

        # Extract commit message and patch
        cm = None
        patch = None
        m1 = re.search(r"<BEGIN_COMMIT_MESSAGE>\s*(.*?)\s*<END_COMMIT_MESSAGE>", text, re.S|re.I)
        if m1: cm = m1.group(1).strip()
        m2 = re.search(r"<BEGIN_PATCH>\s*(.*?)\s*<END_PATCH>", text, re.S|re.I)
        if m2: patch = m2.group(1).strip()

        return (patch, cm)
    except Exception as e:
        print(f"[vertex] generation failed: {e}")
        return (None, None)

# ---- Safety checks -----------------------------------------------------------

def patch_has_secrets(patch_text: str) -> bool:
    # Block any obvious credential leakage
    forbidden_patterns = [
        r'private_key',
        r'client_email',
        r'-----BEGIN (RSA|EC) PRIVATE KEY-----',
        r'\"type\"\s*:\s*\"service_account\"',
        r'\.json',
    ]
    for pat in forbidden_patterns:
        if re.search(pat, patch_text, re.I):
            return True
    return False

# ---- Heuristics fallback -----------------------------------------------------

def heuristics_try_fix() -> tuple[bool, str]:
    """
    Conservative fallback fixes:
      - Ensure /api/healthz route exists and is placed BEFORE catch-all.
      - Fix 'messege:' -> 'message:' if present and test refers to res.body.message
    """
    changed = False
    reason = []
    if not SERVER.exists():
        return (False, "server.js not found")

    src = SERVER.read_text(encoding="utf-8")

    # Ensure health route exists and before catch-all
    has_healthz = "app.get('/api/healthz'" in src or "app.get(\"/api/healthz\"" in src
    catch_all_idx = src.find("app.get('*'")
    if catch_all_idx == -1:
        catch_all_idx = src.find('app.get("*"')
    health_idx = src.find("app.get('/api/healthz'")
    if health_idx == -1:
        health_idx = src.find('app.get("/api/healthz"')

    if catch_all_idx != -1 and (not has_healthz or (health_idx != -1 and health_idx > catch_all_idx)):
        # Inject or move healthz above catch-all (cheap inject)
        reason.append("ensure /api/healthz before SPA catch-all")
        inject = "\napp.get('/api/healthz', (req, res) => { res.json({ status: 'ok', message: 'Hello from GKE Vertex PoC!' }); });\n"
        if has_healthz:
            # remove the later healthz (simplify: inject fresh one above catch-all)
            # (We keep simple to avoid heavy AST)
            pass
        # inject right before catch-all
        src = src[:catch_all_idx] + inject + src[catch_all_idx:]
        changed = True

    # Misspelled message key
    if re.search(r"\bmessege\s*:", src):
        reason.append("fix misspelled `messege:` -> `message:`")
        src = re.sub(r"\bmessege\s*:", "message:", src)
        changed = True

    if changed:
        SERVER.write_text(src, encoding="utf-8")
        return (True, "; ".join(reason))
    return (False, "no heuristic applied")

# ---- Test runner & patch applier --------------------------------------------

def run_tests() -> bool:
    r = sh(["npm", "ci"], cwd=str(APP_DIR), check=False)
    if r.returncode != 0:
        return False
    r = sh(["npm", "test", "--", "--runInBand"], cwd=str(APP_DIR), check=False)
    return r.returncode == 0

def apply_unified_patch(patch_text: str) -> bool:
    with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".patch") as tf:
        tf.write(patch_text)
        tf.flush()
        patch_path = tf.name
    # Validate then apply
    r = sh(["git", "apply", "--check", patch_path], cwd=str(ROOT), check=False, capture=True)
    if r.returncode != 0:
        print("[patch] --check failed; output:\n", r.stdout or "")
        return False
    r2 = sh(["git", "apply", "--whitespace=fix", patch_path], cwd=str(ROOT), check=False, capture=True)
    if r2.returncode != 0:
        print("[patch] apply failed; output:\n", r2.stdout or "")
        return False
    return True

# ---- Main flow ---------------------------------------------------------------

def main():
    print("MCP_MANIFEST: mcp-server/vertex-orchestrator@poc (Vertex + fallbacks)")
    ensure_git_identity()
    base_branch = None

    # 1) Find junit
    junit = find_junit()
    junit_text = junit_summary(junit) if junit else "(JUnit not found)"

    # 2) Build repo context
    tree_str = repo_tree_under(APP_DIR)
    blobs = {}
    for p in important_files():
        rel = str(p.relative_to(ROOT))
        blobs[rel] = read_text_limited(p)

    # 3) Prepare branch
    sha8 = current_sha_short()
    branch = f"auto-fix/{sha8}"
    sh(["git", "checkout", "-b", branch], cwd=str(ROOT), check=False)

    # 4) Try LLM first (if enabled)
    patch = None
    commit_msg = None
    if vertex_enabled():
        print("[vertex] generating patch with model:", os.environ.get("VERTEX_MODEL"))
        patch, commit_msg = vertex_generate_patch(junit_text, tree_str, blobs)

    # 5) If LLM failed or produced nothing, use heuristics
    used_heuristics = False
    if not patch:
        print("[vertex] no patch returned, using heuristics…")
        ok, why = heuristics_try_fix()
        if not ok:
            print("[result] unable to propose a fix (no LLM / no heuristic).")
            sys.exit(1)
        used_heuristics = True
        commit_msg = f"Agentic fix (heuristic): {why}"

    # 6) Apply patch (if LLM provided one)
    if patch:
        print("=== BEGIN PATCH (from Vertex) ===")
        print(patch)
        print("=== END PATCH ===")
        if patch_has_secrets(patch):
            print("[safety] patch appears to include secrets/credentials; aborting.")
            sys.exit(1)
        ok = apply_unified_patch(patch)
        if not ok:
            print("[patch] could not apply Vertex patch; trying heuristics…")
            ok2, why2 = heuristics_try_fix()
            if not ok2:
                print("[result] Vertex patch failed and heuristics found nothing. Abort.")
                sys.exit(1)
            used_heuristics = True
            commit_msg = f"Agentic fix (heuristic fallback): {why2}"

    # 7) Test
    if not commit_msg:
        commit_msg = "Agentic fix: make tests pass"

    sh(["git", "add", "-A"], cwd=str(ROOT))
    sh(["git", "commit", "-m", commit_msg], cwd=str(ROOT))

    ok_tests = run_tests()
    if not ok_tests:
        print("[result] tests still failing; reverting branch.")
        # soft revert by resetting branch to previous
        sh(["git", "reset", "--hard", "HEAD~1"], cwd=str(ROOT), check=False)
        sys.exit(2)

    # 8) Push branch
    sh(["git", "push", "origin", branch], cwd=str(ROOT), check=False)
    print(f"[result] pushed {branch} with fix ({'heuristics' if used_heuristics else 'vertex'}).")

if __name__ == "__main__":
    main()


