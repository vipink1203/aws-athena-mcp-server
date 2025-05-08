import os
import logging
import asyncio
import sys
import json
import time
from typing import Dict, Any, List, Optional, AsyncIterator
from contextlib import asynccontextmanager

import boto3
from botocore.exceptions import ClientError
from mcp.server.fastmcp import FastMCP, Context
from pydantic import BaseModel, Field

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("athena-mcp")
logger.info("Starting AWS Athena MCP in SSE mode")

# Pydantic models for request/response
class QueryRequest(BaseModel):
    query: str
    database: Optional[str] = None
    catalog: Optional[str] = None
    output_location: Optional[str] = None
    workgroup: Optional[str] = None
    max_results: Optional[int] = Field(default=100, ge=1, le=1000)
    max_wait_seconds: Optional[int] = Field(default=300, ge=1, le=3600)  # Default 5 minutes, max 1 hour

class QueryResults(BaseModel):
    query_execution_id: str
    status: str
    state_change_reason: Optional[str] = None
    statistics: Optional[Dict[str, Any]] = None
    columns: Optional[List[Dict[str, str]]] = None
    rows: Optional[List[Dict[str, Any]]] = None
    error_message: Optional[str] = None
    
class AthenaClient:
    def __init__(self, region_name: Optional[str] = None):
        self.region_name = region_name or os.environ.get('AWS_REGION', 'us-east-1')
        logger.info(f"Initializing Athena client in region: {self.region_name}")
        
        # Initialize Athena client
        self.client = boto3.client('athena', region_name=self.region_name)
        
        # Get default values from environment
        self.default_catalog = os.environ.get('ATHENA_CATALOG', 'AwsDataCatalog')
        self.default_database = os.environ.get('ATHENA_DATABASE')
        self.default_workgroup = os.environ.get('ATHENA_WORKGROUP', 'primary')
        self.default_output_location = os.environ.get('ATHENA_OUTPUT_LOCATION')
        
        logger.info(f"Default catalog: {self.default_catalog}")
        logger.info(f"Default database: {self.default_database or 'Not set'}")
        logger.info(f"Default workgroup: {self.default_workgroup}")
        logger.info(f"Default output location: {self.default_output_location or 'Not set'}")
    
    async def execute_query(self, request: QueryRequest) -> QueryResults:
        """Execute an Athena query and wait for results"""
        try:
            # Prepare query execution parameters
            execute_params = {
                'QueryString': request.query,
                'WorkGroup': request.workgroup or self.default_workgroup
            }
            
            # Add optional parameters if provided
            query_execution_context = {}
            if catalog := (request.catalog or self.default_catalog):
                query_execution_context['Catalog'] = catalog
            if database := (request.database or self.default_database):
                query_execution_context['Database'] = database
            
            if query_execution_context:
                execute_params['QueryExecutionContext'] = query_execution_context
            
            # Set result configuration if output location provided
            if output_location := (request.output_location or self.default_output_location):
                execute_params['ResultConfiguration'] = {
                    'OutputLocation': output_location
                }
            
            # Start query execution
            logger.info(f"Starting query execution: {request.query[:100]}...")
            response = self.client.start_query_execution(**execute_params)
            query_execution_id = response['QueryExecutionId']
            logger.info(f"Query execution ID: {query_execution_id}")
            
            # Wait for query to complete (with timeout)
            max_wait = request.max_wait_seconds or 300  # Default 5 minutes
            start_time = time.time()
            status = 'RUNNING'
            state_change_reason = None
            
            while status in ('RUNNING', 'QUEUED') and (time.time() - start_time) < max_wait:
                await asyncio.sleep(1)  # Check every second
                
                query_details = self.client.get_query_execution(QueryExecutionId=query_execution_id)
                execution = query_details['QueryExecution']
                status = execution['Status']['State']
                state_change_reason = execution['Status'].get('StateChangeReason')
                
                # If status is no longer running/queued, break the loop
                if status not in ('RUNNING', 'QUEUED'):
                    break
            
            # If query still running after timeout, return with status
            if status in ('RUNNING', 'QUEUED') and (time.time() - start_time) >= max_wait:
                return QueryResults(
                    query_execution_id=query_execution_id,
                    status="TIMEOUT",
                    state_change_reason=f"Query exceeded maximum wait time of {max_wait} seconds"
                )
            
            # Get statistics if available
            statistics = None
            if 'Statistics' in execution:
                statistics = {
                    'processing_time_ms': execution['Statistics'].get('TotalExecutionTimeInMillis'),
                    'data_scanned_bytes': execution['Statistics'].get('DataScannedInBytes'),
                    'engine_execution_time_ms': execution['Statistics'].get('EngineExecutionTimeInMillis'),
                    'query_queue_time_ms': execution['Statistics'].get('QueryQueueTimeInMillis'),
                    'service_processing_time_ms': execution['Statistics'].get('ServiceProcessingTimeInMillis')
                }
            
            # If query succeeded, get results
            if status == 'SUCCEEDED':
                # Get results with pagination if needed
                max_results = request.max_results or 100
                results_response = self.client.get_query_results(
                    QueryExecutionId=query_execution_id,
                    MaxResults=max_results
                )
                
                # Extract column info
                columns = []
                column_info = results_response['ResultSet']['ResultSetMetadata']['ColumnInfo']
                for col in column_info:
                    columns.append({
                        'name': col['Name'],
                        'type': col['Type']
                    })
                
                # Extract data rows
                rows = []
                result_rows = results_response['ResultSet'].get('Rows', [])
                
                # Skip header row if present
                data_rows = result_rows[1:] if result_rows and len(result_rows) > 0 else []
                
                for row in data_rows:
                    row_data = {}
                    for i, col in enumerate(columns):
                        # Handle potential missing data
                        if i < len(row['Data']):
                            cell = row['Data'][i]
                            row_data[col['name']] = cell.get('VarCharValue') if 'VarCharValue' in cell else None
                        else:
                            row_data[col['name']] = None
                    rows.append(row_data)
                
                return QueryResults(
                    query_execution_id=query_execution_id,
                    status=status,
                    statistics=statistics,
                    columns=columns,
                    rows=rows
                )
            else:
                # Handle failed queries
                error_message = state_change_reason if state_change_reason else f"Query failed with status: {status}"
                return QueryResults(
                    query_execution_id=query_execution_id,
                    status=status,
                    state_change_reason=state_change_reason,
                    statistics=statistics,
                    error_message=error_message
                )
                
        except ClientError as e:
            logger.error(f"Boto3 client error: {str(e)}")
            return QueryResults(
                query_execution_id="",
                status="ERROR",
                error_message=f"AWS Client Error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Error executing query: {str(e)}", exc_info=True)
            return QueryResults(
                query_execution_id="",
                status="ERROR",
                error_message=f"Internal Server Error: {str(e)}"
            )
    
    async def list_databases(self, catalog: Optional[str] = None) -> List[str]:
        """List available databases in the given catalog"""
        try:
            catalog_name = catalog or self.default_catalog
            logger.info(f"Listing databases in catalog: {catalog_name}")
            
            response = self.client.list_databases(
                CatalogName=catalog_name
            )
            
            databases = [db['Name'] for db in response.get('DatabaseList', [])]
            return databases
        except Exception as e:
            logger.error(f"Error listing databases: {str(e)}", exc_info=True)
            return []
    
    async def list_tables(self, database: str, catalog: Optional[str] = None) -> List[str]:
        """List tables in the given database"""
        try:
            catalog_name = catalog or self.default_catalog
            logger.info(f"Listing tables in catalog: {catalog_name}, database: {database}")
            
            response = self.client.list_table_metadata(
                CatalogName=catalog_name,
                DatabaseName=database
            )
            
            tables = [table['Name'] for table in response.get('TableMetadataList', [])]
            return tables
        except Exception as e:
            logger.error(f"Error listing tables: {str(e)}", exc_info=True)
            return []
    
    async def get_table_metadata(self, table: str, database: str, catalog: Optional[str] = None) -> Dict[str, Any]:
        """Get metadata for a specific table"""
        try:
            catalog_name = catalog or self.default_catalog
            logger.info(f"Getting metadata for table: {table} in database: {database}")
            
            response = self.client.get_table_metadata(
                CatalogName=catalog_name,
                DatabaseName=database,
                TableName=table
            )
            
            table_metadata = response.get('TableMetadata', {})
            result = {
                'name': table,
                'database': database,
                'catalog': catalog_name,
                'columns': []
            }
            
            # Extract column information
            for col in table_metadata.get('Columns', []):
                result['columns'].append({
                    'name': col.get('Name'),
                    'type': col.get('Type')
                })
                
            return result
        except Exception as e:
            logger.error(f"Error getting table metadata: {str(e)}", exc_info=True)
            return {
                'name': table,
                'database': database,
                'catalog': catalog_name,
                'columns': [],
                'error': str(e)
            }

# Global client instance
athena_client = None

# Server lifespan manager
@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[None]:
    global athena_client
    logger.info("Initializing Athena client")
    
    # Get AWS region from environment variable
    region = os.getenv("AWS_REGION", "us-east-1")
    
    # Initialize Athena client
    athena_client = AthenaClient(region_name=region)
    
    try:
        yield
    finally:
        logger.info("Cleaning up resources")

# Create MCP server
mcp = FastMCP("AWS Athena MCP", lifespan=app_lifespan)

# Define tools
@mcp.tool()
async def execute_query(ctx: Context, query: str, database: Optional[str] = None, 
                      catalog: Optional[str] = None, output_location: Optional[str] = None,
                      workgroup: Optional[str] = None, max_results: Optional[int] = 100,
                      max_wait_seconds: Optional[int] = 300) -> QueryResults:
    """Execute an Athena SQL query and return results
    
    Args:
        query: SQL query to execute
        database: Optional database name (defaults to environment variable)
        catalog: Optional catalog name (defaults to environment variable or AwsDataCatalog)
        output_location: Optional S3 location for query results (defaults to environment variable)
        workgroup: Optional workgroup name (defaults to environment variable or primary)
        max_results: Maximum number of results to return (default: 100)
        max_wait_seconds: Maximum time to wait for query completion in seconds (default: 300)
        
    Returns:
        Query results including columns and data rows
    """
    logger.info(f"Tool called: execute_query(query={query[:50]}..., database={database})")
    
    request = QueryRequest(
        query=query,
        database=database,
        catalog=catalog,
        output_location=output_location,
        workgroup=workgroup,
        max_results=max_results,
        max_wait_seconds=max_wait_seconds
    )
    
    result = await athena_client.execute_query(request)
    return result

@mcp.tool()
async def list_databases(ctx: Context, catalog: Optional[str] = None) -> List[str]:
    """List available databases in the specified catalog
    
    Args:
        catalog: Optional catalog name (defaults to environment variable or AwsDataCatalog)
        
    Returns:
        List of database names
    """
    logger.info(f"Tool called: list_databases(catalog={catalog})")
    return await athena_client.list_databases(catalog)

@mcp.tool()
async def list_tables(ctx: Context, database: str, catalog: Optional[str] = None) -> List[str]:
    """List tables in the specified database
    
    Args:
        database: Database name
        catalog: Optional catalog name (defaults to environment variable or AwsDataCatalog)
        
    Returns:
        List of table names
    """
    logger.info(f"Tool called: list_tables(database={database}, catalog={catalog})")
    return await athena_client.list_tables(database, catalog)

@mcp.tool()
async def get_table_metadata(ctx: Context, table: str, database: str, catalog: Optional[str] = None) -> Dict[str, Any]:
    """Get metadata for a specific table including column definitions
    
    Args:
        table: Table name
        database: Database name
        catalog: Optional catalog name (defaults to environment variable or AwsDataCatalog)
        
    Returns:
        Table metadata including column definitions
    """
    logger.info(f"Tool called: get_table_metadata(table={table}, database={database}, catalog={catalog})")
    return await athena_client.get_table_metadata(table, database, catalog)

# Enhanced health check
async def health_check():
    if not athena_client:
        return {"status": "error", "message": "Athena client not initialized"}
    
    try:
        # Test listing databases as a simple health check
        databases = await athena_client.list_databases()
        
        # Check if we could list databases
        if databases is not None:
            return {
                "status": "ok",
                "region": athena_client.region_name,
                "default_catalog": athena_client.default_catalog,
                "default_database": athena_client.default_database,
                "default_workgroup": athena_client.default_workgroup,
                "databases_count": len(databases)
            }
        else:
            return {
                "status": "error",
                "message": "Failed to list databases",
                "region": athena_client.region_name
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}

# Main application
if __name__ == "__main__":
    try:
        # Log system information
        logger.info(f"Python version: {sys.version}")
        logger.info(f"Working directory: {os.getcwd()}")
        
        # Log relevant environment variables
        logger.info(f"AWS_REGION: {os.getenv('AWS_REGION', 'Not set - using default us-east-1')}")
        logger.info(f"ATHENA_CATALOG: {os.getenv('ATHENA_CATALOG', 'Not set - using default AwsDataCatalog')}")
        logger.info(f"ATHENA_DATABASE: {os.getenv('ATHENA_DATABASE', 'Not set')}")
        logger.info(f"ATHENA_WORKGROUP: {os.getenv('ATHENA_WORKGROUP', 'Not set - using default primary')}")
        
        # Check if output location is set and valid
        output_location = os.getenv('ATHENA_OUTPUT_LOCATION')
        if not output_location:
            logger.warning("ATHENA_OUTPUT_LOCATION not set. This may be required for some queries.")
        elif not output_location.startswith('s3://'):
            logger.warning(f"ATHENA_OUTPUT_LOCATION '{output_location}' doesn't use s3:// protocol")
        
        import uvicorn
        from starlette.applications import Starlette
        from starlette.routing import Mount
        from starlette.responses import JSONResponse
        from starlette.middleware.cors import CORSMiddleware
        
        # Create Starlette app with SSE
        sse_app = mcp.sse_app()
        
        # Add CORS support for MCP Inspector
        sse_app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],  # In production, restrict this
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        # Add health endpoint
        @sse_app.route("/health")
        async def health(request):
            result = await health_check()
            status_code = 200 if result.get("status") == "ok" else 500
            return JSONResponse(result, status_code=status_code)
        
        # Add a root endpoint for basic connectivity testing
        @sse_app.route("/")
        async def root(request):
            return JSONResponse({
                "status": "MCP server running",
                "name": "AWS Athena MCP",
                "endpoints": ["/health", "/sse"]
            })
        
        # Create main app with MCP mounted
        app = Starlette(
            routes=[Mount("/", app=sse_app)],
            lifespan=app_lifespan
        )
        
        # Run server
        host = os.getenv("HOST", "0.0.0.0")
        port = int(os.getenv("PORT", 8050))
        logger.info(f"Starting server on {host}:{port}")
        
        config = uvicorn.Config(app, host=host, port=port, log_level="debug")
        server = uvicorn.Server(config)
        server.run()  # Synchronous run to avoid event loop issues in containers
        
    except Exception as e:
        logger.error(f"Failed to start server: {e}", exc_info=True)
        sys.exit(1)