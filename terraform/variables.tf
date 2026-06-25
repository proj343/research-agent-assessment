variable "project_id" {
  description = "GCP project ID"
  type        = string
  default     = "research-agent-assessment"
}

variable "region" {
  description = "GCP region for Cloud Run and Artifact Registry"
  type        = string
  default     = "us-central1"
}
