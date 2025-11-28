# 🚀 What This Project Is About

This repository implements a **self-healing CI/CD system** powered by **Agentic AI** (Gemini on Vertex AI).  
It automatically:

1. **Builds + tests** your Node.js application (CI).
2. **Deploys** it to GKE using GitHub Actions (CD).
3. When failures occur — **either in CI or during Kubernetes deployment**:
   - An *Agentic AI pipeline* analyzes logs, code, and deployment state  
   - Generates a **fix patch**  
   - Applies it  
   - Runs tests  
   - Pushes an auto-fix branch  
   - Opens a Pull Request  

This makes the pipeline **autonomous, self-diagnosing, and self-healing**.

---

# 🤖 How Agentic AI Is Used

The agentic logic is implemented in `agents/mcp_server.py`.  
It performs **autonomous debugging and code repair** by:

## 1. Collecting Context

- CI failure logs  
- Deployment failures (CrashLoopBackOff, ImagePullBackOff, etc.)  
- Relevant source code files  
- Manifest & configuration files  
- Commit SHA and repository structure  

## 2. Building an LLM Prompt

A rich prompt is constructed containing:

- Logs  
- Code  
- Manifests  
- CI metadata  
- Deployment investigation summary (if CD failure triggered)  

## 3. Calling Gemini (Vertex AI)

Gemini receives this complete context and returns a **unified diff patch**  
to fix the failure (code, tests, manifests, configs).

## 4. Applying the Fix & Verifying

- Patch is applied using `git apply`  
- Tests run (`npm ci`, `npm test`, Jest)  
- If successful → branch is pushed  
- A PR is created automatically  

This functions as an **AI SRE/DevOps Engineer** that fixes the system continuously.

---

# 🔁 High-Level System Flow

## 1. Developer Pushes a Commit → `ci.yml` Runs

- Installs Node.js dependencies  
- Builds the frontend (Vite)  
- Runs Jest tests  
- Produces `junit.xml`  
- Uploads CI logs as artifacts  

## 2A. If CI Passes → `cd.yml` Runs

- Builds Docker image  
- Pushes to Artifact Registry  
- Connects to GKE  
- Applies Kubernetes manifests  
- Waits for rollout  

## 2B. If CI Fails → `agentic-heal.yml` Runs

- Downloads CI logs  
- Authenticates to GCP  
- Runs MCP server (AI Healer)  
- AI generates + applies fix  
- Auto-fix branch created  
- PR opened  

## 3. If Deployment Fails (CD) → Deployment Investigation Runs

- Inspects pods, events, and logs  
- Collects ImagePullBackOff/CrashLoop data  
- Appends investigation summary into LLM prompt  
- AI proposes manifest/code fixes  
- PR is created with corrections  

---

# 📁 Folder Structure Explanation

## `.github/workflows/`
Contains CI, CD, and AI self-healing automation workflows.

---

## `ci.yml`
Implements **Continuous Integration**:

- Installs dependencies  
- Builds frontend  
- Runs Jest tests  
- Generates `junit.xml` test report  
- Uploads logs (for Agentic-Heal)  

💡 Triggers `agentic-heal.yml` when CI fails.

---

## `cd.yml`
Implements **Continuous Deployment**:

- Authenticates to GCP  
- Builds Docker image  
- Pushes to Artifact Registry  
- Applies Kubernetes manifests  
- Checks rollout status  
- If deployment fails → runs MCP script with investigation enabled  

💡 Integrates Agentic AI into deployment debugging.

---

## `agentic-heal.yml`
Implements **Self-Healing** for CI failures:

- Triggered automatically when CI fails  
- Downloads CI logs  
- Executes mcp_server.py  
- AI generates + applies fix  
- Auto-fix branch pushed  
- Pull request created  

💡 Turns your pipeline into a self-healing CI system.

---

# 🧠 `agents/mcp_server.py` (The Agentic AI Brain)

Core autonomous debugging engine:

- Reads CI logs  
- Reads deployment logs (from CD)  
- Reads code + manifests  
- Builds LLM prompt with context  
- Sends to Vertex/Gemini  
- Extracts AI-generated patch  
- Applies patch  
- Runs tests  
- Pushes auto-fix branch  
- Opens PR  

This transforms your pipeline into an **autonomous engineering agent**.

---

# 📦 `agents/app/`

Contains the Node.js backend + frontend:

- API routes  
- Test suites  
- Frontend (`web/` using Vite)  
- Dockerfile  
- Jest config  
- package.json  

This is what CI validates and CD deploys.

---

# 📦 `k8s/`

Kubernetes manifests:

- `deployment.yaml`  
- `service.yaml`  

These are applied by CD and can be auto-fixed by Agentic AI when deployment fails.

---

# 📄 `README.md`
Documentation for the project.

---

# 🎯 In Summary

This repository is a complete **Agentic AI–powered DevOps system**:

- ✔ CI detects failures → **AI heals them**  
- ✔ CD detects rollout issues → **AI diagnoses & fixes them**  
- ✔ AI learns from logs, manifests, and code to generate precise patches  
- ✔ Fully automated end-to-end healing pipeline  

It is a **self-repairing, autonomous CI/CD platform powered by Gemini**.

---

