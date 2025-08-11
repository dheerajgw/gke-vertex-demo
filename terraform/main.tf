terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "5.35.0"
    }
  }
  required_version = ">= 1.3.0"
}

provider "google" {
  project = "agenticai-demo-vertex" # Replace if different
  region  = "us-central1"
}

resource "google_container_cluster" "autopilot_cluster" {
  name     = "agentic-ai-demo-gke"
  location = "us-central1"
  enable_autopilot = true
}

output "kubeconfig" {
  value = "Run: gcloud container clusters get-credentials agentic-ai-demo-gke --region us-central1"
}
