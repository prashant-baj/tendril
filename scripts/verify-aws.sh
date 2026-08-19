#!/usr/bin/env bash
# Verify AWS CLI setup for Tendril (TF-02). Run on your machine after `aws sso login`.
set -euo pipefail

PROFILE="${AWS_PROFILE:-tendril-dev}"
REGION="${AWS_REGION:-ap-south-1}"

echo "Profile: $PROFILE   Region: $REGION"
echo "--- Identity ---"
aws sts get-caller-identity --profile "$PROFILE"

echo "--- Bedrock model access (first few) ---"
aws bedrock list-foundation-models --profile "$PROFILE" --region "$REGION" \
  --query 'modelSummaries[?contains(modelId, `claude`)].modelId' --output table || \
  echo "!! Could not list Bedrock models — enable model access in the console."

echo "OK: AWS CLI reachable. Ensure Claude model access is enabled for $REGION."
