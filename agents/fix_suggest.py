import os, json
from google.cloud import aiplatform
def main():
    project = os.environ["GCP_PROJECT"]
    location = os.environ.get("GCP_LOCATION","us-central1")
    model_name = os.environ.get("VERTEX_MODEL","gemini-1.5-pro")
    triage = os.environ.get("TRIAGE_TEXT","")
    snapshot = os.environ.get("REPO_SNAPSHOT","")
    aiplatform.init(project=project, location=location)
    model = aiplatform.GenerativeModel(model_name)
    prompt = f"""Return ONLY a unified diff (git apply compatible) to fix the issue described, updating code and tests as needed.
Triage:
{triage}
Repo:
{snapshot}
"""
    resp = model.generate_content(prompt)
    print(json.dumps({"diff": getattr(resp, "text", str(resp))}, ensure_ascii=False))
if __name__ == "__main__":
    main()

