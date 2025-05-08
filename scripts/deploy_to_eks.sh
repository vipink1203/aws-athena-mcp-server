#!/bin/bash
#
# Deploy the Athena MCP server to AWS EKS
#
# Usage: ./deploy_to_eks.sh <aws_region> <account_id> <cluster_name> <ecr_repo_name> <athena_db> <athena_workgroup> <s3_output_location>
#
# Example: ./deploy_to_eks.sh us-east-1 123456789012 my-eks-cluster athena-mcp-server my-database primary s3://my-bucket/athena-results/
#

set -e

# Check for required tools
command -v aws >/dev/null 2>&1 || { echo "aws-cli is required but not installed. Aborting."; exit 1; }
command -v kubectl >/dev/null 2>&1 || { echo "kubectl is required but not installed. Aborting."; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "docker is required but not installed. Aborting."; exit 1; }
command -v envsubst >/dev/null 2>&1 || { echo "envsubst is required but not installed. Aborting."; exit 1; }

# Parse arguments
if [ "$#" -lt 7 ]; then
    echo "Usage: $0 <aws_region> <account_id> <cluster_name> <ecr_repo_name> <athena_db> <athena_workgroup> <s3_output_location>"
    exit 1
fi

AWS_REGION=$1
AWS_ACCOUNT_ID=$2
CLUSTER_NAME=$3
ECR_REPO_NAME=$4
ATHENA_DATABASE=$5
ATHENA_WORKGROUP=$6
ATHENA_OUTPUT_LOCATION=$7

echo "Deploying Athena MCP server to EKS..."
echo "AWS Region: $AWS_REGION"
echo "AWS Account ID: $AWS_ACCOUNT_ID"
echo "EKS Cluster: $CLUSTER_NAME"
echo "ECR Repository: $ECR_REPO_NAME"
echo "Athena Database: $ATHENA_DATABASE"
echo "Athena Workgroup: $ATHENA_WORKGROUP"
echo "S3 Output Location: $ATHENA_OUTPUT_LOCATION"

# Extract bucket name from S3 output location
S3_URL_PATTERN="s3://([^/]+)/(.*)"
if [[ $ATHENA_OUTPUT_LOCATION =~ $S3_URL_PATTERN ]]; then
    ATHENA_RESULTS_BUCKET="${BASH_REMATCH[1]}"
    echo "Extracted S3 bucket: $ATHENA_RESULTS_BUCKET"
else
    echo "Invalid S3 output location format. Should be s3://bucket-name/path/"
    exit 1
fi

# Export variables for envsubst
export AWS_REGION
export AWS_ACCOUNT_ID
export ATHENA_DEFAULT_DATABASE=$ATHENA_DATABASE
export ATHENA_WORKGROUP
export ATHENA_OUTPUT_LOCATION
export ATHENA_RESULTS_BUCKET

# Configure kubectl to use the specified cluster
echo "Configuring kubectl to use cluster $CLUSTER_NAME in region $AWS_REGION..."
aws eks update-kubeconfig --name $CLUSTER_NAME --region $AWS_REGION

# Create the ECR repository if it doesn't exist
echo "Ensuring ECR repository exists..."
aws ecr describe-repositories --repository-names $ECR_REPO_NAME --region $AWS_REGION || \
    aws ecr create-repository --repository-name $ECR_REPO_NAME --region $AWS_REGION

# Build and tag the Docker image
echo "Building Docker image..."
docker build -t $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO_NAME:latest .

# Log in to ECR
echo "Logging in to ECR..."
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

# Push the image to ECR
echo "Pushing image to ECR..."
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO_NAME:latest

# Create the AWS policy for Athena access
echo "Creating/updating IAM policy for Athena access..."
POLICY_NAME="AthenaAccessPolicy-$ECR_REPO_NAME"

# Check if the policy exists
aws iam get-policy --policy-arn arn:aws:iam::$AWS_ACCOUNT_ID:policy/$POLICY_NAME 2>/dev/null || POLICY_EXISTS=0

# Create the policy file
mkdir -p ./k8s/generated
envsubst < ./k8s/aws-policy.json > ./k8s/generated/aws-policy.json

if [ -z "$POLICY_EXISTS" ]; then
    # Create new policy
    echo "Creating new IAM policy $POLICY_NAME..."
    POLICY_ARN=$(aws iam create-policy --policy-name $POLICY_NAME --policy-document file://./k8s/generated/aws-policy.json --query 'Policy.Arn' --output text)
else
    # Policy exists, get its ARN
    POLICY_ARN="arn:aws:iam::$AWS_ACCOUNT_ID:policy/$POLICY_NAME"
    
    # Update policy
    echo "Updating existing IAM policy $POLICY_NAME..."
    POLICY_VERSION=$(aws iam list-policy-versions --policy-arn $POLICY_ARN --query 'Versions[?IsDefaultVersion==`true`].VersionId' --output text)
    aws iam create-policy-version --policy-arn $POLICY_ARN --policy-document file://./k8s/generated/aws-policy.json --set-as-default
fi

echo "Policy ARN: $POLICY_ARN"

# Create Kubernetes manifests from templates
echo "Creating Kubernetes manifests..."
mkdir -p ./k8s/generated

# Process each YAML file
for file in ./k8s/*.yaml; do
    filename=$(basename $file)
    echo "Processing $filename..."
    envsubst < $file > ./k8s/generated/$filename
done

# Apply Kubernetes resources
echo "Applying Kubernetes resources..."
kubectl apply -f ./k8s/generated/serviceaccount.yaml
kubectl apply -f ./k8s/generated/deployment.yaml
kubectl apply -f ./k8s/generated/service.yaml

echo "Checking deployment status..."
kubectl rollout status deployment/athena-mcp-server

echo "Deployment completed successfully!"
echo "Athena MCP server should now be accessible at http://athena-mcp-server:8050 within the cluster"

# Get pods and services
echo "Deployed resources:"
kubectl get pods -l app=athena-mcp-server
kubectl get service athena-mcp-server

echo ""
echo "To test the server, run: kubectl port-forward service/athena-mcp-server 8050:8050"
echo "Then access http://localhost:8050/health in your browser"