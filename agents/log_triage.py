import os, json, sys
from google.cloud import aiplatform
def main():
    project = os.environ["GCP_PROJECT"]
    location = os.environ.get("GCP_LOCATION","us-central1")
    model_name = os.environ.get("VERTEX_MODEL","gemini-1.5-pro")
    # For simplicity, just read junit.xml if present
    log_text = ""
    for p in ["./ci-logs/junit.xml", "app/junit.xml"]:
        if os.path.exists(p):
            log_text = open(p, "r", encoding="utf-8", errors="ignore").read()
            break
    aiplatform.init(project=project, location=location)
    model = aiplatform.GenerativeModel(model_name)
    prompt = f"""You are a CI log triage assistant. Summarize the failure, identify the root cause lines, and propose a minimal fix.
Logs:
{log_text[:120000]}"""
    resp = model.generate_content(prompt)
    print(json.dumps({"triage": getattr(resp, "text", str(resp))}, ensure_ascii=False))
if __name__ == "__main__":
    main()
