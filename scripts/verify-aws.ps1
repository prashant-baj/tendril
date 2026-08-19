# Verify AWS CLI setup for Tendril (TF-02) — Windows / PowerShell.
# Run after `aws sso login --profile tendril-dev`.
$ErrorActionPreference = "Stop"

$AwsProfile = if ($env:AWS_PROFILE) { $env:AWS_PROFILE } else { "tendril-dev" }
$AwsRegion  = if ($env:AWS_REGION)  { $env:AWS_REGION }  else { "ap-south-1" }

Write-Host "Profile: $AwsProfile   Region: $AwsRegion"
Write-Host "--- Identity ---"
aws sts get-caller-identity --profile $AwsProfile

Write-Host "--- Bedrock Claude models ---"
aws bedrock list-foundation-models --profile $AwsProfile --region $AwsRegion `
  --query "modelSummaries[?contains(modelId, 'claude')].modelId" --output table

Write-Host "OK: AWS CLI reachable. Ensure Claude model access is enabled for $AwsRegion."
