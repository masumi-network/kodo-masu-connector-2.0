#!/usr/bin/env python3
"""
Script to populate url_identifier field for all flows.
Creates URL-friendly identifiers like "YouTubeChannelAnalysis" from flow summaries.
"""

import psycopg2
import os
import re
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database configuration
DB_CONFIG = {
    'host': os.getenv('POSTGRES_HOST', 'postgres'),
    'port': os.getenv('POSTGRES_PORT', '5432'),
    'database': os.getenv('POSTGRES_DB', 'kodosumi_db'),
    'user': os.getenv('POSTGRES_USER', 'kodosumi_user'),
    'password': os.getenv('POSTGRES_PASSWORD', 'kodosumi_pass')
}

def generate_url_identifier(summary):
    """
    Generate a URL-friendly identifier from flow summary.
    
    Examples:
    "YouTube Channel Analysis" -> "YouTubeChannelAnalysis"
    "Advanced Web Research" -> "AdvancedWebResearch"
    "Company Research Agent" -> "CompanyResearchAgent"
    """
    if not summary:
        return "UnknownFlow"
    
    # Remove special characters and normalize
    # Keep only alphanumeric characters and spaces
    clean_name = re.sub(r'[^a-zA-Z0-9\s]', '', summary)
    
    # Split into words and capitalize each word (PascalCase)
    words = clean_name.split()
    url_identifier = ''.join(word.capitalize() for word in words if word)
    
    # Ensure it's not empty
    if not url_identifier:
        return "UnknownFlow"
    
    return url_identifier

def main():
    """Populate url_identifier for all flows."""
    try:
        # Connect to database
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        # Get all flows
        cursor.execute("SELECT id, uid, summary FROM flows")
        flows = cursor.fetchall()
        
        print(f"Processing {len(flows)} flows...")
        
        updated_count = 0
        conflicts = {}
        
        for flow_id, uid, summary in flows:
            url_identifier = generate_url_identifier(summary)
            
            # Check for conflicts
            if url_identifier in conflicts:
                # Add number suffix for conflicts
                conflicts[url_identifier] += 1
                url_identifier = f"{url_identifier}{conflicts[url_identifier]}"
            else:
                conflicts[url_identifier] = 1
            
            # Update the flow
            cursor.execute(
                "UPDATE flows SET url_identifier = %s WHERE id = %s",
                (url_identifier, flow_id)
            )
            
            print(f"  {summary} -> {url_identifier}")
            updated_count += 1
        
        # Commit changes
        conn.commit()
        print(f"\nSuccessfully updated {updated_count} flows with URL identifiers.")
        
        # Show final results
        print("\nURL Identifier Summary:")
        cursor.execute("SELECT summary, url_identifier FROM flows ORDER BY summary")
        for summary, url_id in cursor.fetchall():
            print(f"  '{summary}' -> '{url_id}'")
            
    except Exception as e:
        print(f"Error: {e}")
        if conn:
            conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

if __name__ == "__main__":
    main()