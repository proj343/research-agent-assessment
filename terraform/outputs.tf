output "service_url" {
  description = "Cloud Run service URL"
  value       = google_cloud_run_v2_service.api.uri
}

output "image_repo" {
  description = "Artifact Registry image path"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/research-agent/api"
}
