import os, json, subprocess, tempfile

def sh(cmd, cwd=None, check=True):
    print("$", " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, check=check, text=True)

def vertex_generate(prompt: str) -> str:
    import vertexai
    from vertexai.generative_models import GenerativeModel
    project = os.environ["GCP_PROJECT"]
    location = os.environ.get("GCP_LOCATION","us-central1")
    model_name = os.environ.get("VERTEX_MODEL","gemini-2.0-flash")
    vertexai.init(project=project, location=location)
    resp = GenerativeModel(model_name).generate_content(prompt)
    return getattr(resp, "text", str(resp))

def read_first(*paths):
    for p in paths:
        if os.path.exists(p):
            try:
                return open(p, "r", encoding="utf-8", errors="ignore").read()
            except: pass
    return ""

def main():
    # 1) Gather context
    logs = read_first("./ci-logs/junit.xml", "app/junit.xml", "junit.xml")[:120000]
    snapshot = []
    for f in ["app/api/server.js", "app/__tests__/api.test.js"]:
        if os.path.exists(f):
            snapshot.append(f"=== {f} ===\n" + open(f, "r", encoding="utf-8", errors="ignore").read()[:6000])
    snapshot = "\n\n".join(snapshot)

    print("MCP_MANIFEST:", json.dumps({
        "server":"mcp-server/vertex-orchestrator@poc",
        "tools":[
            {"name":"triage_with_vertex"}, {"name":"propose_fix_with_vertex"},
            {"name":"apply_and_test"}, {"name":"open_pr_if_green"}
        ]}, ensure_ascii=False))

    # 2) Triage
    triage_prompt = f"""You are a CI log triage assistant.
Summarize the failure, identify the root cause, and describe the minimal fix in one short paragraph.
Logs:
{logs}
"""
    triage = vertex_generate(triage_prompt)
    print("MCP_RESULT:", json.dumps({"triage":triage}, ensure_ascii=False))

    # 3) Ask for a unified diff
    diff_prompt = f"""Return ONLY a unified diff (git apply compatible). Do not include any prose.
If tests also need edits, include them. Keep the patch minimal and correct.

Context (triage):
{triage}

Repository snapshot:
{snapshot}
"""
    diff = vertex_generate(diff_prompt)
    if "diff --git" not in diff and ("--- " not in diff or "+++" not in diff):
        print("MCP_RESULT:", json.dumps({"error":"model did not return a diff"}, ensure_ascii=False))
        raise SystemExit(2)

    # 4) Apply diff on a new branch and run tests
    branch = "auto-fix/" + os.environ.get("GITHUB_RUN_ID","local")
    sh(["git","config","user.name", os.environ.get("GH_BOT_ACTOR","agentic-bot")])
    sh(["git","config","user.email", os.environ.get("GH_BOT_EMAIL","agentic-bot@example.com")])
    sh(["git","checkout","-b", branch])

    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        tf.write(diff)
        tf.flush()
        sh(["git","apply", tf.name])

    ok = True
    try:
        sh(["npm","ci"], cwd="app")
        sh(["npm","test"], cwd="app")
    except subprocess.CalledProcessError:
        ok = False

    if ok:
        #Stage all changes
        sh(["git","add","-A"])
        # Ensure no credentials are staged
        subprocess.run(["git", "restore", "--staged", "gcp-key.json"], check=False)
        subprocess.run(["git", "rm", "--cached", "gcp-key.json"], check=False)
        sh(["git","commit","-m","Agentic fix: CI failure auto-patch"])
        sh(["git","push","origin", branch])
        # Try to auto-merge into main and push
        try:
            sh(["git","fetch","origin","main"])
            sh(["git","checkout","main"])
            sh(["git","pull","--ff-only","origin","main"])
            sh(["git","merge","--no-ff","-m","Agentic Heal: auto-merge fix", branch])
            sh(["git","push","origin","main"])
            print("MCP_DONE:", json.dumps(
                {"result":"merged_to_main","branch":branch}, ensure_ascii=False))
        except subprocess.CalledProcessError:
            # If protected branch prevents direct push, fall back to PR
            open("branch_name.txt","w").write(branch)
            print("MCP_DONE:", json.dumps(
                {"result":"branch_pushed_need_pr","branch":branch}, ensure_ascii=False))
    else:
        print("MCP_DONE:", json.dumps({"result":"patch_failed_tests"}, ensure_ascii=False))
        raise SystemExit(3)

if __name__ == "__main__":
    main()
