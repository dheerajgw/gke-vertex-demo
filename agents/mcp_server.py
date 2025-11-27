#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import re
import json
import textwrap
import subprocess
from pathlib import Path
from typing import List, Tuple

# When DISABLE_HEAL_PATCH=1, skip applying LLM patches
DISABLE_HEAL_PATCH = os.environ.get("DISABLE_HEAL_PATCH", "0") == "1"

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

# -------- utilities

ROOT = Path.cwd()
APP_DIR = ROOT / "app"
CILOG_DIR = ROOT / "ci-logs"


def run(cmd: List[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command, echoing it; raise if non-zero and check=True."""
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=False,
        check=check,
    )


def run_cap(cmd: List[str], cwd: Path | None = None, env: dict | None = None) -> Tuple[int, str]:
    """Run a command, capturing stdout+stderr (merged)."""
    print(f"$ {' '.join(cmd)}")
    p = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
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
        "Dockerfile", "app/Dockerfile", "README.md",
    ]
    seen: set[Path] = set()
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
    blobs: List[str] = []
    for p in sorted(CILOG_DIR.rglob("*")):
        if p.is_dir():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if len(text) > 128_000:
            text = text[:128_000] + "\n[...truncated...]\n"
        blobs.append(f"=== {p.name} ===\n{text}\n")
    return "\n".join(blobs)[:500_000]


def read_code_context() -> str:
    chunks: List[str] = []
    total = 0
    for rel in important_files():
        try:
            text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if len(text) > 120_000:
            text = text[:120_000] + "\n[...truncated...]\n"
        blob = f"=== {rel.as_posix()} ===\n{text}\n"
        total += len(blob)
        if total > 800_000:
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
        print(f"Vertex SDK import failed: {e}")
        raise

    project = os.environ.get("GCP_PROJECT_ID")
    location = os.environ.get("GCP_LOCATION")
    model_name = os.environ.get("VERTEX_MODEL", "gemini-1.5-pro")

    if not project or not location:
        print("Missing GCP_PROJECT_ID or GCP_LOCATION")
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
        - If you find junk or invalid tokens (e.g. stray `***)`, broken brackets, or malformed syntax in test files), clean them up so the file is valid and the tests can run.
        - Always ensure resulting code compiles and tests can execute.
    """)

    full_prompt = sys_prompt + "\n\n" + prompt
    try:
        resp = model.generate_content(full_prompt)
        text = getattr(resp, "text", None) or (
            resp.candidates[0].content.parts[0].text
            if getattr(resp, "candidates", None) else ""
        )
        return text or ""
    except Exception as e:
        print(f"Vertex call failed: {e}")
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
    return s[start + 1:end].strip()


# -------- Patch + test

def apply_patch(diff_text: str) -> bool:
    # Return early if the LLM produced an empty/whitespace-only diff
    if not diff_text.strip():
        return False

    # We *create* the patch file; don't check existence before writing
    tmp = ROOT / "auto_fix.patch"
    if DISABLE_HEAL_PATCH:
        print("Patch mode disabled; skipping")
        return False

    tmp.write_text(diff_text, encoding="utf-8")

    # Try several strategies to apply the patch
    code, out = run_cap(["git", "apply", "--index", "--whitespace=nowarn", "-p0", str(tmp)])
    if code != 0:
        code, out = run_cap(["git", "apply", "--whitespace=nowarn", "-p0", str(tmp)])
        if code != 0:
            code, out = run_cap(["git", "apply", "--3way", "--whitespace=nowarn", "-p0", str(tmp)])
            if code != 0:
                print("Patch failed to apply.")
                return False

    # Best effort: remove patch file after successful apply
    try:
        tmp.unlink(missing_ok=True)  # Python 3.8+: missing_ok supported on Path.unlink?
    except TypeError:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
    except Exception:
        pass

    return True


def run_tests() -> bool:
    # 1) install
    code, out = run_cap(["npm", "ci"], cwd=APP_DIR)
    print(out)
    if code != 0:
        return False

    # 2) build web (if present)
    pkg = json.loads((APP_DIR / "package.json").read_text(encoding="utf-8"))
    if pkg.get("scripts", {}).get("build:web"):
        code_b, out_b = run_cap(["npm", "run", "build:web"], cwd=APP_DIR)
        print(out_b)
        if code_b != 0:
            return False

    # 3) run tests (prefer jest+junit; fall back to npm test)
    env = os.environ.copy()
    env["JEST_JUNIT_OUTPUT"] = str(APP_DIR / "junit.xml")
    jest_bin = APP_DIR / "node_modules" / ".bin" / "jest"
    if jest_bin.exists():
        code_t, out_t = run_cap(
            ["npx", "jest", "--runInBand", "--reporters=default", "--reporters=jest-junit"],
            cwd=APP_DIR,
            env=env,
        )
        print(out_t)
        return code_t == 0
    else:
        code_t2, out_t2 = run_cap(["npm", "test"], cwd=APP_DIR, env=env)
        print(out_t2)
        return code_t2 == 0


def push_autofix_branch() -> None:
    ensure_git_identity()
    sha8 = current_sha_short()
    branch = f"auto-fix/{sha8}"
    run(["git", "checkout", "-b", branch], check=True)
    run(["git", "add", "-u"], check=True)
    run(["git", "commit", "-m", "Agentic fix: CI failure auto-patch"], check=True)
    run(["git", "push", "origin", branch], check=True)
    print(f"Pushed {branch}")

# --- Deployment Investigation ---

import shlex
from datetime import datetime

# Environment knobs (fallbacks)
DEPLOY_NAMESPACE = os.environ.get("DEPLOY_NAMESPACE", "default")
# DEPLOY_SELECTOR is a kubectl label selector to identify relevant deployments (e.g. "app=myapp")
DEPLOY_SELECTOR = os.environ.get("DEPLOY_SELECTOR", "")  # optional

def kubectl(cmd: List[str], capture: bool = True) -> Tuple[int, str]:
    base = ["kubectl", "--namespace", DEPLOY_NAMESPACE] + cmd
    if capture:
        return run_cap(base)
    else:
        return (0, "") if run(base, check=True) else (1, "")

def list_failed_deployments(selector: str | None = None) -> List[str]:
    """Return deployment names that are not successfully rolled out."""
    sel_args = ["-l", selector] if selector else []
    code, out = kubectl(["get", "deploy", *sel_args, "-o", "jsonpath={range .items[*]}{.metadata.name}{\"\\n\"}{end}"])
    if code != 0:
        return []
    names = [line.strip() for line in out.splitlines() if line.strip()]
    failed = []
    for n in names:
        c, o = kubectl(["rollout", "status", "deploy/" + n, "--timeout=2s"])
        # rollout status returns non-zero or times out if not ready
        if c != 0:
            failed.append(n)
    return failed

def get_deployment_pods(deploy_name: str) -> List[str]:
    # find pods for a deployment via label selector from the deployment
    code, labels = kubectl(["get", "deploy", deploy_name, "-o", "jsonpath={.spec.selector.matchLabels}"])
    if code != 0 or not labels.strip():
        # fallback: list pods with owner=deploy
        code2, out2 = kubectl(["get", "pods", "-o", "jsonpath={range .items[*]}{.metadata.name}{\"\\t\"}{.metadata.ownerReferences[0].name}{\"\\n\"}{end}"])
        pods = []
        if code2 == 0:
            for line in out2.splitlines():
                try:
                    pod, owner = line.split("\t", 1)
                except Exception:
                    continue
                if owner == deploy_name:
                    pods.append(pod)
            return pods
        return []
    # labels comes like map[name:myapp app:myapp] or json-ish; simplify by asking pods by owner label
    code3, pods_out = kubectl(["get", "pods", "-l", f"app={deploy_name}", "-o", "jsonpath={range .items[*]}{.metadata.name}{\"\\n\"}{end}"])
    if code3 == 0 and pods_out.strip():
        return [p for p in pods_out.splitlines() if p.strip()]
    # fallback: return all pods for the deployment using field-selector ownerReferences
    return []

def describe_pod_and_logs(pod: str, tail_lines: int = 500) -> dict:
    info = {"pod": pod}
    c1, desc = kubectl(["describe", "pod", pod])
    info["describe"] = desc if c1 == 0 else ""
    c2, logs = kubectl(["logs", pod, f"--tail={tail_lines}"])
    if c2 != 0:
        # try previous container or include message
        c3, logs_prev = kubectl(["logs", pod, "--previous", f"--tail={tail_lines}"])
        logs = logs_prev if c3 == 0 else ""
    info["logs"] = logs
    return info

def parse_image_from_deployment(deploy_name: str) -> List[Tuple[str, str]]:
    """Return list of (containerName, image) for a deployment."""
    c, out = kubectl(["get", "deploy", deploy_name, "-o", "jsonpath={range .spec.template.spec.containers[*]}{.name}{\"|\"}{.image}{\"\\n\"}{end}"])
    if c != 0 or not out.strip():
        return []
    imgs = []
    for line in out.splitlines():
        if "|" in line:
            name, img = line.split("|", 1)
            imgs.append((name.strip(), img.strip()))
    return imgs

def git_commit_info(commit: str) -> dict:
    c, out = run_cap(["git", "show", "-s", "--format=%H%n%an%n%ae%n%ad%n%s", commit])
    if c != 0:
        return {}
    lines = out.splitlines()
    return {
        "hash": lines[0] if len(lines) > 0 else commit,
        "author": lines[1] if len(lines) > 1 else "",
        "email": lines[2] if len(lines) > 2 else "",
        "date": lines[3] if len(lines) > 3 else "",
        "subject": lines[4] if len(lines) > 4 else "",
    }

def find_commits_touching_paths(paths: List[str], n: int = 5) -> List[dict]:
    """Return last n commits touching any of the given paths."""
    commits = []
    # use --pretty=format:%H to get hashes
    args = ["log", f"-n{n}", "--pretty=format:%H", "--"] + paths
    c, out = run_cap(["git"] + args)
    if c != 0 or not out.strip():
        return []
    for h in out.splitlines():
        commits.append(git_commit_info(h.strip()))
    return commits

def find_commits_touching_manifests(n: int = 5) -> List[dict]:
    pats = ["k8s", "kubernetes", "deploy", "manifests", ".github/workflows", "Dockerfile", "app/Dockerfile"]
    paths = []
    for p in pats:
        for f in ROOT.rglob(f"{p}/*"):
            try:
                rel = f.relative_to(ROOT)
            except Exception:
                continue
            if rel.is_file():
                paths.append(str(rel))
    if not paths:
        # fallback: look at top-level k8s yaml files
        for f in ROOT.glob("*.yaml"):
            try:
                paths.append(str(f.relative_to(ROOT)))
            except Exception:
                continue
    if not paths:
        return []
    # limit to a handful paths to keep git quick
    return find_commits_touching_paths(paths[:50], n=n)

def identify_offending_commit_from_image(image: str) -> dict | None:
    """
    If image contains a short/long git SHA, try to verify and return commit info.
    Examples:
      my-registry/myrepo/myapp:sha1234abcd
      gcr.io/proj/myapp@sha256:...
    """
    # heuristics: detect hex substrings of length 7-40
    m = re.search(r"(?P<sha>[0-9a-f]{7,40})", image)
    if not m:
        return None
    sha = m.group("sha")
    # try to resolve to full commit
    code, out = run_cap(["git", "rev-parse", "--verify", sha])
    if code != 0:
        # maybe short -> try rev-parse with ^? or assume short ok
        return None
    full = out.strip()
    return git_commit_info(full)

def investigate_deployment_failure(namespace: str | None = None, selector: str | None = None) -> dict:
    """
    High-level investigator. Returns a dictionary with:
    - failed_deployments: [names]
    - per_deployment: { name: { images: [...], pods: [...], pod_info: [...], candidate_commits: [...] } }
    """
    if namespace:
        global DEPLOY_NAMESPACE
        DEPLOY_NAMESPACE = namespace

    sel = selector or (DEPLOY_SELECTOR if DEPLOY_SELECTOR else None)
    result = {"namespace": DEPLOY_NAMESPACE, "selector": sel, "failed_deployments": [], "per_deployment": {}}

    failed = list_failed_deployments(sel)
    result["failed_deployments"] = failed

    for d in failed:
        info = {"images": [], "pods": [], "pod_info": [], "candidate_commits": []}
        imgs = parse_image_from_deployment(d)
        info["images"] = imgs

        pods = get_deployment_pods(d)
        info["pods"] = pods

        for p in pods:
            info["pod_info"].append(describe_pod_and_logs(p))

        # candidate commits: by image -> if image contains sha, add that commit
        candidate_commits = []
        for cname, img in imgs:
            maybe = identify_offending_commit_from_image(img)
            if maybe:
                candidate_commits.append({"reason": "image-sha", "commit": maybe, "image": img})

        # fallback: last commits touching k8s manifests
        manifest_commits = find_commits_touching_manifests(n=5)
        if manifest_commits:
            candidate_commits.append({"reason": "manifest-change", "commits": manifest_commits})

        # fallback: last commits touching app code
        recent_app_commits = []
        c, out = run_cap(["git", "log", "--pretty=format:%H%n%an%n%ad%n%s", "-n", "5"])
        if c == 0 and out.strip():
            lines = out.splitlines()
            # parse groups of 4 lines (hash, author, date, subject) - best-effort
            parsed = []
            i = 0
            while i + 3 < len(lines):
                parsed.append({
                    "hash": lines[i].strip(),
                    "author": lines[i+1].strip(),
                    "date": lines[i+2].strip(),
                    "subject": lines[i+3].strip(),
                })
                i += 4
            if parsed:
                recent_app_commits = parsed
                candidate_commits.append({"reason": "recent-commits", "commits": recent_app_commits})

        info["candidate_commits"] = candidate_commits
        result["per_deployment"][d] = info

    # print a human-friendly summary
    print("\n--- Deployment investigation summary ---")
    if not failed:
        print("No failed deployments found (namespace=%s, selector=%s)" % (DEPLOY_NAMESPACE, sel))
        return result

    for dep, depinfo in result["per_deployment"].items():
        print(f"\nDeployment: {dep}")
        print("Images:")
        for n, img in depinfo["images"]:
            print(f"  - {n}: {img}")
        print("Pods:")
        for p in depinfo["pods"]:
            print(f"  - {p}")
        print("Pod problems (describe/log excerpts):")
        for podi in depinfo["pod_info"]:
            name = podi.get("pod")
            desc = podi.get("describe", "")
            # try extract events/errors lines
            errs = []
            for L in desc.splitlines():
                if any(k in L.lower() for k in ("err", "fail", "backoff", "imagepull", "crashloop", "o k il", "oomkill")):
                    errs.append(L.strip())
            print(f"  {name}: {errs[:5]}")
        print("Candidate commits (by heuristic):")
        for c in depinfo["candidate_commits"]:
            reason = c.get("reason")
            if reason == "image-sha":
                cm = c.get("commit")
                print(f"  - image tag maps to commit {cm.get('hash')} {cm.get('subject')} (author {cm.get('author')})")
            elif reason == "manifest-change":
                for cm in c.get("commits", []):
                    print(f"  - manifest change {cm.get('hash')} {cm.get('subject')} ({cm.get('date')})")
            elif reason == "recent-commits":
                for cm in c.get("commits", []):
                    print(f"  - recent {cm.get('hash')} {cm.get('subject')}")
            else:
                print(f"  - {c}")
    print("--- end summary ---\n")
    return result

# -------- fallback heuristics (tiny, safe)

def fallback_heuristics() -> bool:
    """
    Very small guardrails if the model fails:
    - Fix 'healthzz' → 'healthz'
    - Fix 'messege:' → 'message:'
    - Fix accidental missing '=' in arrow functions: ') > {' → ') => {'
    - Ensure /api/healthz exists before catch-all in app/api/server.js
    """
    changed = False
    srv = APP_DIR / "api" / "server.js"
    if srv.exists():
        s = srv.read_text(encoding="utf-8", errors="replace")
        t = s.replace("app.get('/api/healthzz'", "app.get('/api/healthz'")
        t = t.replace("messege:", "message:")
        # fix missing '=' in arrow functions
        t = re.sub(r"\)\s*>\s*\{", ") => {", t)

        # ensure route exists before catch-all
        if "/api/healthz" not in t:
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


def vertex_try(prompt: str) -> str:
    """Isolate Vertex call so we can clearly fall back."""
    from vertexai import init as vertex_init
    from vertexai.generative_models import GenerativeModel

    project = os.environ.get("GCP_PROJECT_ID")
    location = os.environ.get("GCP_LOCATION")
    model_name = os.environ.get("VERTEX_MODEL", "gemini-1.5-pro")

    if not project or not location:
        print("Missing GCP_PROJECT_ID or GCP_LOCATION")
        raise SystemExit(6)

    vertex_init(project=project, location=location)
    model = GenerativeModel(model_name)
    resp = model.generate_content(prompt)
    text = getattr(resp, "text", None) or (
        resp.candidates[0].content.parts[0].text
        if getattr(resp, "candidates", None) else ""
    )
    return text or ""


def main() -> None:
    print("MCP_MANIFEST: mcp-server/vertex-orchestrator@poc (Vertex + fallbacks)")
    for k in ("GCP_PROJECT_ID", "GCP_LOCATION", "VERTEX_MODEL"):
        if not os.environ.get(k):
            print(f"Missing env {k}")
            raise SystemExit(6)

    ensure_git_identity()

    # 1) Build prompt from logs + code
    prompt = build_llm_prompt()

    # 2) Ask Vertex for a unified diff
    try:
        llm_raw = vertex_try(
            textwrap.dedent("""
            You are an expert DevOps+Software agent. You are given CI logs and code snapshots.
            Return ONLY a unified diff patch inside a single ```diff fenced block``` that fixes the CI failure.
            """) + "\n\n" + prompt
        )
        diff = extract_diff_block(llm_raw)
    except SystemExit as e:
        print("Vertex generation failed; trying fallback heuristics…")
        if fallback_heuristics():
            print("Fallback heuristics healed and pushed auto-fix branch.")
            return
        raise e
    except Exception as e:
        print(f"Vertex call failed unexpectedly: {e}")
        print("Trying fallback heuristics…")
        if fallback_heuristics():
            print("Fallback heuristics healed and pushed auto-fix branch.")
            return
        raise SystemExit(5)

    if not diff:
        print("No diff block returned by model.")
        if fallback_heuristics():
            print("Fallback heuristics healed and pushed auto-fix branch.")
            return
        raise SystemExit(2)

    # 3) Apply patch
    if not apply_patch(diff):
        print("Patch failed to apply.")
        if fallback_heuristics():
            print("Fallback heuristics healed and pushed auto-fix branch.")
            return
        raise SystemExit(3)

    # 4) Run tests
    if not run_tests():
        print("Tests still failing after patch.")
        raise SystemExit(4)

    # 5) Push auto-fix branch
    push_autofix_branch()
    print("Healed successfully, auto-fix branch pushed.")


if __name__ == "__main__":
    main()
