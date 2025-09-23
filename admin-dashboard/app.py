#!/usr/bin/env python3
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import psycopg2
import json
import os
import hashlib
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import secrets

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', secrets.token_hex(32))

# Base endpoints shown in admin UI helpers
RAW_API_BASE_URL = os.getenv('MIP003_API_BASE_URL', 'http://localhost:8000')
RAW_KODOSUMI_URL = os.getenv('KODOSUMI_SERVER_URL', '')

def _normalize_base(url: str) -> str:
    return url[:-1] if url.endswith('/') else url

API_BASE_URL = _normalize_base(RAW_API_BASE_URL)
KODOSUMI_SERVER_URL = _normalize_base(RAW_KODOSUMI_URL)


def _load_starter_max_attempts() -> int:
    raw_value = os.getenv('KODOSUMI_START_MAX_ATTEMPTS', '10')
    try:
        parsed = int(raw_value)
    except ValueError:
        parsed = 10
    return max(1, parsed)


STARTER_MAX_ATTEMPTS = _load_starter_max_attempts()

# Disable template caching in development
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access the admin dashboard.'

# Simple user class for authentication
class AdminUser(UserMixin):
    def __init__(self, id):
        self.id = id

@login_manager.user_loader
def load_user(user_id):
    if user_id == 'admin':
        return AdminUser('admin')
    return None

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


def compute_result_hash(result_data):
    """Compute SHA-256 hash of the final result payload if available."""
    if not result_data:
        return None

    payload = None
    if isinstance(result_data, dict):
        for key in ('final_result', 'final', 'result', 'output'):
            if key in result_data and result_data[key] not in (None, ''):
                payload = result_data[key]
                break
        if payload is None:
            payload = result_data
    else:
        payload = result_data

    if payload is None:
        return None

    if not isinstance(payload, str):
        try:
            payload = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        except (TypeError, ValueError):
            payload = str(payload)
    else:
        payload = payload.strip()

    if not payload:
        return None

    return hashlib.sha256(payload.encode('utf-8')).hexdigest()

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page."""
    if request.method == 'POST':
        password = request.form.get('password')
        admin_password = os.getenv('ADMIN_PASSWORD')
        
        if not admin_password:
            flash('Admin password not configured. Please set ADMIN_PASSWORD in .env file.', 'error')
            return render_template('login.html')
        
        if password == admin_password:
            user = AdminUser('admin')
            login_user(user, remember=True)
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('dashboard'))
        else:
            flash('Invalid password. Please try again.', 'error')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    """Logout the user."""
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def dashboard():
    """Main dashboard page."""
    return render_template('dashboard.html')

@app.context_processor
def inject_global_urls():
    """Expose commonly used URLs to all templates."""
    return {
        'api_base_url': API_BASE_URL,
        'kodosumi_server_url': KODOSUMI_SERVER_URL,
        'starter_max_attempts': STARTER_MAX_ATTEMPTS,
    }


@app.route('/flows')
@login_required
def flows():
    """Flows management page."""
    return render_template('flows.html')

@app.route('/cron-jobs')
@login_required
def cron_jobs():
    """Cron jobs status page."""
    return render_template('cron_jobs.html')

@app.route('/jobs')
@login_required
def jobs():
    """Jobs management page."""
    return render_template('jobs.html')

@app.route('/api/flows')
@login_required
def api_flows():
    """API endpoint to get all flows."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get flows with pagination
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        search = request.args.get('search', '', type=str)
        active_only = request.args.get('active_only', 0, type=int)

        # Build query with optional filters
        conditions = []
        params = []

        if search:
            conditions.append("(summary ILIKE %s OR description ILIKE %s OR author ILIKE %s)")
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

        if active_only:
            conditions.append("NULLIF(TRIM(COALESCE(agent_identifier_default, agent_identifier, '')), '') IS NOT NULL")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Get total count
        cursor.execute(f"SELECT COUNT(*) FROM flows {where_clause}", params)
        total_count = cursor.fetchone()[0]
        
        # Get flows
        offset = (page - 1) * per_page
        cursor.execute(f"""
            SELECT id, uid, author, deprecated, description, method,
                   organization, source, summary, tags, url, url_identifier,
                   agent_identifier,
                   COALESCE(agent_identifier_default, agent_identifier) AS agent_identifier_default,
                   premium_agent_identifier,
                   free_agent_identifier,
                   free_mode_enabled,
                   created_at, updated_at
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
                'agent_identifier_default': row[13],
                'premium_agent_identifier': row[14],
                'free_agent_identifier': row[15],
                'free_mode_enabled': row[16],
                'created_at': row[17].isoformat() if row[17] else None,
                'updated_at': row[18].isoformat() if row[18] else None
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
@login_required
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
@login_required
def api_cron_status():
    """API endpoint to get cron job status."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get execution counts from cron_executions table (last 7 days)
        cursor.execute("""
            SELECT 
                service_name,
                COUNT(*) as total_executions,
                COUNT(CASE WHEN status = 'completed' THEN 1 END) as successful_executions,
                COUNT(CASE WHEN status = 'failed' THEN 1 END) as failed_executions,
                MAX(execution_time) as last_execution,
                AVG(duration_ms) as avg_duration_ms,
                SUM(items_processed) as total_items_processed
            FROM cron_executions
            WHERE execution_time > NOW() - INTERVAL '7 days'
            GROUP BY service_name
        """)
        execution_stats = {}
        for row in cursor.fetchall():
            execution_stats[row[0]] = {
                'total_executions': row[1],
                'successful_executions': row[2],
                'failed_executions': row[3],
                'last_execution': row[4],
                'avg_duration_ms': float(row[5]) if row[5] else 0,
                'total_items_processed': row[6] or 0
            }
        
        # Get last successful run details
        cursor.execute("""
            SELECT DISTINCT ON (service_name) 
                service_name,
                execution_time,
                items_processed,
                duration_ms
            FROM cron_executions
            WHERE status = 'completed'
            ORDER BY service_name, execution_time DESC
        """)
        last_successful_runs = {}
        for row in cursor.fetchall():
            last_successful_runs[row[0]] = {
                'time': row[1],
                'items_processed': row[2],
                'duration_ms': row[3]
            }
        
        cursor.close()
        conn.close()
        
        # Calculate next run times based on actual cron schedule
        now = datetime.now()
        
        # Define service configurations
        services = [
            {
                'name': 'API Key Sync',
                'service': 'authenticator',
                'schedule': 'Every 10 hours',
                'schedule_cron': '0 */10 * * *'
            },
            {
                'name': 'Flow Sync', 
                'service': 'flow-sync',
                'schedule': 'Every 30 minutes',
                'schedule_cron': '*/30 * * * *'
            },
            {
                'name': 'Payment Checker',
                'service': 'payment-checker', 
                'schedule': 'Every 2 minutes',
                'schedule_cron': '*/2 * * * *'
            },
            {
                'name': 'Kodosumi Job Starter',
                'service': 'kodosumi-starter',
                'schedule': 'Every minute',
                'schedule_cron': '* * * * *'
            },
            {
                'name': 'Kodosumi Status Checker',
                'service': 'kodosumi-status',
                'schedule': 'Every 2 minutes', 
                'schedule_cron': '*/2 * * * *'
            }
        ]
        
        cron_jobs = []
        for service_config in services:
            service_name = service_config['service']
            stats = execution_stats.get(service_name, {})
            last_successful = last_successful_runs.get(service_name, {})
            
            # Calculate health status
            if stats.get('total_executions', 0) == 0:
                status = 'never_run'
            elif stats.get('failed_executions', 0) > stats.get('successful_executions', 0):
                status = 'unhealthy'
            elif last_successful.get('time'):
                # Check if last successful run was recent enough
                last_run_time = last_successful['time']
                time_since_last = (now - last_run_time).total_seconds()
                
                # Expected intervals in seconds
                expected_intervals = {
                    'authenticator': 36000,      # 10 hours
                    'flow-sync': 1800,          # 30 minutes
                    'payment-checker': 120,     # 2 minutes
                    'kodosumi-starter': 60,     # 1 minute
                    'kodosumi-status': 120      # 2 minutes
                }
                
                expected_interval = expected_intervals.get(service_name, 3600)
                if time_since_last > expected_interval * 2:
                    status = 'warning'
                else:
                    status = 'healthy'
            else:
                status = 'unknown'
            
            # Calculate next run based on last execution
            last_exec = stats.get('last_execution')
            if last_exec:
                if service_name == 'authenticator':
                    # Next 10-hour boundary
                    hour = last_exec.hour
                    next_hour = ((hour // 10) + 1) * 10
                    if next_hour >= 24:
                        next_run = (last_exec.replace(hour=0, minute=0, second=0) + timedelta(days=1))
                    else:
                        next_run = last_exec.replace(hour=next_hour, minute=0, second=0)
                elif service_name == 'flow-sync':
                    # Next 30-minute boundary
                    next_run = last_exec + timedelta(minutes=30)
                elif service_name == 'payment-checker':
                    # Next 2-minute boundary
                    next_run = last_exec + timedelta(minutes=2)
                elif service_name == 'kodosumi-starter':
                    # Next minute
                    next_run = last_exec + timedelta(minutes=1)
                elif service_name == 'kodosumi-status':
                    # Next 2-minute boundary
                    next_run = last_exec + timedelta(minutes=2)
                else:
                    next_run = None
            else:
                next_run = now + timedelta(minutes=1)
            
            cron_job = {
                'name': service_config['name'],
                'service': service_name,
                'schedule': service_config['schedule'],
                'schedule_cron': service_config['schedule_cron'],
                'status': status,
                'total_executions': stats.get('total_executions', 0),
                'successful_executions': stats.get('successful_executions', 0),
                'failed_executions': stats.get('failed_executions', 0), 
                'total_items_processed': stats.get('total_items_processed', 0),
                'avg_duration_ms': stats.get('avg_duration_ms', 0),
                'last_execution': stats.get('last_execution').isoformat() + 'Z' if stats.get('last_execution') else None,
                'last_successful_run': {
                    'time': last_successful.get('time').isoformat() + 'Z' if last_successful.get('time') else None,
                    'items_processed': last_successful.get('items_processed', 0),
                    'duration_ms': last_successful.get('duration_ms', 0)
                },
                'next_run': next_run.isoformat() + 'Z' if next_run else None
            }
            
            cron_jobs.append(cron_job)
        
        return jsonify({'cron_jobs': cron_jobs})
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in api_cron_status: {error_details}")
        return jsonify({'error': str(e), 'details': error_details}), 500

@app.route('/api/jobs')
@login_required
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
            ORDER BY j.created_at DESC, j.job_id DESC
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
        
        response = jsonify({
            'jobs': jobs,
            'total': total_count,
            'page': page,
            'per_page': per_page,
            'pages': (total_count + per_page - 1) // per_page
        })
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/jobs/<job_id>')
@login_required
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
                   j.waiting_for_start_in_kodosumi, j.kodosumi_start_attempts,
                   f.mip003_schema
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
        mip003_schema = row[16]  # MIP003 schema from flow
        
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
            'kodosumi_fid': result_data.get('kodosumi_fid') if isinstance(result_data, dict) else None,
            'mip003_schema': mip003_schema,  # Include MIP003 schema for field names
            'output_hash': compute_result_hash(result_data)
        }
        
        cursor.close()
        conn.close()
        
        response = jsonify(job)
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in api_job_details: {error_details}")
        return jsonify({'error': str(e), 'details': error_details}), 500

@app.route('/api/stats')
@login_required
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
@login_required
def update_flow_agent(flow_id):
    """API endpoint to update flow agent identifier."""
    try:
        data = request.get_json(silent=True) or {}
        agent_default = (data.get('agent_identifier_default') or data.get('agent_identifier') or '').strip()
        premium_agent = (data.get('premium_agent_identifier') or '').strip()
        free_agent = (data.get('free_agent_identifier') or '').strip()
        raw_free_flag = data.get('free_mode_enabled', False)
        if isinstance(raw_free_flag, bool):
            free_mode_enabled = raw_free_flag
        elif isinstance(raw_free_flag, (int, float)):
            free_mode_enabled = bool(raw_free_flag)
        else:
            free_mode_enabled = str(raw_free_flag).lower() in ['1', 'true', 'yes', 'on']

        conn = get_db_connection()
        cursor = conn.cursor()

        # Update the agent identifiers for the flow
        cursor.execute("""
            UPDATE flows 
            SET agent_identifier = %s,
                agent_identifier_default = %s,
                premium_agent_identifier = %s,
                free_agent_identifier = %s,
                free_mode_enabled = %s,
                updated_at = CURRENT_TIMESTAMP 
            WHERE id = %s
        """, (
            agent_default or None,
            agent_default or None,
            premium_agent or None,
            free_agent or None,
            free_mode_enabled,
            flow_id
        ))

        if cursor.rowcount == 0:
            return jsonify({'error': 'Flow not found'}), 404

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'agent_identifier_default': agent_default,
            'premium_agent_identifier': premium_agent,
            'free_agent_identifier': free_agent,
            'free_mode_enabled': free_mode_enabled
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/logs')
@login_required
def logs():
    """Display logs page."""
    app.logger.info("[LOGS] Logs page accessed")
    from datetime import datetime
    import time
    return render_template('logs.html', now=datetime.now().isoformat(), version=int(time.time()))

@app.route('/api/logs/test')
@login_required
def test_logs_api():
    """Test endpoint for logs API."""
    return jsonify({'status': 'ok', 'message': 'Logs API is working'})

@app.route('/logs_debug')
@login_required
def logs_debug():
    """Display debug logs page."""
    return render_template('logs_debug.html')

@app.route('/api/logs/<container_name>')
@login_required
def get_container_logs(container_name):
    """Get logs for a specific container."""
    try:
        import subprocess
        
        app.logger.info(f"[LOGS API] Fetching logs for container: {container_name}")
        
        # Whitelist of allowed container names
        allowed_containers = [
            'kodosumi-starter',
            'kodosumi-status',
            'kodosumi-authenticator',
            'kodosumi-flow-sync',
            'kodosumi-payment-checker',
            'kodosumi-api-server',
            'kodosumi-admin-dashboard',
            'kodosumi-postgres',
            'kodosumi-pgadmin'
        ]
        
        if container_name not in allowed_containers:
            app.logger.error(f"[LOGS API] Invalid container name: {container_name}")
            return jsonify({'error': 'Invalid container name'}), 400
        
        # Get number of lines from query parameter (default 100)
        lines = request.args.get('lines', '100')
        try:
            lines = str(int(lines))  # Validate it's a number
        except:
            lines = '100'
        
        # Get logs from Docker
        cmd = ['docker', 'logs', '--tail', lines, container_name]
        app.logger.info(f"Running command: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False
        )
        
        app.logger.info(f"Command return code: {result.returncode}")
        app.logger.info(f"Output length: {len(result.stdout)} characters")
        
        if result.returncode != 0:
            app.logger.error(f"Docker command failed: {result.stdout}")
            return jsonify({'error': 'Failed to get logs', 'details': result.stdout}), 500
        
        # Split logs into lines and reverse to show newest first
        log_lines = result.stdout.strip().split('\n')
        
        return jsonify({
            'container': container_name,
            'logs': log_lines,
            'line_count': len(log_lines)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
