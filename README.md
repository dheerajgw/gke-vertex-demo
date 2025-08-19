# Agentic AI PoC (clean reset)

graph TD
    subgraph Dev
      DevUser[Developer]
      Repo[GitHub Repo\n(main)]
    end

    subgraph CI["Workflow: CI (GitHub Actions)"]
      CI_Build[Build & Unit Tests\n(Jest + JUnit)]
      CI_Artifacts[Upload junit.xml\nas Artifact 'ci-logs']
    end

    subgraph Heal["Workflow: Agentic-Heal (GitHub Actions)"]
      Healer[MCP Healer (Python)\nReason-Act Loop]
      Vertex[Vertex AI\n(Google Cloud)]
      Gemini[Gemini Model\n(Reasoning & Patch Synthesis)]
      Patch[Unified Diff Patch\n(Minimal Fix)]
      Verify[Apply Patch → npm ci / build / test]
      AutoBranch[Push auto-fix/<sha>\n(Guardrails: no merge)]
    end

    subgraph CD["Workflow: CD (GitHub Actions)"]
      BuildImg[Build Container\n(Multi-stage Docker)]
      PushAR[Push to Artifact Registry]
      GKECreds[Get GKE Credentials]
      KApply[kubectl apply\nDeploy to GKE]
    end

    subgraph Runtime["GKE Runtime"]
      Svc[LoadBalancer Service]
      Pod[App Pod\n(Node+Express + SPA)]
    end

    DevUser -->|commit/push| Repo
    Repo -->|triggers| CI_Build
    CI_Build -->|fail? upload junit.xml| CI_Artifacts
    CI_Artifacts -->|workflow_run: completed(failure)| Healer

    Healer -->|download 'ci-logs'| Healer
    Healer --> Vertex
    Vertex --> Gemini
    Gemini -->|root cause + minimal fix| Patch
    Healer -->|apply & verify| Verify
    Verify -->|green| AutoBranch
    AutoBranch --> Repo

    Repo -->|merge PR (human)| CD
    CD --> BuildImg --> PushAR --> GKECreds --> KApply --> Pod
    Pod --> Svc --> DevUser

    %% Security/Secrets notes
    classDef note fill:#f7f7f7,stroke:#bbb,color:#333,font-size:12px;
    Note1[Secrets:\nGCP_SA_KEY, GCP_PROJECT_ID,\nGCP_LOCATION, VERTEX_MODEL,\nGKE_CLUSTER, GKE_REGION/ZONE]:::note
    Heal -. uses .-> Note1
    CD -. uses .-> Note1

