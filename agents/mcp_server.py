import os, json, subprocess, tempfile, textwrap
from typing import Dict, Any, Callable

# --- "MCP server" registry ----------------------------------------------------
class ToolError(Exception): pass

TOOLS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}

def tool(name):
    def wrap(fn):
        TOOLS[name] = fn
        return fn
    return wrap

def run(cmd, cwd=None, check=True):
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=cwd, check=check, text=True, capture_output=False)

# --- Vertex client (lazy import) ----------------------------------------------
def vertex_generate(prompt: str) -> str:
    import vertexai
    from vertexai.generative_models import GenerativeModel

    project = os.environ["GCP_PROJECT"]
    location = os.environ.get("GCP_LOCATION", "us-central1")
    model_name = os.environ.get("VERTEX_MODEL", "gemini-1.5-pro")

    vertexai.init(project=project, location=location)
    model = GenerativeModel(model_name)

    resp = model.generate_content(prompt)
    return getattr(resp, "text", str(resp))
# --- Tools --------------------------------------------------------------------
@tool("gather_context")
def gather_context(params):
    # read junit.xml if present
    log_text = ""
    for p in ["./ci-logs/junit.xml", "app/junit.xml", "junit.xml"]:
        if os.path.exists(p):
            log_text = open(p, "r", encoding="utf-8", errors="ignore").read()
            break
    # capture small code snapshot
    def cat(path, n=250):
        if os.path.exists(path):
            return open(path, "r", encoding="utf-8", errors="ignore").read().splitlines()[:n]
        return []
    snap = []
    for f in ["app/src/server.js","app/__tests__/health.test.js","app/tests/health.test.js"]:
        lines = cat(f)
        if lines:
            snap.append(f"=== {f} ===\n" + "\n".join(lines))
    return {"logs": log_text, "snapshot": "\n\n".join(snap)}

@tool("triage_with_vertex")
def triage_with_vertex(params):
    logs = params.get("logs","")[:120000]
    prompt = f"""You are a CI log triage assistant.
Summarize the failure, identify exact root-cause lines, and propose a minimal fix.
Focus on JavaScript/Jest output if present.
Logs:
{logs}
"""
    triage = vertex_generate(prompt)
    return {"triage": triage}

@tool("propose_fix_with_vertex")
def propose_fix_with_vertex(params):
    triage = params.get("triage","")
    snapshot = params.get("snapshot","")[:120000]
    prompt = f"""Return ONLY a unified diff (git apply compatible). Do not include prose.
If tests require updates, include them. Keep patch minimal and correct.

Context (triage):
{triage}

Repository snapshot:
{snapshot}
"""
    diff = vertex_generate(prompt)
    # basic guard: ensure it looks like a diff
    if "diff --git" not in diff and "@@ " not in diff and "--- " not in diff:
        diff = ""
    return {"diff": diff}

@tool("apply_and_test")
def apply_and_test(params):
    diff = params.get("diff","")
    if not diff.strip():
        raise ToolError("No diff provided by LLM.")
    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        tf.write(diff)
        tf.flush()
        run(["git","config","user.name", os.environ.get("GH_BOT_ACTOR","agentic-bot")])
        run(["git","config","user.email", os.environ.get("GH_BOT_EMAIL","agentic-bot@example.com")])
        branch = "auto-fix/" + os.environ.get("GITHUB_RUN_ID","local")
        run(["git","checkout","-b", branch])
        run(["git","apply", tf.name])
    # test
    ok = True
    try:
        run(["npm","ci"], cwd="app")
        run(["npm","test"], cwd="app")
    except subprocess.CalledProcessError:
        ok = False
    return {"tests_green": ok, "branch": branch}

@tool("open_pr_if_green")
def open_pr_if_green(params):
    if not params.get("tests_green"):
        return {"opened": False, "reason": "tests not green"}
    branch = params.get("branch","auto-fix/local")
    # Let GitHub create PR UI if needed; push branch
    run(["git","add","-A"])
    run(["git","commit","-m","Agentic fix: CI failure auto-patch"])
    run(["git","push","origin", branch])
    return {"opened": True, "branch": branch}

# --- Simple plan executor -----------------------------------------------------
PLAN = [
    {"call":"gather_context"},
    {"call":"triage_with_vertex", "use":["logs"]},
    {"call":"propose_fix_with_vertex", "use":["triage","snapshot"]},
    {"call":"apply_and_test", "use":["diff"]},
    {"call":"open_pr_if_green", "use":["tests_green","branch"]},
]

MANIFEST = {
  "server": "mcp-server/vertex-orchestrator@poc",
  "tools": [
    {"name":"gather_context","desc":"Collect CI logs and code snapshot"},
    {"name":"triage_with_vertex","desc":"Summarize failure & root cause via Vertex"},
    {"name":"propose_fix_with_vertex","desc":"Return unified diff patch via Vertex"},
    {"name":"apply_and_test","desc":"Apply diff, run npm ci/test"},
    {"name":"open_pr_if_green","desc":"Push branch and open PR if tests pass"}
  ]
}

def main():
    print("MCP_MANIFEST:", json.dumps(MANIFEST))
    state: Dict[str, Any] = {}
    for step in PLAN:
        name = step["call"]
        uses = step.get("use", [])
        args = {k: state.get(k) for k in uses}
        print(f"MCP_CALL: {name} ARGS: {list(args.keys())}")
        try:
            out = TOOLS[name](args)
        except ToolError as e:
            print(json.dumps({"tool":name,"error":str(e)}))
            break
        state.update(out or {})
        print("MCP_RESULT:", json.dumps({name: out}, ensure_ascii=False))
    # Final state for logs
    print("MCP_DONE:", json.dumps(state, ensure_ascii=False))

if __name__ == "__main__":
    main()
