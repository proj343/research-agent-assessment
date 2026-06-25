PROJECT_ID := research-agent-assessment
REGION     := us-central1
REPO       := research-agent
IMAGE      := $(REGION)-docker.pkg.dev/$(PROJECT_ID)/$(REPO)/api

.PHONY: test build push tf-init secrets deploy

test:
	uv run pytest -n auto

build:
	docker build -t $(IMAGE):latest .

push: build
	gcloud auth configure-docker $(REGION)-docker.pkg.dev --quiet
	docker push $(IMAGE):latest

# One-time: initialise Terraform
tf-init:
	cd terraform && terraform init

# One-time: push API keys from .env to GCP Secret Manager
secrets:
	gcloud services enable secretmanager.googleapis.com --project=$(PROJECT_ID)
	@GROQ_KEY=$$(grep ^GROQ_API_KEY .env | cut -d= -f2); \
		gcloud secrets create groq-api-key --project=$(PROJECT_ID) --replication-policy=automatic 2>/dev/null; \
		printf '%s' "$$GROQ_KEY" | gcloud secrets versions add groq-api-key --project=$(PROJECT_ID) --data-file=-
	@FRED_KEY=$$(grep ^FRED_API_KEY .env | cut -d= -f2); \
		gcloud secrets create fred-api-key --project=$(PROJECT_ID) --replication-policy=automatic 2>/dev/null; \
		printf '%s' "$$FRED_KEY" | gcloud secrets versions add fred-api-key --project=$(PROJECT_ID) --data-file=-

deploy: push
	cd terraform && terraform apply -auto-approve
