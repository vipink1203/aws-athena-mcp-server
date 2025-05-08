#!/usr/bin/env python
"""
Test script for the Athena MCP server.
This script performs basic tests against a locally running MCP server.
"""

import asyncio
import json
import logging
import sys
from typing import Dict, Any, List

import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("athena-mcp-tester")

# MCP Server URL - change this to match your deployment
MCP_SERVER_URL = "http://localhost:8050/sse"


async def test_health_endpoint() -> bool:
    """Test the health check endpoint."""
    logger.info("Testing health endpoint...")
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get("http://localhost:8050/health") as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(f"Health check response: {data}")
                    return True
                else:
                    logger.error(f"Health check failed with status {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Error connecting to health endpoint: {e}")
            return False


async def call_mcp_tool(tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Call a tool on the MCP server."""
    logger.info(f"Calling tool '{tool_name}' with params: {params}")
    
    async with aiohttp.ClientSession() as session:
        try:
            # Format the tool request
            tool_request = {
                "type": "tool_call",
                "tool": {
                    "name": tool_name,
                    "parameters": params
                }
            }
            
            # Send the request
            async with session.post(MCP_SERVER_URL, json=tool_request) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(f"Tool response: {json.dumps(data, indent=2)}")
                    return data
                else:
                    error_text = await response.text()
                    logger.error(f"Tool call failed with status {response.status}: {error_text}")
                    return {"error": f"HTTP error {response.status}", "details": error_text}
        except Exception as e:
            logger.error(f"Error calling tool: {e}")
            return {"error": str(e)}


async def test_list_databases() -> bool:
    """Test the list_databases tool."""
    result = await call_mcp_tool("list_databases", {})
    return "error" not in result


async def test_execute_query(query: str, database: str = None) -> bool:
    """Test the execute_query tool with a simple query."""
    params = {
        "query": query,
        "max_results": 5
    }
    
    if database:
        params["database"] = database
    
    result = await call_mcp_tool("execute_query", params)
    return "error" not in result


async def run_all_tests():
    """Run all tests in sequence."""
    logger.info("Starting tests...")
    
    # Test 1: Health check
    if not await test_health_endpoint():
        logger.error("Health check failed, aborting further tests")
        return
    
    # Test 2: List databases
    logger.info("Testing list_databases...")
    if not await test_list_databases():
        logger.error("list_databases failed, aborting further tests")
        return
    
    # Test 3: Execute a simple query
    logger.info("Testing execute_query with a simple query...")
    if not await test_execute_query("SELECT 1 as test"):
        logger.error("Simple query failed")
    
    # Additional tests can be added here
    
    logger.info("All tests completed")


if __name__ == "__main__":
    try:
        asyncio.run(run_all_tests())
    except KeyboardInterrupt:
        logger.info("Tests interrupted")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error during tests: {e}", exc_info=True)
        sys.exit(1)