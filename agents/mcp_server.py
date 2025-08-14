import os, json, subprocess, tempfile, re, sys

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

def file_text(path):
    try:
        return open(path, "r", encoding="utf-8", errors="ignore").read()
    except FileNotFoundError:
        return ""

def write_text(path, txt):
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)

def try_fast_fix() -> bool:
    """
    Deterministic guardrail:
    If tests expect res.body.message but server returns 'messege',
    replace the key and run tests. Return True if tests pass.
    """
    test = file_text("app/__tests__/api.test.js")
    server = file_text("app/api/server.js")
    if not server:
        return False

    expects_message = "res.body.message" in test
    has_typo = re.search(r"\\bmessege\\s*:", server) is not None

    if expects_message and has_typo:
        print("FAST_FIX: Detected 'messege' key; replacing with 'message'…")
        server_fixed = re.sub(r"\\bmessege\\s*:", "message:", server)
        write_text("app/api/server.js", server_fixed)

        ok = True
        try:
            sh(["npm","ci"], cwd="app")
            sh(["npm","test"], cwd="app")
        except subprocess.CalledProcessError:
            ok = False

        if ok:
            # Commit + push + attempt auto-merge to main
            sh(["git","config","user.name", os.environ.get("GH_BOT_ACTOR","agentic-bot")])
            sh(["git","config","user.email", os.environ.get("GH_BOT_EMAIL","agentic-bot@example.com")])
            branch = "auto-fix/" + os.environ.get("GITHUB_RUN_ID","local")
            sh(["git","checkout","-b", branch])
            sh(["git","add","app/api/server.js"])
            # safety: never commit accidental creds
            subprocess.run(["git","restore","--staged","gcp-key.json"], check=False)
            subprocess.run(["git","rm","--cached","gcp-key.json"], check=False)
            sh(["git","commit","-m","Agentic fast-fix: replace 'messege' with 'message'"])
            sh(["git","push","origin", branch])

            # Try to merge to main (fallback to PR if protected)
            try:
                sh(["git","fetch","origin","main"])
                sh(["git","checkout","main"])
                sh(["git","pull","--ff-only","origin","main"])
                sh(["git","merge","--no-ff","-m","Agentic Heal: auto-merge fast-fix", branch])
                sh(["git","push","origin","main"])
                print("MCP_DONE:", json.dumps({"result":"merged_to_main","branch":branch}, ensure_ascii=False))
            except subprocess.CalledProcessError:
                open("branch_name.txt","w").write(branch)
                print("MCP_DONE:", json.dumps({"result":"branch_pushed_need_pr","branch":branch}, ensure_ascii=False))
            return True

    print("FAST_FIX: Not applicable.")
    return False

def main():
    # 1) Fast guardrail fix first
    if try_fast_fix():
        return

    # 2) Otherwise fall back to Vertex triage + diff flow
    logs = file_text("./ci-logs/junit.xml") or file_text("app/junit.xml") or ""
    snapshot = []
    for f in ["app/api/server.js","app/__tests__/api.test.js"]:
        if os.path.exists(f):
            snapshot.append(f"=== {f} ===\\n" + file_text(f)[:6000])
    snapshot = "\\n\\n".join(snapshot)

    print("MCP_MANIFEST:", json.dumps({
        "server":"mcp-server/vertex-orchestrator@poc",
        "tools":[
            {"name":"triage_with_vertex"}, {"name":"propose_fix_with_vertex"},
            {"name":"apply_and_test"}, {"name":"open_pr_if_green"}
        ]}, ensure_ascii=False))

    triage_prompt = f"""You are a CI log triage assistant.
Summarize failure and minimal code fix in one short paragraph.
Logs:
{logs[:120000]}
"""
    triage = vertex_generate(triage_prompt)
    print("MCP_RESULT:", json.dumps({"triage":triage}, ensure_ascii=False))

    diff_prompt = f"""Return ONLY a unified diff (git apply compatible). No prose.
Keep the patch minimal and correct.

Context (triage):
{triage}

Repository snapshot:
{snapshot}
"""
    diff = vertex_generate(diff_prompt)
    if "diff --git" not in diff and ("--- " not in diff or "+++" not in diff):
        print("MCP_RESULT:", json.dumps({"error":"model did not return a diff"}, ensure_ascii=False))
        sys.exit(2)

    sh(["git","config","user.name", os.environ.get("GH_BOT_ACTOR","agentic-bot")])
    sh(["git","config","user.email", os.environ.get("GH_BOT_EMAIL","agentic-bot@example.com")])
    branch = "auto-fix/" + os.environ.get("GITHUB_RUN_ID","local")
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
        sh(["git","add","-A"])
        subprocess.run(["git","restore","--staged","gcp-key.json"], check=False)
        subprocess.run(["git","rm","--cached","gcp-key.json"], check=False)
        sh(["git","commit","-m","Agentic fix: CI failure auto-patch"])
        sh(["git","push","origin", branch])

        try:
            sh(["git","fetch","origin","main"])
            sh(["git","checkout","main"])
            sh(["git","pull","--ff-only","origin","main"])
            sh(["git","merge","--no-ff","-m","Agentic Heal: auto-merge fix", branch])
            sh(["git","push","origin","main"])
            print("MCP_DONE:", json.dumps({"result":"merged_to_main","branch":branch}, ensure_ascii=False))
        except subprocess.CalledProcessError:
            open("branch_name.txt","w").write(branch)
            print("MCP_DONE:", json.dumps({"result":"branch_pushed_need_pr","branch":branch}, ensure_ascii=False))
    else:
        print("MCP_DONE:", json.dumps({"result":"patch_failed_tests"}, ensure_ascii=False))
        sys.exit(3)

if __name__ == "__main__":
    main()
