terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ── APIs ──────────────────────────────────────────────────────────────────────

resource "google_project_service" "run" {
  service            = "run.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "artifactregistry" {
  service            = "artifactregistry.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "secretmanager" {
  service            = "secretmanager.googleapis.com"
  disable_on_destroy = false
}

# ── Artifact Registry ─────────────────────────────────────────────────────────

resource "google_artifact_registry_repository" "repo" {
  location      = var.region
  repository_id = "research-agent"
  format        = "DOCKER"
  depends_on    = [google_project_service.artifactregistry]
}

# ── Secrets (created via `make secrets`, referenced here) ─────────────────────

data "google_secret_manager_secret" "groq_api_key" {
  secret_id  = "groq-api-key"
  depends_on = [google_project_service.secretmanager]
}

data "google_secret_manager_secret" "fred_api_key" {
  secret_id  = "fred-api-key"
  depends_on = [google_project_service.secretmanager]
}

# ── Service Account ───────────────────────────────────────────────────────────

resource "google_service_account" "run_sa" {
  account_id   = "research-agent-run"
  display_name = "Research Agent Cloud Run"
}

resource "google_secret_manager_secret_iam_member" "groq_access" {
  secret_id = data.google_secret_manager_secret.groq_api_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.run_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "fred_access" {
  secret_id = data.google_secret_manager_secret.fred_api_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.run_sa.email}"
}

# ── Cloud Run ─────────────────────────────────────────────────────────────────

locals {
  image = "${var.region}-docker.pkg.dev/${var.project_id}/research-agent/api:latest"
}

resource "google_cloud_run_v2_service" "api" {
  name     = "research-agent"
  location = var.region

  template {
    service_account = google_service_account.run_sa.email

    containers {
      image = local.image

      ports {
        container_port = 8080
      }

      env {
        name  = "LLM_PROVIDER"
        value = "groq"
      }

      env {
        name  = "LLM_MODEL"
        value = "llama-3.3-70b-versatile"
      }

      env {
        name = "GROQ_API_KEY"
        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.groq_api_key.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "FRED_API_KEY"
        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.fred_api_key.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.run,
    google_artifact_registry_repository.repo,
    google_secret_manager_secret_iam_member.groq_access,
    google_secret_manager_secret_iam_member.fred_access,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
