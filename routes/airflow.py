"""
Airflow Management Routes
Web interface for managing and monitoring Airflow DAGs
"""
from flask import Blueprint, render_template, jsonify, request, flash, redirect, url_for, session, Response
from flask_login import login_required, current_user
from services.airflow_service import airflow_service
import logging
import requests

logger = logging.getLogger(__name__)

airflow_bp = Blueprint('airflow', __name__, url_prefix='/airflow')


@airflow_bp.route('/')
@login_required
def index():
    """Redirect /airflow to dashboard to avoid 404"""
    return redirect(url_for('airflow.dashboard'))


@airflow_bp.route('/scheduled-jobs', methods=['GET', 'POST'])
@login_required
def scheduled_jobs():
    """
    Same scheduled-job / DAG health view as Intelligence Hub (SQLite metadata + log hints).
    Registered here so the page works when the Hub blueprint fails to load.
    """
    from flask import current_app
    from extensions import db
    from services.airflow_run_insights_service import (
        build_airflow_runs_template_context,
        sync_airflow_failure_alerts,
    )

    path = current_app.config.get("AIRFLOW_METADATA_DB_PATH")
    airflow_home = current_app.config.get("AIRFLOW_HOME")
    if request.method == "POST" and request.form.get("action") == "sync_alerts":
        try:
            sync_result = sync_airflow_failure_alerts(
                db.session, current_user.id, path, airflow_home=airflow_home
            )
            flash(
                "Alerts synced: "
                f"{sync_result.get('created', 0)} created, "
                f"{sync_result.get('updated', 0)} updated, "
                f"{sync_result.get('resolved', 0)} auto-resolved after success.",
                "success",
            )
        except Exception as exc:
            logger.error("Airflow alert sync failed: %s", exc, exc_info=True)
            flash(f"Could not sync alerts: {exc}", "error")
        return redirect(url_for("airflow.scheduled_jobs"))

    ctx = build_airflow_runs_template_context(current_app.config)
    ctx["runs_url"] = url_for("airflow.scheduled_jobs")
    return render_template("hub/airflow_runs.html", **ctx)


# Import airflow_auth_service with error handling
try:
    from services.airflow_auth_service import airflow_auth_service
except ImportError as e:
    logger.warning(f"Could not import airflow_auth_service: {e}")
    # Create a dummy service to avoid errors
    class DummyAuthService:
        def is_airflow_authenticated(self): return False
        def create_airflow_session(self): return None
        def get_proxy_headers(self): return {}
    airflow_auth_service = DummyAuthService()

@airflow_bp.route('/dashboard')
@login_required
def dashboard():
    """Airflow dashboard showing all DAGs and their status"""
    try:
        # Ensure Airflow session exists for current Flask user (if auth service available)
        try:
            if not airflow_auth_service.is_airflow_authenticated():
                airflow_auth_service.create_airflow_session()
        except Exception as auth_error:
            logger.warning(f"Could not create Airflow session: {auth_error}")
        
        info = airflow_service.get_airflow_info()
        dags = airflow_service.get_dag_list()
        
        # Get metrics for each DAG
        dag_metrics = {}
        for dag in dags:
            dag_id = dag.get('dag_id')
            if dag_id:
                try:
                    metrics = airflow_service.get_dag_metrics(dag_id)
                    # Ensure all required keys exist
                    dag_metrics[dag_id] = {
                        'total_runs': metrics.get('total_runs', 0),
                        'successful': metrics.get('successful', 0),
                        'failed': metrics.get('failed', 0),
                        'success_rate': metrics.get('success_rate', 0),
                        'last_success': metrics.get('last_success'),
                        'last_failure': metrics.get('last_failure'),
                        'last_run_state': metrics.get('last_run_state')
                    }
                except Exception as e:
                    logger.warning(f"Error getting metrics for {dag_id}: {str(e)}")
                    dag_metrics[dag_id] = {
                        'total_runs': 0,
                        'successful': 0,
                        'failed': 0,
                        'success_rate': 0,
                        'last_success': None,
                        'last_failure': None,
                        'last_run_state': None
                    }
        
        # Use direct URL instead of url_for to avoid context issues
        airflow_ui_url = "/airflow/ui"
        
        return render_template('airflow/dashboard_enhanced.html',
                             airflow_info=info,
                             dags=dags,
                             dag_metrics=dag_metrics,
                             airflow_ui_url=airflow_ui_url)
    except Exception as e:
        logger.error(f"Error loading Airflow dashboard: {str(e)}", exc_info=True)
        flash(f'Error loading Airflow dashboard: {str(e)}', 'error')
        return render_template('airflow/dashboard_enhanced.html',
                             airflow_info={'is_running': False},
                             dags=[],
                             dag_metrics={},
                             airflow_ui_url="/airflow/proxy")

@airflow_bp.route('/api/dags', methods=['GET'])
@login_required
def get_dags():
    """Get list of all DAGs"""
    try:
        dags = airflow_service.get_dag_list()
        return jsonify({
            'success': True,
            'dags': dags
        })
    except Exception as e:
        logger.error(f"Error getting DAGs: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@airflow_bp.route('/api/dags/<dag_id>/status', methods=['GET'])
@login_required
def get_dag_status(dag_id):
    """Get status of a specific DAG"""
    try:
        status = airflow_service.get_dag_status(dag_id)
        return jsonify({
            'success': True,
            'status': status
        })
    except Exception as e:
        logger.error(f"Error getting DAG status: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@airflow_bp.route('/api/dags/<dag_id>/trigger', methods=['POST'])
@login_required
def trigger_dag(dag_id):
    """Trigger a DAG run"""
    try:
        run_id = request.json.get('run_id') if request.json else None
        result = airflow_service.trigger_dag(dag_id, run_id)
        
        if result['success']:
            flash(f"DAG '{dag_id}' triggered successfully", 'success')
        else:
            flash(f"Failed to trigger DAG: {result.get('message', 'Unknown error')}", 'error')
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error triggering DAG: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@airflow_bp.route('/api/dags/<dag_id>/pause', methods=['POST'])
@login_required
def pause_dag(dag_id):
    """Pause a DAG"""
    try:
        result = airflow_service.pause_dag(dag_id)
        
        if result['success']:
            flash(f"DAG '{dag_id}' paused successfully", 'success')
        else:
            flash(f"Failed to pause DAG: {result.get('message', 'Unknown error')}", 'error')
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error pausing DAG: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@airflow_bp.route('/api/dags/<dag_id>/unpause', methods=['POST'])
@login_required
def unpause_dag(dag_id):
    """Unpause a DAG"""
    try:
        result = airflow_service.unpause_dag(dag_id)
        
        if result['success']:
            flash(f"DAG '{dag_id}' unpaused successfully", 'success')
        else:
            flash(f"Failed to unpause DAG: {result.get('message', 'Unknown error')}", 'error')
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error unpausing DAG: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@airflow_bp.route('/api/dags/<dag_id>/runs', methods=['GET'])
@login_required
def get_dag_runs(dag_id):
    """Get recent runs for a DAG"""
    try:
        limit = request.args.get('limit', 10, type=int)
        runs = airflow_service.get_dag_runs(dag_id, limit)
        return jsonify({
            'success': True,
            'runs': runs
        })
    except Exception as e:
        logger.error(f"Error getting DAG runs: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@airflow_bp.route('/api/info', methods=['GET'])
@login_required
def get_airflow_info():
    """Get Airflow system information"""
    try:
        info = airflow_service.get_airflow_info()
        return jsonify({
            'success': True,
            'info': info
        })
    except Exception as e:
        logger.error(f"Error getting Airflow info: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@airflow_bp.route('/api/dags/<dag_id>/next-run', methods=['GET'])
@login_required
def get_next_run(dag_id):
    """Get next scheduled run for a DAG"""
    try:
        next_run = airflow_service.get_next_run(dag_id)
        return jsonify({
            'success': True,
            'next_run': next_run
        })
    except Exception as e:
        logger.error(f"Error getting next run: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@airflow_bp.route('/api/dags/<dag_id>/metrics', methods=['GET'])
@login_required
def get_dag_metrics(dag_id):
    """Get metrics for a DAG"""
    try:
        metrics = airflow_service.get_dag_metrics(dag_id)
        return jsonify({
            'success': True,
            'metrics': metrics
        })
    except Exception as e:
        logger.error(f"Error getting DAG metrics: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@airflow_bp.route('/api/dags/<dag_id>/details', methods=['GET'])
@login_required
def get_dag_details(dag_id):
    """Get detailed information about a DAG"""
    try:
        details = airflow_service.get_dag_details(dag_id)
        return jsonify({
            'success': True,
            'details': details
        })
    except Exception as e:
        logger.error(f"Error getting DAG details: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@airflow_bp.route('/api/dags/<dag_id>/logs/<run_id>/<task_id>', methods=['GET'])
@login_required
def get_task_logs(dag_id, run_id, task_id):
    """Get logs for a specific task"""
    try:
        try_number = request.args.get('try_number', 1, type=int)
        logs = airflow_service.get_task_logs(dag_id, task_id, run_id, try_number)
        return jsonify({
            'success': True,
            'logs': logs
        })
    except Exception as e:
        logger.error(f"Error getting task logs: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@airflow_bp.route('/proxy')
@airflow_bp.route('/proxy/<path:path>')
@login_required
def proxy_airflow(path=''):
    """
    Proxy requests to Airflow UI, maintaining Flask authentication
    This allows users to access Airflow UI without logging in again
    """
    try:
        # Ensure we have an Airflow session
        if not airflow_auth_service.is_airflow_authenticated():
            airflow_auth_service.create_airflow_session()
        
        # Get the full path
        airflow_path = path if path else ''
        if not airflow_path:
            airflow_path = 'home'
        
        # Build Airflow URL (must match AIRFLOW_BASE_URL / api-server port)
        base = airflow_auth_service.airflow_base_url.rstrip('/')
        airflow_url = f'{base}/{airflow_path}'
        
        # Get query parameters
        query_string = request.query_string.decode('utf-8')
        if query_string:
            airflow_url += f'?{query_string}'
        
        # Get headers for proxying (includes session cookie if available)
        headers = airflow_auth_service.get_proxy_headers()
        
        # Create a session to maintain cookies
        airflow_session = requests.Session()
        session_cookie = airflow_auth_service.get_airflow_session()
        if session_cookie and session_cookie != 'proxy_auth':
            airflow_session.cookies.set('airflow_session', session_cookie, domain='localhost')
        
        # Forward the request using session to maintain cookies
        if request.method == 'GET':
            response = airflow_session.get(airflow_url, headers=headers, timeout=30, allow_redirects=True)
        elif request.method == 'POST':
            response = airflow_session.post(
                airflow_url,
                headers=headers,
                data=request.get_data(),
                timeout=30,
                allow_redirects=True
            )
        else:
            response = airflow_session.request(
                request.method,
                airflow_url,
                headers=headers,
                data=request.get_data(),
                timeout=30,
                allow_redirects=True
            )
        
        # Update Flask session with any new cookies from Airflow
        af_cookie = airflow_session.cookies.get('airflow_session') or airflow_session.cookies.get('session')
        if af_cookie:
            session['airflow_session'] = af_cookie
        
        # Create Flask response
        flask_response = Response(
            response.content,
            status=response.status_code,
            headers=dict(response.headers)
        )
        
        # Remove Airflow-specific headers that might cause issues
        flask_response.headers.pop('Content-Encoding', None)
        flask_response.headers.pop('Transfer-Encoding', None)
        flask_response.headers.pop('Content-Length', None)
        
        # Handle redirects - rewrite to use our proxy
        if response.status_code in [301, 302, 303, 307, 308]:
            location = response.headers.get('Location', '')
            base = airflow_auth_service.airflow_base_url.rstrip('/')
            if location.startswith(base):
                location = '/airflow/proxy' + location[len(base):]
            elif location.startswith('/') and not location.startswith('/airflow'):
                # Relative path - prepend proxy
                location = f'/airflow/proxy{location}'
            flask_response.headers['Location'] = location
        
        return flask_response
        
    except Exception as e:
        logger.error(f"Error proxying Airflow request: {str(e)}", exc_info=True)
        flash(f'Error accessing Airflow UI: {str(e)}', 'error')
        return redirect('/airflow/dashboard')

@airflow_bp.route('/ui')
@login_required
def airflow_ui():
    """
    Embedded Airflow UI using iframe with authentication
    For Airflow 3.x, authentication is handled by the proxy
    """
    # Mark as authenticated to allow proxy access
    # Airflow will handle actual authentication
    try:
        if not airflow_auth_service.is_airflow_authenticated():
            airflow_auth_service.create_airflow_session()
    except Exception as auth_error:
        logger.warning(f"Could not create Airflow session (using proxy): {auth_error}")
        # Continue - proxy will handle authentication
    
    return render_template('airflow/ui_embedded.html',
                         airflow_proxy_url='/airflow/proxy/home')
