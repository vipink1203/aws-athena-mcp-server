# AWS Athena MCP Server

A Model Context Protocol (MCP) server for AWS Athena, designed to integrate with n8n AI agents.

## Overview

This MCP server provides a standardized way for n8n AI agents to query AWS Athena and retrieve data. It implements the Model Context Protocol, allowing n8n agents to:

1. List available databases and tables
2. Get table schemas and metadata
3. Execute SQL queries and retrieve results

## Features

- Simple API for interacting with Athena from n8n AI agents
- Built-in health checks and monitoring
- Configurable through environment variables
- Containerized for easy deployment
- Kubernetes-ready with sample EKS manifests

## Prerequisites

- AWS account with Athena access
- AWS credentials with appropriate permissions
- Docker for local development/testing
- Kubernetes/EKS for production deployment

## Environment Variables

The server is configured using the following environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `AWS_REGION` | AWS region for Athena | `us-east-1` |
| `ATHENA_CATALOG` | Athena catalog name | `AwsDataCatalog` |
| `ATHENA_DATABASE` | Default database to use | None |
| `ATHENA_WORKGROUP` | Athena workgroup to use | `primary` |
| `ATHENA_OUTPUT_LOCATION` | S3 location for query results | None (required) |
| `HOST` | Host to bind the server | `0.0.0.0` |
| `PORT` | Port to listen on | `8050` |

## Available Tools

The MCP server provides the following tools to n8n AI agents:

### execute_query

Execute SQL queries against Athena and retrieve results.

**Parameters:**
- `query` (string, required): SQL query to execute
- `database` (string, optional): Database name
- `catalog` (string, optional): Catalog name
- `output_location` (string, optional): S3 location for results
- `workgroup` (string, optional): Workgroup name
- `max_results` (integer, optional): Maximum number of results to return
- `max_wait_seconds` (integer, optional): Maximum time to wait for query completion

### list_databases

List available databases in a catalog.

**Parameters:**
- `catalog` (string, optional): Catalog name

### list_tables

List tables in a specific database.

**Parameters:**
- `database` (string, required): Database name
- `catalog` (string, optional): Catalog name

### get_table_metadata

Get metadata for a specific table, including column definitions.

**Parameters:**
- `table` (string, required): Table name
- `database` (string, required): Database name
- `catalog` (string, optional): Catalog name

## Local Development

### Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/aws-athena-mcp-server.git
cd aws-athena-mcp-server
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure AWS credentials:
```bash
export AWS_ACCESS_KEY_ID=your_access_key
export AWS_SECRET_ACCESS_KEY=your_secret_key
export AWS_REGION=your_region
export ATHENA_OUTPUT_LOCATION=s3://your-bucket/folder/
```

4. Run the server:
```bash
python main.py
```

The server will be available at http://localhost:8050.

## Docker Setup

### Building the Docker Image

```bash
docker build -t athena-mcp-server:latest .
```

### Running the Docker Container

```bash
docker run -p 8050:8050 \
  -e AWS_REGION=us-east-1 \
  -e ATHENA_OUTPUT_LOCATION=s3://your-bucket/folder/ \
  -e AWS_ACCESS_KEY_ID=your_access_key \
  -e AWS_SECRET_ACCESS_KEY=your_secret_key \
  athena-mcp-server:latest
```

## Deploying to AWS EKS

### Prerequisites

- AWS CLI configured with appropriate permissions
- `kubectl` installed and configured to connect to your EKS cluster
- Docker installed for building images

### Deployment Steps

1. Build and push the Docker image to ECR:

```bash
# Create ECR repository if it doesn't exist
aws ecr create-repository --repository-name athena-mcp-server

# Get ECR login command
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin ${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com

# Build and tag the image
docker build -t ${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/athena-mcp-server:latest .

# Push the image to ECR
docker push ${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/athena-mcp-server:latest
```

2. Create IAM role for the EKS service account:

```bash
# Create IAM policy
aws iam create-policy \
  --policy-name AthenaQueryPolicy \
  --policy-document file://k8s/aws-policy.json

# Create IAM role for service account (IRSA)
eksctl create iamserviceaccount \
  --name athena-mcp-server \
  --namespace default \
  --cluster your-eks-cluster \
  --attach-policy-arn arn:aws:iam::${AWS_ACCOUNT_ID}:policy/AthenaQueryPolicy \
  --approve \
  --override-existing-serviceaccounts
```

3. Update Kubernetes manifest placeholders with actual values:

```bash
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export AWS_REGION=us-east-1
export ATHENA_DEFAULT_DATABASE=your_database
export ATHENA_WORKGROUP=primary
export ATHENA_OUTPUT_LOCATION=s3://your-bucket/athena-results/

# Replace variables in YAML files
envsubst < k8s/deployment.yaml > k8s/generated/deployment.yaml
envsubst < k8s/serviceaccount.yaml > k8s/generated/serviceaccount.yaml
```

4. Apply Kubernetes manifests:

```bash
kubectl apply -f k8s/generated/serviceaccount.yaml
kubectl apply -f k8s/generated/deployment.yaml
kubectl apply -f k8s/generated/service.yaml
```

5. Verify deployment:

```bash
kubectl get pods -l app=athena-mcp-server
kubectl get service athena-mcp-server
```

6. (Optional) Create an ingress resource if you need external access.

## Integration with n8n

To use this MCP server with n8n:

1. Deploy the MCP server to your infrastructure
2. Configure the n8n AI Agent node with the appropriate System Prompt (see `docs/n8n_system_prompt_template.md`)
3. Connect to the MCP server endpoint from n8n

## n8n AI Agent System Prompt Template

Use the template in `docs/n8n_system_prompt_template.md` for the n8n AI Agent node System Prompt. This template guides the AI in:

- Understanding database schema
- Formulating efficient SQL queries
- Executing queries via the MCP server
- Presenting results clearly

Customize the template with your specific Athena database structure and common query patterns.

## Documentation

Additional documentation can be found in the `docs/` directory:

- `n8n_system_prompt_template.md`: Template for n8n AI Agent system prompt
- `implementation_guide.md`: Detailed implementation guide
- `architecture.md`: Architecture diagram and explanation
- `troubleshooting.md`: Solutions for common issues
- `n8n_integration_example.md`: Example n8n workflow
- `examples/`: Directory with example prompts for specific use cases

## License

MIT

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.