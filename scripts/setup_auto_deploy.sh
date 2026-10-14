#!/usr/bin/env bash
# One-time setup for the CI `deploy` job. See docs/auto-deploy.md for what each step does and why.
#
# Run it where gcloud is signed in as a project owner, e.g. Google Cloud Shell (https://shell.cloud.google.com):
#   VM_USER=<your Linux user on the VM> ./scripts/setup_auto_deploy.sh
# The VM checkout must already contain scripts/authorize_deploy_key.sh and scripts/redeploy.sh.
# Safe to re-run: existing Google Cloud resources are kept, and the deploy key is replaced.
set -euo pipefail

GCP_PROJECT_ID="${GCP_PROJECT_ID:-reddit-autocrosspostbot}"
GCP_ZONE="${GCP_ZONE:-us-central1-a}"
VM_NAME="${VM_NAME:-reddit-crosspost-bot}"
VM_USER="${VM_USER:?Set VM_USER to the Linux account on the VM that owns the checkout and runs docker compose}"
VM_REPO_DIR="${VM_REPO_DIR:-/opt/RedditAutoCrosspostBot}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-Toldry/RedditAutoCrosspostBot}"
SERVICE_ACCOUNT_NAME="${SERVICE_ACCOUNT_NAME:-github-deployer}"
WORKLOAD_IDENTITY_POOL_ID="${WORKLOAD_IDENTITY_POOL_ID:-github}"
WORKLOAD_IDENTITY_PROVIDER_ID="${WORKLOAD_IDENTITY_PROVIDER_ID:-racb-repo}"
FIREWALL_RULE_NAME="${FIREWALL_RULE_NAME:-allow-iap-ssh}"

# Google's fixed source range for IAP TCP forwarding
readonly IAP_SOURCE_RANGE=35.235.240.0/20
readonly GITHUB_OIDC_ISSUER=https://token.actions.githubusercontent.com
readonly DEPLOY_KEY_COMMENT=racb-github-deploy
readonly FALLBACK_KEY_FILE=racb-deploy-key

log() { printf '\n==> %s\n' "$*"; }
die() {
  echo "setup_auto_deploy: $*" >&2
  exit 1
}
gcloud_in_project() { gcloud --project="$GCP_PROJECT_ID" "$@"; }

command -v gcloud >/dev/null || die "gcloud is not installed"
command -v ssh-keygen >/dev/null || die "ssh-keygen is not installed"

log "Enabling the APIs that keyless sign-in and IAP need"
gcloud_in_project services enable iam.googleapis.com iamcredentials.googleapis.com \
  sts.googleapis.com iap.googleapis.com compute.googleapis.com

project_number="$(gcloud projects describe "$GCP_PROJECT_ID" --format='value(projectNumber)')"
service_account_email="$SERVICE_ACCOUNT_NAME@$GCP_PROJECT_ID.iam.gserviceaccount.com"
pool_resource="projects/$project_number/locations/global/workloadIdentityPools/$WORKLOAD_IDENTITY_POOL_ID"
provider_resource="$pool_resource/providers/$WORKLOAD_IDENTITY_PROVIDER_ID"

log "Creating the service account $service_account_email"
if ! gcloud_in_project iam service-accounts describe "$service_account_email" >/dev/null 2>&1; then
  gcloud_in_project iam service-accounts create "$SERVICE_ACCOUNT_NAME" \
    --display-name="GitHub Actions deployer for $GITHUB_REPOSITORY"
fi
# tunnelResourceAccessor opens the IAP tunnel; compute.viewer lets gcloud look the VM up by name
for role in roles/iap.tunnelResourceAccessor roles/compute.viewer; do
  gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" --member="serviceAccount:$service_account_email" \
    --role="$role" --condition=None --quiet >/dev/null
done

log "Creating the workload identity pool and the GitHub provider"
if ! gcloud_in_project iam workload-identity-pools describe "$WORKLOAD_IDENTITY_POOL_ID" \
  --location=global >/dev/null 2>&1; then
  gcloud_in_project iam workload-identity-pools create "$WORKLOAD_IDENTITY_POOL_ID" \
    --location=global --display-name="GitHub Actions"
fi
# Only workflow runs of this repository on master can sign in, so pull requests and forks cannot deploy
attribute_condition="assertion.repository == '$GITHUB_REPOSITORY' && assertion.ref == 'refs/heads/master'"
attribute_mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref"
if gcloud_in_project iam workload-identity-pools providers describe "$WORKLOAD_IDENTITY_PROVIDER_ID" \
  --location=global --workload-identity-pool="$WORKLOAD_IDENTITY_POOL_ID" >/dev/null 2>&1; then
  gcloud_in_project iam workload-identity-pools providers update-oidc "$WORKLOAD_IDENTITY_PROVIDER_ID" \
    --location=global --workload-identity-pool="$WORKLOAD_IDENTITY_POOL_ID" \
    --attribute-mapping="$attribute_mapping" --attribute-condition="$attribute_condition"
else
  gcloud_in_project iam workload-identity-pools providers create-oidc "$WORKLOAD_IDENTITY_PROVIDER_ID" \
    --location=global --workload-identity-pool="$WORKLOAD_IDENTITY_POOL_ID" \
    --display-name="$GITHUB_REPOSITORY" --issuer-uri="$GITHUB_OIDC_ISSUER" \
    --attribute-mapping="$attribute_mapping" --attribute-condition="$attribute_condition"
fi
gcloud_in_project iam service-accounts add-iam-policy-binding "$service_account_email" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/$pool_resource/attribute.repository/$GITHUB_REPOSITORY" \
  >/dev/null

log "Allowing SSH from IAP to the VM's network"
vm_network="$(gcloud_in_project compute instances describe "$VM_NAME" --zone="$GCP_ZONE" \
  --format='value(networkInterfaces[0].network.basename())')"
if ! gcloud_in_project compute firewall-rules describe "$FIREWALL_RULE_NAME" >/dev/null 2>&1; then
  gcloud_in_project compute firewall-rules create "$FIREWALL_RULE_NAME" --network="$vm_network" \
    --direction=INGRESS --action=allow --rules=tcp:22 --source-ranges="$IAP_SOURCE_RANGE"
fi

log "Generating a new deploy key"
key_dir="$(mktemp -d)"
trap 'rm -rf "$key_dir"' EXIT
ssh-keygen -q -t ed25519 -N '' -C "$DEPLOY_KEY_COMMENT" -f "$key_dir/deploy_key"
public_key="$(cat "$key_dir/deploy_key.pub")"

log "Authorizing the deploy key on the VM as $VM_USER (gcloud may ask to create your own SSH key first)"
vm_output="$(gcloud_in_project compute ssh "$VM_USER@$VM_NAME" --zone="$GCP_ZONE" --tunnel-through-iap \
  --command="$VM_REPO_DIR/scripts/authorize_deploy_key.sh '$public_key'")"
echo "$vm_output"
host_key="$(sed -n 's/^HOST_KEY: //p' <<<"$vm_output" | tr -d '\r')"
[[ -n $host_key ]] || die "the VM did not report its SSH host key"

declare -A github_variables=(
  [GCP_PROJECT_ID]="$GCP_PROJECT_ID"
  [GCP_ZONE]="$GCP_ZONE"
  [VM_NAME]="$VM_NAME"
  [VM_USER]="$VM_USER"
  [VM_SSH_HOST_KEY]="$host_key"
  [GCP_WORKLOAD_IDENTITY_PROVIDER]="$provider_resource"
  [GCP_DEPLOY_SERVICE_ACCOUNT]="$service_account_email"
  [AUTO_DEPLOY]=true
)

if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  log "Saving the variables and the deploy key secret in $GITHUB_REPOSITORY"
  gh secret set VM_DEPLOY_SSH_KEY --repo "$GITHUB_REPOSITORY" <"$key_dir/deploy_key"
  for name in "${!github_variables[@]}"; do
    gh variable set "$name" --repo "$GITHUB_REPOSITORY" --body "${github_variables[$name]}"
  done
  log "Done. The next push to master deploys automatically."
else
  (umask 077 && cp "$key_dir/deploy_key" "$FALLBACK_KEY_FILE")
  log "gh is not signed in, so set these by hand in GitHub (docs/auto-deploy.md, step 4)"
  echo "Settings > Secrets and variables > Actions > Variables:"
  for name in "${!github_variables[@]}"; do
    printf '  %-32s %s\n' "$name" "${github_variables[$name]}"
  done
  echo "Settings > Secrets and variables > Actions > Secrets:"
  echo "  VM_DEPLOY_SSH_KEY                the whole content of $PWD/$FALLBACK_KEY_FILE"
  echo "Delete $PWD/$FALLBACK_KEY_FILE once the secret is saved."
fi
