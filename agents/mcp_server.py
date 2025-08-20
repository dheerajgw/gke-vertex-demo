import os
DISABLE_HEAL_PATCH = os.environ.get("DISABLE_HEAL_PATCH","1") == "1"
import os
DISABLE_HEAL_PATCH = os.environ.get("DISABLE_HEAL_PATCH","1") == "1"
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Vertex-powered MCP-style healer for CI failures.

Env required:
  GCP_PROJECT_ID, GCP_LOCATION, VERTEX_MODEL
  (GOOGLE_APPLICATION_CREDENTIALS is set by GitHub action auth step)

Behavior:
  1) Collect junit.xml and any logs in ./ci-logs
  2) Collect important repo files (relative rglob)
  3) Ask Gemini to propose a unified diff patch
  4) Apply patch, run npm ci && npm test
  5) If green, push branch `auto-fix/<sha8>`

Exit codes:
  0 = healed & pushed
  2 = no patch proposed
  3 = patch failed to apply
  4 = tests still failing after patch
  5 = Vertex call failed
  6 = Repo/Env missing critical bits
"""

from __future__ import annotations
import os
import sys
import json
import textwrap
import subprocess
from pathlib import Path
from typing import List, Tuple

# -------- utilities

ROOT = Path.cwd()
APP_DIR = ROOT / "app"
CILOG_DIR = ROOT / "ci-logs"

def echo(msg: str) -> None:
    print(msg, flush=True)

def run(cmd: List[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command, echoing it, returning CompletedProcess. Raises if check and non-zero."""
    echo(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, capture_output=False, check=check)

def run_cap(cmd: List[str], cwd: Path | None = None) -> Tuple[int, str]:
    """Run a command, capturing stdout+stderr."""
    echo(f"$ {' '.join(cmd)}")
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode, p.stdout

def ensure_git_identity() -> None:
    run(["git", "config", "user.name", os.environ.get("GH_BOT_ACTOR", "agentic-bot")], check=True)
    run(["git", "config", "user.email", os.environ.get("GH_BOT_EMAIL", "agentic-bot@example.com")], check=True)

def current_sha_short() -> str:
    code, out = run_cap(["git", "rev-parse", "--short", "HEAD"])
    return out.strip() if code == 0 else "unknown"

# -------- file gathering

def important_files() -> List[Path]:
    """
    Return a list of RELATIVE file paths to include in context.
    Uses rglob with only relative patterns to avoid pathlib NotImplementedError.
    """
    patterns = [
        "package.json", "package-lock.json",
        "app/package.json", "app/package-lock.json",
        "app/**/*.js", "app/**/*.ts", "app/**/*.tsx", "app/**/*.jsx",
        "app/**/*.json", "app/**/*.yaml", "app/**/*.yml", "app/**/*.md",
        "agents/**/*.py",
        ".github/workflows/*.yml", ".github/workflows/*.yaml",
        "k8s/**/*.yaml", "k8s/**/*.yml",
        "Dockerfile", "app/Dockerfile", "README.md"
    ]
    seen = set()
    files: List[Path] = []
    for patt in patterns:
        for p in ROOT.rglob(patt):
            try:
                rel = p.relative_to(ROOT)
            except Exception:
                continue
            if rel.is_dir():
                continue
            if rel in seen:
                continue
            seen.add(rel)
            files.append(rel)
    return files

def read_ci_logs() -> str:
    if not CILOG_DIR.exists():
        return ""
    blobs = []
    # Prefer junit.xml, but include everything small
    for p in sorted(CILOG_DIR.rglob("*")):
        if p.is_dir():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # Bound each file to ~128KB to avoid prompt bloat
        if len(text) > 128_000:
            text = text[:128_000] + "\n[...truncated...]\n"
        blobs.append(f"=== {p.name} ===\n{text}\n")
    return "\n".join(blobs)[:500_000]  # cap total

def read_code_context() -> str:
    chunks = []
    total = 0
    for rel in important_files():
        try:
            text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # keep small files, truncate bigger ones
        if len(text) > 120_000:
            text = text[:120_000] + "\n[...truncated...]\n"
        blob = f"=== {rel.as_posix()} ===\n{text}\n"
        total += len(blob)
        if total > 800_000:  # keep budget
            break
        chunks.append(blob)
    return "\n".join(chunks)

# -------- Vertex AI

def vertex_generate_patch(prompt: str) -> str:
    """Call Vertex AI GenerativeModel to obtain a unified diff patch inside ```diff ...```."""
    try:
        from vertexai import init as vertex_init
        from vertexai.generative_models import GenerativeModel
    except Exception as e:
        echo(f"Vertex SDK import failed: {e}")
        raise

    project = os.environ.get("GCP_PROJECT_ID")
    location = os.environ.get("GCP_LOCATION")
    model_name = os.environ.get("VERTEX_MODEL", "gemini-1.5-pro")

    if not project or not location:
        echo("Missing GCP_PROJECT_ID or GCP_LOCATION")
        raise SystemExit(6)

    vertex_init(project=project, location=location)
    model = GenerativeModel(model_name)

    sys_prompt = textwrap.dedent("""
    You are an expert DevOps+Software agent. You are given CI logs and code snapshots.
    Your task:
      1) Identify the minimal root cause for the failure.
      2) Return ONLY a unified diff patch to fix it. Use paths relative to repo root.
      3) The diff must apply cleanly with `git apply -p0`.
      4) Do not include commentary. Put the diff inside a single fenced block like:

         ```diff
         diff --git a/path/file.js b/path/file.js
         --- a/path/file.js
         +++ b/path/file.js
         @@
         -bad
         +good
         ```

      Rules:
        - Prefer surgical changes.
        - Keep existing formatting and style.
        - If the issue is a missing API route vs SPA catch-all, ensure API routes are defined BEFORE the catch-all.
        - If tests assert JSON keys, ensure keys exist with expected values.
        - Do not add secrets or break other parts.
    """)

    full_prompt = sys_prompt + "\n\n" + prompt
    try:
        resp = model.generate_content(full_prompt)
        text = getattr(resp, "text", None) or (resp.candidates[0].content.parts[0].text if getattr(resp, "candidates", None) else "")
        return text or ""
    except Exception as e:
        echo(f"Vertex call failed: {e}")
        raise SystemExit(5)

def extract_diff_block(s: str) -> str:
    """Extract the first ```diff ...``` fenced block."""
    if not s:
        return ""
    start = s.find("```diff")
    if start == -1:
        return ""
    start = s.find("\n", start)
    if start == -1:
        return ""
    end = s.find("```", start)
    if end == -1:
        return ""
    return s[start+1:end].strip()

# -------- Patch + test

def apply_patch(diff_text: str) -> bool:
    if not diff_text.strip():
    if DISABLE_HEAL_PATCH or not tmp.exists():
        print("Patch mode disabled or missing auto_fix.patch; skipping")
        return False
        return False
    tmp = ROOT / "auto_fix.patch"
    if DISABLE_HEAL_PATCH or not tmp.exists():
        print("Patch mode disabled or missing auto_fix.patch; skipping")
        return False
    tmp.write_text(diff_text, encoding="utf-8")
    # Try apply with -p0; allow whitespace noise
    code, out = run_cap(["git", "apply", "--index", "--whitespace=nowarn", "-p0", str(tmp)])
    echo(out)
    if code != 0:
        # retry without --index, then with 3-way
        code2, out2 = run_cap(["git", "apply", "--whitespace=nowarn", "-p0", str(tmp)])
        echo(out2)
        if code2 != 0:
            code3, out3 = run_cap(["git", "apply", "--3way", "--whitespace=nowarn", "-p0", str(tmp)])
            echo(out3)
            return code3 == 0
        return True
    return True

def run_tests() -> bool:
    # install
    code, out = run_cap(["npm", "ci"], cwd=APP_DIR)
    echo(out)
    if code != 0:
        return False
    # build web (if present)
    pkg = json.loads((APP_DIR / "package.json").read_text(encoding="utf-8"))
    if pkg.get("scripts", {}).get("build:web"):
        code_b, out_b = run_cap(["npm", "run", "build:web"], cwd=APP_DIR)
        echo(out_b)
        if code_b != 0:
            return False
    # test
    code_t, out_t = run_cap(["npx", "jest", "--runInBand"], cwd=APP_DIR)
    echo(out_t)
    return code_t == 0

def push_autofix_branch() -> None:
    ensure_git_identity()
    sha8 = current_sha_short()
    branch = f"auto-fix/{sha8}"
    run(["git", "checkout", "-b", branch], check=True)
    run(["git", "add", "-u"], check=True)
    # create a concise commit message
    run(["git", "commit", "-m", "Agentic fix: CI failure auto-patch"], check=True)
    run(["git", "push", "origin", branch], check=True)
    echo(f"Pushed {branch}")

# -------- fallback heuristics (tiny, safe)

def fallback_heuristics() -> bool:
    """
    Very small guardrails if the model fails: 
    - Fix 'healthzz' → 'healthz'
    - Fix 'messege:' → 'message:'
    - Ensure /api/healthz exists before catch-all in app/api/server.js
    """
    changed = False
    srv = APP_DIR / "api" / "server.js"
    if srv.exists():
        s = srv.read_text(encoding="utf-8", errors="replace")
        t = s.replace("app.get('/api/healthzz'", "app.get('/api/healthz'")
        t = t.replace("messege:", "message:")
        # ensure route exists before catch-all
        if "/api/healthz" not in t:
            # insert a minimal route before the catch-all
            star_idx = t.find("app.get('*'")
            inject = "\napp.get('/api/healthz', (req,res)=>{res.json({ status:'ok', message:'Hello from GKE Vertex PoC!' });});\n"
            if star_idx != -1:
                t = t[:star_idx] + inject + t[star_idx:]
            else:
                t += inject
        if t != s:
            srv.write_text(t, encoding="utf-8")
            changed = True
    if changed:
        # try tests
        if run_tests():
            push_autofix_branch()
            return True
    return False

# -------- main

def build_llm_prompt() -> str:
    logs = read_ci_logs()
    code = read_code_context()
    repo = os.environ.get("GITHUB_REPOSITORY", "unknown/repo")
    sha = current_sha_short()
    return textwrap.dedent(f"""
    REPOSITORY: {repo}
    COMMIT: {sha}

    CI LOGS:
    {logs if logs.strip() else "(no ci-logs found)"}

    CODE SNAPSHOT (paths relative to repo root):
    {code}
    """)

def main() -> None:
    echo("MCP_MANIFEST: mcp-server/vertex-orchestrator@poc (Vertex + fallbacks)")
    # 0) sanity
    for k in ("GCP_PROJECT_ID","GCP_LOCATION","VERTEX_MODEL"):
        if not os.environ.get(k):
            echo(f"Missing env {k}")
            raise SystemExit(6)

    ensure_git_identity()

    # 1) Build prompt from logs + code
    prompt = build_llm_prompt()

    # 2) Ask Vertex for a unified diff
    try:
        llm_raw = vertex_generate_patch(prompt)
        diff = extract_diff_block(llm_raw)
    except SystemExit as e:
        # vertex call failed; try fallback heuristics
        echo("Vertex generation failed; trying fallback heuristics…")
        if fallback_heuristics():
            echo("Fallback heuristics healed and pushed auto-fix branch.")
            return
        raise e

    if not diff:
        echo("No diff block returned by model.")
        # try small heuristics
        if fallback_heuristics():
            echo("Fallback heuristics healed and pushed auto-fix branch.")
            return
        raise SystemExit(2)

    # 3) Apply patch
    if not apply_patch(diff):
        echo("Patch failed to apply.")
        # heuristics?
        if fallback_heuristics():
            echo("Fallback heuristics healed and pushed auto-fix branch.")
            return
        raise SystemExit(3)

    # 4) Run tests
    if not run_tests():
        echo("Tests still failing after patch.")
        raise SystemExit(4)

    # 5) Push auto-fix branch
    push_autofix_branch()
    echo("Healed successfully, auto-fix branch pushed.")

if __name__ == "__main__":
    main()
