#!/usr/bin/env python3
"""
Script to update all MIP003 schemas in the database to support textarea type.
This is a one-time migration script.
"""

import os
import json
import psycopg2
from mip003_converter import convert_kodosumi_to_mip003

def get_db_connection():
    """Create database connection using environment variables."""
    return psycopg2.connect(
        dbname=os.getenv('POSTGRES_DB', 'kodosumi_db'),
        user=os.getenv('POSTGRES_USER', 'kodosumi_user'),
        password=os.getenv('POSTGRES_PASSWORD', 'kodosumi_pass'),
        host=os.getenv('POSTGRES_HOST', 'postgres'),
        port=os.getenv('POSTGRES_PORT', '5432')
    )

def update_schemas():
    """Update all MIP003 schemas in the database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Get all flows with input schemas
        cursor.execute("""
            SELECT uid, input_schema, mip003_schema, summary 
            FROM flows 
            WHERE input_schema IS NOT NULL
        """)
        
        flows = cursor.fetchall()
        total_flows = len(flows)
        updated_count = 0
        textarea_count = 0
        
        print(f"Found {total_flows} flows with input schemas")
        
        for flow in flows:
            uid, input_schema, old_mip003_schema, summary = flow
            
            # Check if input schema contains textarea
            if input_schema and 'textarea' in json.dumps(input_schema):
                textarea_count += 1
                
                # Convert to new MIP003 format
                new_mip003_schema = convert_kodosumi_to_mip003(input_schema)
                
                # Update the database
                cursor.execute("""
                    UPDATE flows 
                    SET mip003_schema = %s, updated_at = CURRENT_TIMESTAMP 
                    WHERE uid = %s
                """, (json.dumps(new_mip003_schema), uid))
                
                updated_count += 1
                print(f"✓ Updated flow: {summary} (UID: {uid})")
                
                # Show the change for textarea fields
                if old_mip003_schema:
                    old_schema = old_mip003_schema if isinstance(old_mip003_schema, list) else json.loads(old_mip003_schema)
                    for i, field in enumerate(new_mip003_schema):
                        if field['type'] == 'textarea' and i < len(old_schema) and old_schema[i].get('type') == 'string':
                            print(f"  → Changed field '{field['id']}' from 'string' to 'textarea'")
        
        # Commit changes
        conn.commit()
        
        print(f"\nUpdate complete!")
        print(f"Total flows processed: {total_flows}")
        print(f"Flows with textarea fields: {textarea_count}")
        print(f"Flows updated: {updated_count}")
        
    except Exception as e:
        print(f"Error during update: {str(e)}")
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    update_schemas()