#!/usr/bin/env python3
import os
import time
import httpx
import psycopg2
from datetime import datetime
from dotenv import load_dotenv
import asyncio
from typing import Optional, Tuple
from cron_logger import CronExecutionLogger

# Load environment variables
load_dotenv()

# Configuration
KODOSUMI_SERVER_URL = (os.getenv('KODOSUMI_SERVER_URL') or '').rstrip('/') or None
KODOSUMI_USERNAME = os.getenv('KODOSUMI_USERNAME')
KODOSUMI_PASSWORD = os.getenv('KODOSUMI_PASSWORD')

# Database configuration
DB_CONFIG = {
    'host': os.getenv('POSTGRES_HOST'),
    'port': os.getenv('POSTGRES_PORT'),
    'database': os.getenv('POSTGRES_DB'),
    'user': os.getenv('POSTGRES_USER'),
    'password': os.getenv('POSTGRES_PASSWORD')
}

def get_db_connection():
    """Create and return a database connection."""
    return psycopg2.connect(**DB_CONFIG)

def authenticate_and_get_credentials() -> Tuple[Optional[str], Optional[str]]:
    """Authenticate with Kodosumi server and retrieve API key plus session cookie."""
    try:
        print(f"Authenticating with Kodosumi server at {KODOSUMI_SERVER_URL}")
        
        # Try /api/login endpoint with JSON
        response = httpx.post(
            f"{KODOSUMI_SERVER_URL}/api/login",
            json={
                "name": KODOSUMI_USERNAME,
                "password": KODOSUMI_PASSWORD
            },
            timeout=30.0
        )
        
        if response.status_code == 200:
            api_key = response.json().get("KODOSUMI_API_KEY")
            session_cookie = response.cookies.get("kodosumi_jwt")

            if api_key:
                if session_cookie:
                    print("Successfully authenticated and retrieved API key and session cookie")
                else:
                    print("Successfully authenticated and retrieved API key (no session cookie found)")
                return api_key, session_cookie
            else:
                print("No API key found in response")
                return None, None
        else:
            print(f"Authentication failed with status code: {response.status_code}")
            print(f"Response: {response.text}")
            return None, None
            
    except Exception as e:
        print(f"Error during authentication: {str(e)}")
        return None, None

def store_api_key(api_key: str, session_cookie: Optional[str]):
    """Store the API key and optional session cookie in the database."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Insert the new API key
        cursor.execute(
            "INSERT INTO api_keys (api_key, session_cookie) VALUES (%s, %s)",
            (api_key, session_cookie)
        )
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print(f"API key stored successfully at {datetime.now()}")
        
    except Exception as e:
        print(f"Error storing API key: {str(e)}")

async def refresh_api_key_with_logging():
    """Main function to refresh the API key with execution logging."""
    # Build database URL for cron logger
    database_url = f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}@{os.getenv('POSTGRES_HOST', 'postgres')}:{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB')}"
    
    cron_logger = CronExecutionLogger('authenticator', database_url)
    await cron_logger.log_start()
    
    items_processed = 0
    error = None
    
    try:
        print(f"\n--- Starting API key refresh at {datetime.now()} ---")
        
        api_key, session_cookie = authenticate_and_get_credentials()
        
        if api_key:
            store_api_key(api_key, session_cookie)
            items_processed = 1  # Successfully stored API credentials
        else:
            error = "Failed to retrieve API key"
            print(error)
        
        print("--- API key refresh completed ---\n")
        
    except Exception as e:
        error = str(e)
        print(f"Error in API key refresh: {error}")
        
    finally:
        await cron_logger.log_completion(items_processed, error)

def refresh_api_key():
    """Wrapper to run async function."""
    asyncio.run(refresh_api_key_with_logging())

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
    """Main entry point for the authenticator service."""
    print("Starting Kodosumi Authenticator Service")
    print(f"Server URL: {KODOSUMI_SERVER_URL}")
    print(f"Username: {KODOSUMI_USERNAME}")
    
    # Wait for database to be ready
    if not wait_for_database():
        print("Exiting due to database connection failure")
        return
    
    # Run authentication (will be called by cron)
    refresh_api_key()

if __name__ == "__main__":
    main()
