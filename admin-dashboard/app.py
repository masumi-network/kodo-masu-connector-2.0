#!/usr/bin/env python3
from flask import Flask, render_template, jsonify, request
import psycopg2
import json
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)

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

@app.route('/')
def dashboard():
    """Main dashboard page."""
    return render_template('dashboard.html')

@app.route('/flows')
def flows():
    """Flows management page."""
    return render_template('flows.html')

@app.route('/cron-jobs')
def cron_jobs():
    """Cron jobs status page."""
    return render_template('cron_jobs.html')

@app.route('/jobs')
def jobs():
    """Jobs management page."""
    return render_template('jobs.html')

@app.route('/api/flows')
def api_flows():
    """API endpoint to get all flows."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get flows with pagination
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        search = request.args.get('search', '', type=str)
        
        # Build query with search
        where_clause = ""
        params = []
        if search:
            where_clause = "WHERE summary ILIKE %s OR description ILIKE %s OR author ILIKE %s"
            params = [f"%{search}%", f"%{search}%", f"%{search}%"]
        
        # Get total count
        cursor.execute(f"SELECT COUNT(*) FROM flows {where_clause}", params)
        total_count = cursor.fetchone()[0]
        
        # Get flows
        offset = (page - 1) * per_page
        cursor.execute(f"""
            SELECT id, uid, author, deprecated, description, method, 
                   organization, source, summary, tags, url, url_identifier,
                   agent_identifier, created_at, updated_at
            FROM flows {where_clause}
            ORDER BY updated_at DESC
            LIMIT %s OFFSET %s
        """, params + [per_page, offset])
        
        flows = []
        for row in cursor.fetchall():
            flows.append({
                'id': row[0],
                'uid': row[1],
                'author': row[2],
                'deprecated': row[3],
                'description': row[4],
                'method': row[5],
                'organization': row[6],
                'source': row[7],
                'summary': row[8],
                'tags': row[9],
                'url': row[10],
                'url_identifier': row[11],
                'agent_identifier': row[12],
                'created_at': row[13].isoformat() if row[13] else None,
                'updated_at': row[14].isoformat() if row[14] else None
            })
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'flows': flows,
            'total': total_count,
            'page': page,
            'per_page': per_page,
            'pages': (total_count + per_page - 1) // per_page
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/flows/<flow_id>/schema')
def api_flow_schema(flow_id):
    """API endpoint to get flow input schema."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT input_schema, mip003_schema FROM flows WHERE id = %s", (flow_id,))
        result = cursor.fetchone()
        
        if result:
            # PostgreSQL JSONB columns return dict objects, not strings
            original_schema = result[0] if result[0] else None
            mip003_schema = result[1] if result[1] else None
            
            return jsonify({
                'original_schema': original_schema,
                'mip003_schema': mip003_schema
            })
        else:
            return jsonify({'error': 'Schema not found'}), 404
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/api/cron-status')
def api_cron_status():
    """API endpoint to get cron job status."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get API key sync status
        cursor.execute("""
            SELECT COUNT(*) as total_keys, 
                   MAX(created_at) as last_sync
            FROM api_keys
        """)
        api_key_result = cursor.fetchone()
        
        # Get flow sync status
        cursor.execute("""
            SELECT COUNT(*) as total_flows, 
                   MAX(updated_at) as last_sync
            FROM flows
        """)
        flow_result = cursor.fetchone()
        
        # Get payment checker status (jobs updated from awaiting_payment to running)
        cursor.execute("""
            SELECT COUNT(*) as jobs_processed, 
                   MAX(updated_at) as last_run
            FROM jobs 
            WHERE status = 'running' AND waiting_for_start_in_kodosumi = true
        """)
        payment_checker_result = cursor.fetchone()
        
        # Get Kodosumi starter status (jobs with waiting_for_start_in_kodosumi set to false)
        cursor.execute("""
            SELECT COUNT(*) as jobs_started, 
                   MAX(updated_at) as last_run
            FROM jobs 
            WHERE waiting_for_start_in_kodosumi = false 
            AND result::text LIKE '%kodosumi_fid%'
        """)
        kodosumi_starter_result = cursor.fetchone()
        
        # Get Kodosumi status checker results (completed jobs)
        cursor.execute("""
            SELECT COUNT(*) as jobs_completed, 
                   MAX(updated_at) as last_run
            FROM jobs 
            WHERE status = 'completed' 
            AND result::text LIKE '%final_result%'
        """)
        kodosumi_status_result = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        # Calculate next run times based on actual cron schedule
        now = datetime.now()
        
        # API key sync: "0 */10 * * *" - runs at 0:00, 10:00, 20:00 daily
        last_api_sync = api_key_result[1] if api_key_result[1] else now
        # Find next scheduled time
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        scheduled_times = [
            today.replace(hour=0),   # 00:00 today
            today.replace(hour=10),  # 10:00 today  
            today.replace(hour=20)   # 20:00 today
        ]
        
        # Find the next scheduled time after now
        next_api_sync = None
        for scheduled_time in scheduled_times:
            if scheduled_time > now:
                next_api_sync = scheduled_time
                break
        
        # If no time today, use 00:00 tomorrow
        if not next_api_sync:
            next_api_sync = (today + timedelta(days=1)).replace(hour=0)
        
        # Flow sync: "*/30 * * * *" - runs every 30 minutes (0, 30 minutes past each hour)
        last_flow_sync = flow_result[1] if flow_result[1] else now
        # Calculate next 30-minute boundary
        current_minute = now.minute
        if current_minute < 30:
            next_flow_sync = now.replace(minute=30, second=0, microsecond=0)
        else:
            next_flow_sync = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
        
        # Calculate next run time for payment checker (every minute)
        last_payment_check = payment_checker_result[1] if payment_checker_result[1] else now
        next_payment_check = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        
        # Calculate next run time for Kodosumi starter (every minute)
        last_kodosumi_start = kodosumi_starter_result[1] if kodosumi_starter_result[1] else now
        next_kodosumi_start = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        
        # Calculate next run time for Kodosumi status checker (every 2 minutes)
        last_kodosumi_status = kodosumi_status_result[1] if kodosumi_status_result[1] else now
        # Round up to next even minute
        current_minute = now.minute
        if current_minute % 2 == 0:
            next_kodosumi_status = now.replace(second=0, microsecond=0) + timedelta(minutes=2)
        else:
            next_kodosumi_status = now.replace(second=0, microsecond=0) + timedelta(minutes=(2 - current_minute % 2))
        
        cron_jobs = [
            {
                'name': 'API Key Sync',
                'service': 'authenticator',
                'schedule': 'Every 10 hours',
                'last_run': last_api_sync.isoformat() if last_api_sync else None,
                'next_run': next_api_sync.isoformat() if next_api_sync else None,
                'status': 'active' if api_key_result[0] > 0 else 'inactive',
                'total_executions': api_key_result[0]
            },
            {
                'name': 'Flow Sync',
                'service': 'flow-sync',
                'schedule': 'Every 30 minutes',
                'last_run': last_flow_sync.isoformat() if last_flow_sync else None,
                'next_run': next_flow_sync.isoformat() if next_flow_sync else None,
                'status': 'active' if flow_result[0] > 0 else 'inactive',
                'total_executions': flow_result[0]
            },
            {
                'name': 'Payment Checker',
                'service': 'payment-checker',
                'schedule': 'Every minute',
                'last_run': last_payment_check.isoformat() if last_payment_check else None,
                'next_run': next_payment_check.isoformat() if next_payment_check else None,
                'status': 'active' if payment_checker_result[0] > 0 else 'inactive',
                'total_executions': payment_checker_result[0]
            },
            {
                'name': 'Kodosumi Job Starter',
                'service': 'kodosumi-starter',
                'schedule': 'Every minute',
                'last_run': last_kodosumi_start.isoformat() if last_kodosumi_start else None,
                'next_run': next_kodosumi_start.isoformat() if next_kodosumi_start else None,
                'status': 'active' if kodosumi_starter_result[0] > 0 else 'inactive',
                'total_executions': kodosumi_starter_result[0]
            },
            {
                'name': 'Kodosumi Status Checker',
                'service': 'kodosumi-status',
                'schedule': 'Every 2 minutes',
                'last_run': last_kodosumi_status.isoformat() if last_kodosumi_status else None,
                'next_run': next_kodosumi_status.isoformat() if next_kodosumi_status else None,
                'status': 'active' if kodosumi_status_result[0] > 0 else 'inactive',
                'total_executions': kodosumi_status_result[0]
            }
        ]
        
        return jsonify({'cron_jobs': cron_jobs})
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in api_cron_status: {error_details}")
        return jsonify({'error': str(e), 'details': error_details}), 500

@app.route('/api/jobs')
def api_jobs():
    """API endpoint to get all jobs."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get query parameters
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        search = request.args.get('search', '', type=str)
        
        # Build query with search
        where_clause = ""
        params = []
        if search:
            where_clause = """WHERE j.job_id::text ILIKE %s 
                             OR j.identifier_from_purchaser ILIKE %s 
                             OR j.status ILIKE %s 
                             OR f.summary ILIKE %s"""
            search_param = f"%{search}%"
            params = [search_param, search_param, search_param, search_param]
        
        # Get total count
        count_query = f"""
            SELECT COUNT(*) 
            FROM jobs j 
            LEFT JOIN flows f ON j.flow_uid = f.uid 
            {where_clause}
        """
        cursor.execute(count_query, params)
        total_count = cursor.fetchone()[0]
        
        # Get jobs with flow information
        offset = (page - 1) * per_page
        jobs_query = f"""
            SELECT j.job_id, j.flow_uid, j.status, j.identifier_from_purchaser,
                   j.input_hash, j.created_at, j.updated_at, j.message,
                   f.summary as flow_summary,
                   j.payment_data->'data'->>'blockchainIdentifier' as blockchain_identifier,
                   j.waiting_for_start_in_kodosumi,
                   j.result->>'kodosumi_fid' as kodosumi_fid,
                   j.kodosumi_start_attempts
            FROM jobs j 
            LEFT JOIN flows f ON j.flow_uid = f.uid 
            {where_clause}
            ORDER BY j.created_at DESC
            LIMIT %s OFFSET %s
        """
        cursor.execute(jobs_query, params + [per_page, offset])
        
        jobs = []
        for row in cursor.fetchall():
            jobs.append({
                'job_id': row[0],
                'flow_uid': row[1],
                'status': row[2],
                'identifier_from_purchaser': row[3],
                'input_hash': row[4],
                'created_at': row[5].isoformat() if row[5] else None,
                'updated_at': row[6].isoformat() if row[6] else None,
                'message': row[7],
                'flow_summary': row[8],
                'blockchain_identifier': row[9],
                'waiting_for_start_in_kodosumi': row[10],
                'kodosumi_fid': row[11],
                'kodosumi_start_attempts': row[12]
            })
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'jobs': jobs,
            'total': total_count,
            'page': page,
            'per_page': per_page,
            'pages': (total_count + per_page - 1) // per_page
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/jobs/<job_id>')
def api_job_details(job_id):
    """API endpoint to get job details."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT j.job_id, j.flow_uid, j.status, j.identifier_from_purchaser,
                   j.input_data, j.payment_data, j.result, j.reasoning,
                   j.input_hash, j.created_at, j.updated_at, j.message,
                   f.summary as flow_summary, f.agent_identifier,
                   j.waiting_for_start_in_kodosumi, j.kodosumi_start_attempts
            FROM jobs j 
            LEFT JOIN flows f ON j.flow_uid = f.uid 
            WHERE j.job_id = %s
        """, (job_id,))
        
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'Job not found'}), 404
        
        # PostgreSQL JSONB columns return native Python objects, no need to parse
        input_data = row[4]  # Already a dict/list from JSONB
        payment_data = row[5]  # Already a dict/list from JSONB  
        result_data = row[6]  # Already a dict/list from JSONB
        
        # Extract blockchain identifier from nested payment data structure
        blockchain_identifier = None
        if payment_data and isinstance(payment_data, dict):
            # Check if it's nested under 'data' key (new structure)
            if 'data' in payment_data and isinstance(payment_data['data'], dict):
                blockchain_identifier = payment_data['data'].get('blockchainIdentifier')
            # Fallback to direct access (old structure)
            else:
                blockchain_identifier = payment_data.get('blockchainIdentifier')
        
        job = {
            'job_id': row[0],
            'flow_uid': row[1],
            'status': row[2],
            'identifier_from_purchaser': row[3],
            'input_data': input_data,
            'payment_data': payment_data,
            'result': result_data,
            'reasoning': row[7],
            'input_hash': row[8],
            'created_at': row[9].isoformat() if row[9] else None,
            'updated_at': row[10].isoformat() if row[10] else None,
            'message': row[11],
            'flow_summary': row[12],
            'flow_agent_identifier': row[13],  # Add the flow's configured agent identifier
            'blockchain_identifier': blockchain_identifier,
            'waiting_for_start_in_kodosumi': row[14],
            'kodosumi_start_attempts': row[15],
            'kodosumi_fid': result_data.get('kodosumi_fid') if result_data else None
        }
        
        cursor.close()
        conn.close()
        
        return jsonify(job)
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in api_job_details: {error_details}")
        return jsonify({'error': str(e), 'details': error_details}), 500

@app.route('/api/stats')
def api_stats():
    """API endpoint to get dashboard statistics."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get flow statistics
        cursor.execute("SELECT COUNT(*) FROM flows")
        total_flows = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM flows WHERE deprecated = true")
        deprecated_flows = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(DISTINCT author) FROM flows WHERE author IS NOT NULL")
        unique_authors = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM api_keys")
        total_api_keys = cursor.fetchone()[0]
        
        # Get job statistics
        cursor.execute("SELECT COUNT(*) FROM jobs")
        total_jobs = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM jobs WHERE status = 'completed'")
        completed_jobs = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM jobs WHERE status = 'running'")
        running_jobs = cursor.fetchone()[0]
        
        # Get recent activity
        cursor.execute("""
            SELECT summary, updated_at 
            FROM flows 
            ORDER BY updated_at DESC 
            LIMIT 5
        """)
        recent_flows = [{'name': row[0], 'updated': row[1].isoformat()} for row in cursor.fetchall()]
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'total_flows': total_flows,
            'deprecated_flows': deprecated_flows,
            'active_flows': total_flows - deprecated_flows,
            'unique_authors': unique_authors,
            'total_api_keys': total_api_keys,
            'total_jobs': total_jobs,
            'completed_jobs': completed_jobs,
            'running_jobs': running_jobs,
            'recent_flows': recent_flows
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/flows/<int:flow_id>/agent', methods=['PUT'])
def update_flow_agent(flow_id):
    """API endpoint to update flow agent identifier."""
    try:
        data = request.get_json()
        agent_identifier = data.get('agent_identifier', '').strip()
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Update the agent_identifier for the flow
        cursor.execute("""
            UPDATE flows 
            SET agent_identifier = %s, updated_at = CURRENT_TIMESTAMP 
            WHERE id = %s
        """, (agent_identifier if agent_identifier else None, flow_id))
        
        if cursor.rowcount == 0:
            return jsonify({'error': 'Flow not found'}), 404
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({'success': True, 'agent_identifier': agent_identifier})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)