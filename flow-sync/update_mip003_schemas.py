#!/usr/bin/env python3
"""
Script to update existing MIP003 schemas in the database when converter logic changes.
Currently handles textarea field upgrades and file output format migrations.
"""

import os
import sys
import json
import psycopg2

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SHARED_DIR_CANDIDATES = [
    os.path.join(SCRIPT_DIR, 'shared'),
    os.path.join(SCRIPT_DIR, '..', 'shared')
]
for shared_path in SHARED_DIR_CANDIDATES:
    if os.path.isdir(shared_path) and shared_path not in sys.path:
        sys.path.append(shared_path)

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
        file_count = 0
        file_updates = 0
        
        print(f"Found {total_flows} flows with input schemas")
        
        for flow in flows:
            uid, input_schema, old_mip003_schema, summary = flow

            # Normalise input schema to dict for inspection
            normalised_input_schema = None
            if input_schema:
                if isinstance(input_schema, dict):
                    normalised_input_schema = input_schema
                else:
                    try:
                        normalised_input_schema = json.loads(input_schema)
                    except (TypeError, ValueError):
                        normalised_input_schema = None

            contains_textarea = False
            contains_file = False
            if isinstance(normalised_input_schema, dict):
                elements = normalised_input_schema.get('elements', [])
                if isinstance(elements, list):
                    for element in elements:
                        if not isinstance(element, dict):
                            continue
                        element_type = element.get('type')
                        if element_type == 'textarea':
                            contains_textarea = True
                        elif element_type == 'file':
                            contains_file = True

            if contains_textarea:
                textarea_count += 1
            if contains_file:
                file_count += 1

            # Parse existing MIP003 schema for inspection
            normalised_mip003 = []
            if old_mip003_schema:
                if isinstance(old_mip003_schema, list):
                    normalised_mip003 = old_mip003_schema
                else:
                    try:
                        normalised_mip003 = json.loads(old_mip003_schema)
                    except (TypeError, ValueError):
                        normalised_mip003 = []

            needs_file_update = False
            if contains_file:
                if not normalised_mip003:
                    needs_file_update = True
                else:
                    for field in normalised_mip003:
                        if not isinstance(field, dict):
                            continue
                        if field.get('type') != 'file':
                            continue
                        output_format = field.get('data', {}).get('outputFormat')
                        if not output_format or str(output_format).lower() != 'url':
                            needs_file_update = True
                            break
                        validations = field.get('validations') or []
                        if any(isinstance(v, dict) and v.get('validation') == 'maxSize' for v in validations):
                            needs_file_update = True
                            break

            needs_textarea_update = contains_textarea
            should_update = needs_textarea_update or needs_file_update

            if not should_update:
                continue

            # Convert to new MIP003 format
            new_mip003_schema = convert_kodosumi_to_mip003(normalised_input_schema or {})
            
            # Update the database
            cursor.execute("""
                UPDATE flows 
                SET mip003_schema = %s, updated_at = CURRENT_TIMESTAMP 
                WHERE uid = %s
            """, (json.dumps(new_mip003_schema), uid))
            
            updated_count += 1
            change_reason = []
            if needs_textarea_update:
                change_reason.append("textarea")
            if needs_file_update:
                change_reason.append("file-output")
                file_updates += 1
            reason_str = ", ".join(change_reason) if change_reason else "general"
            print(f"✓ Updated flow: {summary} (UID: {uid}) [{reason_str}]")
            
            # Show the change for textarea fields
            if needs_textarea_update and normalised_mip003:
                for i, field in enumerate(new_mip003_schema):
                    if field['type'] == 'textarea':
                        corresponding = normalised_mip003[i] if i < len(normalised_mip003) else {}
                        if corresponding.get('type') == 'string':
                            print(f"  → Changed field '{field['id']}' from 'string' to 'textarea'")
            
            # Show change for file output format
            if needs_file_update:
                for field in new_mip003_schema:
                    if field.get('type') == 'file':
                        new_format = field.get('data', {}).get('outputFormat')
                        print(f"  → Set file field '{field.get('id')}' outputFormat to '{new_format}'")
        
        # Commit changes
        conn.commit()
        
        print(f"\nUpdate complete!")
        print(f"Total flows processed: {total_flows}")
        print(f"Flows with textarea fields: {textarea_count}")
        print(f"Flows with file fields: {file_count}")
        print(f"Flows updated: {updated_count}")
        print(f"Flows updated for file output format: {file_updates}")
        
    except Exception as e:
        print(f"Error during update: {str(e)}")
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    update_schemas()
