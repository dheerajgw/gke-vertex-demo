import os, subprocess, json, sys, tempfile
def apply_diff(diff_text):
    if not diff_text.strip():
        print(json.dumps({"result":"no_diff"})); sys.exit(1)
    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        tf.write(diff_text)
        tf.flush()
        subprocess.check_call(["git", "apply", tf.name])
def run_tests():
    try:
        subprocess.check_call(["npm","ci"], cwd="app")
        subprocess.check_call(["npm","test"], cwd="app")
        return True
    except subprocess.CalledProcessError:
        return False
def main():
    diff = os.environ.get("PATCH_DIFF","")
    subprocess.check_call(["git","config","user.name",os.environ.get("GH_BOT_ACTOR","agentic-bot")])
    subprocess.check_call(["git","config","user.email",os.environ.get("GH_BOT_EMAIL","agentic-bot@example.com")])
    branch = "auto-fix/" + os.environ.get("GITHUB_RUN_ID","local")
    subprocess.check_call(["git","checkout","-b",branch])
    apply_diff(diff)
    if run_tests():
        subprocess.check_call(["git","add","-A"])
        subprocess.check_call(["git","commit","-m","Agentic fix: CI failure auto-patch"])
        subprocess.check_call(["git","push","origin",branch])
        print(json.dumps({"result":"patch_applied_and_pushed","branch":branch}))
    else:
        print(json.dumps({"result":"patch_failed_tests"}))
        sys.exit(2)
if __name__ == "__main__":
    main()
