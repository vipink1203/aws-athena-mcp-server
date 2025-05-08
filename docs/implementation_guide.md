# Athena MCP Server Implementation Guide

This guide covers how to set up the Athena MCP server, deploy it to AWS EKS, and integrate it with n8n.

## Prerequisites

Before you begin, ensure you have:

- An AWS account with appropriate permissions
- AWS CLI installed and configured
- Docker installed for building container images
- `kubectl` installed and configured for your EKS cluster
- Basic knowledge of AWS services (Athena, ECR, EKS)

## Local Development and Testing

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/aws-athena-mcp-server.git
cd aws-athena-mcp-server
```

### 2. Set Up Local Environment

```bash
# Create a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure AWS credentials
export AWS_ACCESS_KEY_ID=your_access_key
export AWS_SECRET_ACCESS_KEY=your_secret_key
export AWS_REGION=us-east-1

# Set Athena-specific environment variables
export ATHENA_DATABASE=your_database
export ATHENA_OUTPUT_LOCATION=s3://your-bucket/athena-results/
```

### 3. Run the MCP Server Locally

```bash
python main.py
```

Access the server at http://localhost:8050 and check the health endpoint at http://localhost:8050/health.

### 4. Test with the MCP Inspector

The MCP server includes a built-in inspector for testing. Access it by running:

```bash
# Install MCP CLI if not already installed
pip install "mcp[cli]"

# Start the inspector (from another terminal)
mcp inspect http://localhost:8050/sse
```

## Building and Pushing Docker Image

### 1. Build the Docker Image

```bash
docker build -t athena-mcp-server:latest .
```

### 2. Test the Docker Image Locally

```bash
docker run -p 8050:8050 \
  -e AWS_ACCESS_KEY_ID=your_access_key \
  -e AWS_SECRET_ACCESS_KEY=your_secret_key \
  -e AWS_REGION=us-east-1 \
  -e ATHENA_OUTPUT_LOCATION=s3://your-bucket/athena-results/ \
  athena-mcp-server:latest
```

### 3. Push to Amazon ECR

```bash
# Set environment variables
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export AWS_REGION=us-east-1

# Create ECR repository (if it doesn't exist)
aws ecr create-repository --repository-name athena-mcp-server

# Login to ECR
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

# Tag the image
docker tag athena-mcp-server:latest $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/athena-mcp-server:latest

# Push the image
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/athena-mcp-server:latest
```

## AWS EKS Deployment

### 1. Create IAM Policy and Role

Create an IAM policy for Athena access:

```bash
# Export S3 bucket variable
export ATHENA_RESULTS_BUCKET=your-athena-results-bucket

# Create policy from JSON file (substitute variables first)
envsubst < k8s/aws-policy.json > /tmp/aws-policy.json
aws iam create-policy \
  --policy-name AthenaQueryPolicy \
  --policy-document file:///tmp/aws-policy.json
```

### 2. Set Up IAM Role for Service Account (IRSA)

```bash
# If using eksctl
eksctl create iamserviceaccount \
  --name athena-mcp-server \
  --namespace default \
  --cluster your-eks-cluster \
  --attach-policy-arn arn:aws:iam::$AWS_ACCOUNT_ID:policy/AthenaQueryPolicy \
  --approve \
  --override-existing-serviceaccounts

# Alternative: Manual setup with IAM OIDC provider
# See AWS documentation for manual IRSA setup if not using eksctl
```

### 3. Prepare Kubernetes Manifests

```bash
# Export necessary variables
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export AWS_REGION=us-east-1
export ATHENA_DEFAULT_DATABASE=your_database
export ATHENA_WORKGROUP=primary
export ATHENA_OUTPUT_LOCATION=s3://your-bucket/athena-results/
export ATHENA_RESULTS_BUCKET=your-bucket

# Replace variables in YAML files
mkdir -p k8s/generated
envsubst < k8s/deployment.yaml > k8s/generated/deployment.yaml
envsubst < k8s/serviceaccount.yaml > k8s/generated/serviceaccount.yaml
envsubst < k8s/service.yaml > k8s/generated/service.yaml
```

### 4. Deploy to EKS

```bash
kubectl apply -f k8s/generated/serviceaccount.yaml
kubectl apply -f k8s/generated/deployment.yaml
kubectl apply -f k8s/generated/service.yaml

# Verify deployment
kubectl get pods -l app=athena-mcp-server
kubectl get service athena-mcp-server
```

### 5. (Optional) Set Up Ingress

If you need to expose the MCP server outside the Kubernetes cluster:

```bash
# Create ingress manifest (example for AWS ALB)
cat > k8s/generated/ingress.yaml << EOF
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: athena-mcp-server
  annotations:
    kubernetes.io/ingress.class: alb
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip
spec:
  rules:
  - http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: athena-mcp-server
            port:
              number: 8050
EOF

# Apply ingress
kubectl apply -f k8s/generated/ingress.yaml

# Get the external address
kubectl get ingress athena-mcp-server
```

### 6. Automate Deployment (Optional)

For easier deployment, you can use the included script:

```bash
chmod +x scripts/deploy_to_eks.sh
./scripts/deploy_to_eks.sh us-east-1 $AWS_ACCOUNT_ID my-eks-cluster athena-mcp-server my_database primary s3://my-bucket/athena-results/
```

## Integrating with n8n

### 1. Configure n8n Environment

Ensure n8n can communicate with your MCP server. If your MCP server is exposed via an ingress, use that URL. Otherwise, if running n8n in the same Kubernetes cluster, you can use the internal service DNS.

### 2. Create an n8n Workflow

1. Add an "AI Agent" node in your n8n workflow
2. Configure it with:
   - Model: Claude 3 (or your preferred LLM)
   - System Prompt: Use the template from `docs/n8n_system_prompt_template.md` and customize it for your database
   - Tools Configuration: Add the MCP server URL in the appropriate field

### 3. Customize the System Prompt

Update the system prompt with your specific database schema:

1. Get your table schemas:
   ```sql
   -- Run this in Athena or use the MCP server
   SHOW TABLES IN your_database;
   DESCRIBE your_table;
   ```

2. Fill in the placeholders in the system prompt:
   - `{{ATHENA_DATABASE}}`: Your Athena database name
   - `{{TABLE_SCHEMAS}}`: The schema details of your tables
   - `{{EXAMPLE_SCHEMA}}`: A simplified example of your schema
   - Add example queries specific to your data model

### 4. Test the Integration

1. Deploy your n8n workflow
2. Test with sample questions to ensure the AI agent can:
   - List available databases and tables
   - Get table metadata
   - Execute SQL queries
   - Present results in a user-friendly format

## Sample Use Cases

Here are some sample use cases for the Athena MCP server:

### 1. Data Analytics Dashboard

Create an n8n workflow that:
1. Periodically runs common analytics queries
2. Formats the results into charts/tables
3. Sends the report to stakeholders via email or Slack

### 2. Natural Language Query Interface

Create a Slack bot or web form that:
1. Takes questions in natural language
2. Uses the AI Agent to generate and execute SQL
3. Returns friendly, formatted responses

### 3. Security Monitoring

Use with CloudTrail logs to:
1. Detect suspicious activity patterns
2. Alert on security anomalies
3. Generate compliance reports

### 4. Customer Support Assistant

Enable support staff to:
1. Query product usage data with simple questions
2. Get insights about customer behavior
3. Make data-driven decisions without SQL knowledge

## Performance Optimization

For best performance:

1. **Partition Data**: Ensure Athena tables are properly partitioned
2. **Filter Queries**: Always include partition filters in queries
3. **Limit Results**: Use LIMIT clauses to reduce data transfer
4. **Workgroup Settings**: Configure appropriate workgroup query limits
5. **Result Reuse**: Enable query result reuse in Athena when appropriate

## Troubleshooting

If you encounter issues:

1. Check the MCP server logs: `kubectl logs deployment/athena-mcp-server`
2. Verify AWS credentials are properly configured
3. Ensure IAM permissions allow Athena access
4. Test Athena queries directly via AWS console
5. Verify network connectivity between n8n and the MCP server

See `docs/troubleshooting.md` for more detailed troubleshooting information.

## Next Steps

- Add monitoring and alerting for the MCP server
- Implement authentication for the MCP server endpoints
- Set up automated backups of Athena query results
- Explore more advanced AI interactions with your data

For any questions or support, please file an issue on the GitHub repository.
