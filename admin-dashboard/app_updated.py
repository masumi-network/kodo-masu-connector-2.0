# Example of updated cron status endpoint for admin dashboard
# This shows how to query actual execution counts

@app.route('/api/cron-status')
def api_cron_status():
    """API endpoint to get cron job status with proper execution counts."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get actual execution counts from cron_executions table
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
            WHERE execution_time > NOW() - INTERVAL '7 days'  -- Last 7 days
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
        
        # Get last successful run for each service
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
        
        # Build response with proper execution counts
        cron_jobs = []
        
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
                'schedule': 'Every minute',
                'schedule_cron': '* * * * *'
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
                # Check if last successful run was recent enough based on schedule
                last_run_time = last_successful['time']
                time_since_last = (datetime.now() - last_run_time).total_seconds()
                
                # Simple check - if more than 2x the expected interval, mark as warning
                expected_intervals = {
                    'authenticator': 36000,  # 10 hours
                    'flow-sync': 1800,       # 30 minutes
                    'payment-checker': 60,   # 1 minute
                    'kodosumi-starter': 60,  # 1 minute
                    'kodosumi-status': 120   # 2 minutes
                }
                
                expected_interval = expected_intervals.get(service_name, 3600)
                if time_since_last > expected_interval * 2:
                    status = 'warning'
                else:
                    status = 'healthy'
            else:
                status = 'unknown'
            
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
                'last_execution': stats.get('last_execution').isoformat() if stats.get('last_execution') else None,
                'last_successful_run': {
                    'time': last_successful.get('time').isoformat() if last_successful.get('time') else None,
                    'items_processed': last_successful.get('items_processed', 0),
                    'duration_ms': last_successful.get('duration_ms', 0)
                }
            }
            
            # Calculate next run time based on cron schedule
            if cron_job['last_execution']:
                cron_job['next_run'] = calculate_next_run_from_cron(
                    service_config['schedule_cron'],
                    datetime.fromisoformat(cron_job['last_execution'])
                )
            
            cron_jobs.append(cron_job)
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'cron_jobs': cron_jobs,
            'summary': {
                'total_services': len(services),
                'healthy_services': sum(1 for job in cron_jobs if job['status'] == 'healthy'),
                'warning_services': sum(1 for job in cron_jobs if job['status'] == 'warning'),
                'unhealthy_services': sum(1 for job in cron_jobs if job['status'] == 'unhealthy')
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500