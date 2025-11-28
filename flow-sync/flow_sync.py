#!/usr/bin/env python3
import os
import sys
import json
import time
import httpx
import psycopg2
from datetime import datetime
from dotenv import load_dotenv
from typing import Dict, List, Optional, Any
import asyncio
from cron_logger import CronExecutionLogger

# Ensure shared utilities are importable
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SHARED_DIR_CANDIDATES = [
    os.path.join(SCRIPT_DIR, 'shared'),
    os.path.join(SCRIPT_DIR, '..', 'shared')
]
for shared_path in SHARED_DIR_CANDIDATES:
    if os.path.isdir(shared_path) and shared_path not in sys.path:
        sys.path.append(shared_path)

# Import MIP003 converter
from mip003_converter import convert_kodosumi_to_mip003

# Load environment variables
load_dotenv()

# Configuration
KODOSUMI_SERVER_URL = os.getenv('KODOSUMI_SERVER_URL')

# Database configuration
DB_CONFIG = {
    'host': os.getenv('POSTGRES_HOST', 'postgres'),
    'port': os.getenv('POSTGRES_PORT'),
    'database': os.getenv('POSTGRES_DB'),
    'user': os.getenv('POSTGRES_USER'),
    'password': os.getenv('POSTGRES_PASSWORD')
}

def get_db_connection():
    """Create and return a database connection."""
    return psycopg2.connect(**DB_CONFIG)

def get_latest_api_key() -> Optional[str]:
    """Get the latest API key from the database."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT api_key FROM api_keys 
            ORDER BY created_at DESC 
            LIMIT 1
        """)
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if result:
            print(f"Retrieved API key (first 10 chars): {result[0][:10]}...")
            return result[0]
        else:
            print("No API key found in database")
            return None
            
    except Exception as e:
        print(f"Error retrieving API key: {str(e)}")
        return None

def fetch_flows(api_key: str) -> Optional[List[Dict]]:
    """Fetch all flows from Kodosumi API with pagination."""
    try:
        all_flows = []
        offset = None
        
        while True:
            # Build URL with pagination
            url = f"{KODOSUMI_SERVER_URL}/flow"
            params = {}
            if offset:
                params['offset'] = offset
            
            print(f"Fetching flows from: {url}")
            
            response = httpx.get(
                url,
                headers={"KODOSUMI_API_KEY": api_key},
                params=params,
                timeout=30.0
            )
            
            if response.status_code != 200:
                print(f"Error fetching flows: {response.status_code}")
                print(f"Response: {response.text}")
                return None
            
            data = response.json()
            flows = data.get('items', [])
            all_flows.extend(flows)
            
            print(f"Fetched {len(flows)} flows (total: {len(all_flows)})")
            
            # Check if there are more pages
            offset = data.get('offset')
            if not offset:
                break
        
        print(f"Total flows fetched: {len(all_flows)}")
        return all_flows
        
    except Exception as e:
        print(f"Error fetching flows: {str(e)}")
        return None

def fetch_flow_input_schema(api_key: str, flow_url: str) -> Optional[Dict]:
    """Fetch input schema for a specific flow."""
    try:
        # Build the full URL for the flow schema
        schema_url = f"{KODOSUMI_SERVER_URL}{flow_url}"
        
        print(f"Fetching input schema from: {schema_url}")
        
        response = httpx.get(
            schema_url,
            headers={"KODOSUMI_API_KEY": api_key},
            timeout=30.0
        )
        
        if response.status_code != 200:
            print(f"Error fetching input schema for {flow_url}: {response.status_code}")
            print(f"Response: {response.text}")
            return None
        
        schema_data = response.json()
        print(f"Successfully fetched input schema for {flow_url}")
        return schema_data
        
    except Exception as e:
        print(f"Error fetching input schema for {flow_url}: {str(e)}")
        return None

def normalize_url(url: str) -> str:
    """Normalize specific flow URLs for consistency."""
    if not url:
        return url
    
    # Only normalize the X (Twitter) Analyzer flow URL
    if 'x-analyzer' in url:
        normalized_url = url.replace('x-analyzer', 'x_analyzer')
        print(f"   ℹ Normalized URL: {url} -> {normalized_url}")
        return normalized_url
    
    # Return all other URLs unchanged
    return url

def upsert_flow(flow_data: Dict, input_schema: Dict) -> bool:
    """Insert or update a flow in the database with MIP003 conversion."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Normalize the URL to use underscores instead of hyphens
        original_url = flow_data.get('url')
        normalized_url = normalize_url(original_url)
        
        # Convert input schema to MIP003 format
        mip003_schema = None
        if input_schema:
            try:
                mip003_schema = convert_kodosumi_to_mip003(input_schema)
                print(f"   ✓ Converted to MIP003 schema ({len(mip003_schema)} inputs)")
            except Exception as e:
                print(f"   ⚠ MIP003 conversion failed: {str(e)}")
                mip003_schema = None
        
        # Prepare the upsert query - using summary as the key instead of uid
        upsert_query = """
            INSERT INTO flows (
                uid, author, deprecated, description, method, 
                organization, source, summary, tags, url, input_schema, mip003_schema
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (summary) DO UPDATE SET
                uid = EXCLUDED.uid,
                author = EXCLUDED.author,
                deprecated = EXCLUDED.deprecated,
                description = EXCLUDED.description,
                method = EXCLUDED.method,
                organization = EXCLUDED.organization,
                source = EXCLUDED.source,
                tags = EXCLUDED.tags,
                url = EXCLUDED.url,
                input_schema = EXCLUDED.input_schema,
                mip003_schema = EXCLUDED.mip003_schema,
                updated_at = CURRENT_TIMESTAMP
        """
        
        
        # Execute the upsert
        cursor.execute(upsert_query, (
            flow_data.get('uid'),
            flow_data.get('author'),
            flow_data.get('deprecated', False),
            flow_data.get('description'),
            flow_data.get('method'),
            flow_data.get('organization'),
            flow_data.get('source'),
            flow_data.get('summary'),
            flow_data.get('tags', []),
            normalized_url,  # Use normalized URL instead of original
            json.dumps(input_schema) if input_schema else None,
            json.dumps(mip003_schema) if mip003_schema else None
        ))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return True
        
    except Exception as e:
        print(f"Error upserting flow {flow_data.get('uid', 'unknown')}: {str(e)}")
        return False

async def sync_flows_with_logging():
    """Main function to sync flows from Kodosumi API to database with execution logging."""
    # Build database URL for cron logger
    database_url = f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}@{os.getenv('POSTGRES_HOST', 'postgres')}:{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB')}"
    
    cron_logger = CronExecutionLogger('flow-sync', database_url)
    await cron_logger.log_start()
    
    items_processed = 0
    error = None
    
    try:
        print(f"\n--- Starting flow sync at {datetime.now()} ---")
        
        # Get the latest API key
        api_key = get_latest_api_key()
        if not api_key:
            print("No API key available. Skipping flow sync.")
            return False
        
        # Fetch all flows
        flows = fetch_flows(api_key)
        if not flows:
            print("No flows retrieved. Skipping sync.")
            return False
        
        # Process each flow
        success_count = 0
        error_count = 0
        
        for flow in flows:
            flow_uid = flow.get('uid', 'unknown')
            flow_url = flow.get('url')
            
            flow_summary = flow.get('summary', 'Unknown')
            print(f"\nProcessing flow: {flow_summary} (UID: {flow_uid})")
            
            # Fetch input schema for this flow
            input_schema = None
            if flow_url:
                input_schema = fetch_flow_input_schema(api_key, flow_url)
            
            # Upsert the flow to database
            if upsert_flow(flow, input_schema):
                success_count += 1
                print(f"✓ Successfully synced flow: {flow_summary} (was UID: {flow_uid})")
            else:
                error_count += 1
                print(f"✗ Failed to sync flow: {flow_summary} (UID: {flow_uid})")
        
        items_processed = success_count
        if error_count > 0:
            error = f"Failed to sync {error_count} flows"
        
        print(f"\n--- Flow sync completed at {datetime.now()} ---")
        print(f"Successfully synced: {success_count} flows")
        print(f"Errors: {error_count} flows")
        
        return error_count == 0
        
    except Exception as e:
        error = str(e)
        print(f"Error in flow sync: {error}")
        return False
        
    finally:
        await cron_logger.log_completion(items_processed, error)

def sync_flows():
    """Wrapper to run async function."""
    return asyncio.run(sync_flows_with_logging())

def wait_for_database():
    """Wait for the database to be ready."""
    max_retries = 30
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            conn = get_db_connection()
            conn.close()
            print("Database is ready!")
            return True
        except psycopg2.OperationalError:
            retry_count += 1
            print(f"Waiting for database... ({retry_count}/{max_retries})")
            time.sleep(2)
    
    print("Database connection timeout!")
    return False

def main():
    """Main entry point for the flow sync service."""
    print("Starting Kodosumi Flow Sync Service")
    print(f"Server URL: {KODOSUMI_SERVER_URL}")
    
    # Wait for database to be ready
    if not wait_for_database():
        print("Exiting due to database connection failure")
        sys.exit(1)
    
    # Run flow sync
    success = sync_flows()
    
    if success:
        print("Flow sync completed successfully")
        sys.exit(0)
    else:
        print("Flow sync completed with errors")
        sys.exit(1)

if __name__ == "__main__":
    main()
