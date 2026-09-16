"""
Airflow Management Service
Provides interface to manage and monitor Airflow DAGs using REST API
"""
import os
import subprocess
import json
import requests
from typing import Dict, List, Optional
from datetime import datetime, timezone
from urllib.parse import urlparse
import logging

logger = logging.getLogger(__name__)

class AirflowService:
    """Service for managing Airflow DAGs using REST API"""
    
    def __init__(self):
        self.app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        default_home = os.path.join(self.app_dir, 'airflow')
        existing = (os.environ.get('AIRFLOW_HOME') or '').strip()
        if existing:
            self.airflow_home = existing.rstrip('/')
        else:
            self.airflow_home = default_home
            os.environ['AIRFLOW_HOME'] = self.airflow_home
        self.airflow_base_url = os.environ.get('AIRFLOW_BASE_URL', 'http://localhost:8080').rstrip('/')
        self.api_base_url = os.environ.get(
            'AIRFLOW_API_BASE_URL',
            f'{self.airflow_base_url}/api/v2',
        )
        self.timeout = 10
        self.airflow_cmd = os.environ.get(
            "AIRFLOW_CMD",
            os.path.join(os.path.dirname(self.app_dir), "airflow_venv", "bin", "airflow"),
        )

    def _listen_port_suffix(self) -> str:
        """':port' for checking ss/netstat against AIRFLOW_BASE_URL."""
        parsed = urlparse(self.airflow_base_url)
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == 'https' else 8080
        return f':{port}'

    @staticmethod
    def _schedule_display(dag: Dict) -> str:
        sched = dag.get('schedule')
        if isinstance(sched, dict) and sched.get('value') is not None:
            return str(sched['value'])
        if isinstance(sched, str) and sched:
            return sched
        for key in ('timetable_summary', 'timetable_description'):
            v = dag.get(key)
            if v:
                return str(v)
        return 'N/A'

    @staticmethod
    def _next_logical_date(dag: Dict) -> Optional[str]:
        nd = dag.get('next_dagrun')
        if isinstance(nd, dict) and nd.get('logical_date') is not None:
            return nd.get('logical_date')
        return dag.get('next_dagrun_logical_date')

    @staticmethod
    def _last_logical_date_v1(dag: Dict) -> Optional[str]:
        ld = dag.get('last_dagrun')
        if isinstance(ld, dict) and ld.get('logical_date') is not None:
            return ld.get('logical_date')
        v = dag.get('last_dagrun_logical_date')
        return v if v is not None else None

    @staticmethod
    def _format_api_error(response: requests.Response) -> str:
        code = response.status_code
        detail = None
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = payload.get('detail') or payload.get('message') or payload.get('title')
        except Exception:
            pass
        if not detail and (response.text or '').strip():
            detail = response.text.strip()[:400]
        base = f'API returned status {code}'
        if detail:
            return f'{base}: {detail}'
        if code == 401:
            return (
                f'{base}. Set AIRFLOW_PASSWORD (or AIRFLOW_HOME pointing at the instance that '
                f'owns simple_auth_manager_passwords.json.generated) and ensure AIRFLOW_BASE_URL '
                f'matches the Airflow webserver.'
            )
        return base

    def _api_request(self, method: str, endpoint: str, **kwargs) -> Dict:
        """Make API request to Airflow REST API"""
        req_kwargs = dict(kwargs)
        try:
            url = f"{self.api_base_url}/{endpoint}"
            from services.airflow_auth_service import airflow_auth_service

            for attempt in range(2):
                merged = dict(req_kwargs)
                headers = dict(merged.pop('headers', None) or {})
                try:
                    auth_headers = airflow_auth_service.get_api_auth_headers()
                    headers.update(auth_headers)
                except Exception as auth_error:
                    logger.warning('Could not add auth headers: %s', auth_error)
                merged['headers'] = headers

                response = requests.request(
                    method=method,
                    url=url,
                    timeout=self.timeout,
                    **merged,
                )

                if response.status_code == 401 and attempt == 0:
                    logger.warning(
                        'Airflow API 401 for %s %s; clearing JWT cache and retrying once',
                        method,
                        endpoint,
                    )
                    airflow_auth_service.invalidate_jwt_cache()
                    continue

                if response.status_code in [200, 201]:
                    try:
                        return {
                            'success': True,
                            'data': response.json(),
                            'status_code': response.status_code,
                        }
                    except Exception:
                        return {
                            'success': True,
                            'data': response.text,
                            'status_code': response.status_code,
                        }
                return {
                    'success': False,
                    'error': self._format_api_error(response),
                    'status_code': response.status_code,
                    'data': response.text if response.text else None,
                }
        except requests.exceptions.ConnectionError:
            return {
                'success': False,
                'error': 'Cannot connect to Airflow API. Is Airflow running?',
                'status_code': 0
            }
        except requests.exceptions.Timeout:
            return {
                'success': False,
                'error': 'Request to Airflow API timed out',
                'status_code': 0
            }
        except Exception as e:
            logger.error(f"Airflow API request error: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'status_code': 0
            }
    
    def _run_airflow_command(self, command: List[str]) -> Dict:
        """Run an Airflow CLI command (fallback method)"""
        try:
            result = subprocess.run(
                [self.airflow_cmd] + command,
                capture_output=True,
                text=True,
                env=os.environ,
                cwd=self.app_dir,
                timeout=30
            )
            return {
                'success': result.returncode == 0,
                'output': result.stdout,
                'error': result.stderr,
                'return_code': result.returncode
            }
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'error': 'Command timed out',
                'output': '',
                'return_code': 1
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'output': '',
                'return_code': 1
            }
    
    def get_dag_list(self) -> List[Dict]:
        """Get list of all DAGs using REST API"""
        result = self._api_request('GET', 'dags', params={'limit': 100})
        
        if result['success'] and 'data' in result:
            try:
                api_data = result['data']
                dags_list = api_data.get('dags', [])
                
                # Filter and format DAGs
                custom_dags = []
                for dag in dags_list:
                    dag_id = dag.get('dag_id', '')
                    # Filter out example DAGs
                    if 'example' not in dag_id.lower():
                        owners_raw = dag.get('owners') or []
                        if owners_raw and isinstance(owners_raw[0], dict):
                            owners_list = [str(o.get('name', o)) for o in owners_raw]
                        else:
                            owners_list = [str(o) for o in owners_raw]
                        tags_raw = dag.get('tags') or []
                        tags_fmt = [
                            t.get('name', t) if isinstance(t, dict) else t for t in tags_raw
                        ]
                        last_run = self._last_logical_date_v1(dag)
                        if last_run is None:
                            runs = self.get_dag_runs(dag_id, limit=1)
                            if runs and isinstance(runs[0], dict):
                                last_run = runs[0].get('logical_date') or runs[0].get('start_date')

                        custom_dags.append({
                            'dag_id': dag_id,
                            'fileloc': dag.get('fileloc', ''),
                            'owners': ', '.join(owners_list),
                            'is_paused': dag.get('is_paused', True),
                            'state': 'paused' if dag.get('is_paused', True) else 'running',
                            'schedule': self._schedule_display(dag),
                            'next_run': self._next_logical_date(dag),
                            'last_run': last_run,
                            'tags': tags_fmt,
                            'description': dag.get('description', '') or '',
                        })
                
                return custom_dags
            except Exception as e:
                logger.error(f"Error parsing DAG list: {str(e)}")
                return []
        
        # Fallback to CLI if API fails
        return self._get_dag_list_cli()
    
    def _get_dag_list_cli(self) -> List[Dict]:
        """Fallback: Get DAG list using CLI"""
        result = self._run_airflow_command(['dags', 'list'])
        if result['success']:
            try:
                dags = []
                lines = result['output'].split('\n')
                start_parsing = False
                
                for line in lines:
                    if 'dag_id' in line.lower() and 'fileloc' in line.lower():
                        start_parsing = True
                        continue
                    if not start_parsing:
                        continue
                    if line.strip().startswith('=') or not line.strip():
                        continue
                    
                    parts = [p.strip() for p in line.split('|')]
                    if len(parts) >= 4:
                        dag_id = parts[0].strip()
                        fileloc = parts[1].strip() if len(parts) > 1 else ''
                        is_paused = parts[3].strip().lower() == 'true' if len(parts) > 3 else True
                        
                        if 'example' not in dag_id.lower():
                            dags.append({
                                'dag_id': dag_id,
                                'fileloc': fileloc,
                                'is_paused': is_paused,
                                'state': 'paused' if is_paused else 'running',
                                'schedule': 'N/A',
                                'next_run': None,
                                'last_run': None,
                                'tags': [],
                                'description': ''
                            })
                return dags
            except:
                return []
        return []
    
    def get_dag_details(self, dag_id: str) -> Dict:
        """Get detailed information about a DAG"""
        result = self._api_request('GET', f'dags/{dag_id}')
        if result['success'] and 'data' in result:
            return result['data']
        return {}
    
    def get_dag_status(self, dag_id: str) -> Dict:
        """Get status of a specific DAG"""
        dag_detail = self.get_dag_details(dag_id)
        last_run = self._last_logical_date_v1(dag_detail)
        if last_run is None:
            runs = self.get_dag_runs(dag_id, limit=1)
            if runs and isinstance(runs[0], dict):
                last_run = runs[0].get('logical_date') or runs[0].get('start_date')
        return {
            'dag_id': dag_id,
            'is_paused': dag_detail.get('is_paused', True),
            'state': 'paused' if dag_detail.get('is_paused', True) else 'running',
            'next_run': self._next_logical_date(dag_detail),
            'last_run': last_run,
            'success': True
        }
    
    def trigger_dag(self, dag_id: str, run_id: Optional[str] = None, conf: Optional[Dict] = None) -> Dict:
        """Trigger a DAG run using REST API"""
        if not run_id:
            run_id = f"manual_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        logical_date = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        payload = {
            'logical_date': logical_date,
            'conf': conf or {},
            'dag_run_id': run_id,
        }
        
        result = self._api_request('POST', f'dags/{dag_id}/dagRuns', json=payload)
        
        if result['success']:
            return {
                'success': True,
                'run_id': run_id,
                'dag_id': dag_id,
                'message': f'DAG {dag_id} triggered successfully',
                'data': result.get('data', {})
            }
        else:
            return {
                'success': False,
                'run_id': run_id,
                'dag_id': dag_id,
                'message': result.get('error', 'Failed to trigger DAG')
            }
    
    def pause_dag(self, dag_id: str) -> Dict:
        """Pause a DAG using REST API"""
        result = self._api_request('PATCH', f'dags/{dag_id}', json={'is_paused': True})
        
        if result['success']:
            return {
                'success': True,
                'dag_id': dag_id,
                'message': f'DAG {dag_id} paused successfully'
            }
        else:
            return {
                'success': False,
                'dag_id': dag_id,
                'message': result.get('error', 'Failed to pause DAG')
            }
    
    def unpause_dag(self, dag_id: str) -> Dict:
        """Unpause a DAG using REST API"""
        result = self._api_request('PATCH', f'dags/{dag_id}', json={'is_paused': False})
        
        if result['success']:
            return {
                'success': True,
                'dag_id': dag_id,
                'message': f'DAG {dag_id} unpaused successfully'
            }
        else:
            return {
                'success': False,
                'dag_id': dag_id,
                'message': result.get('error', 'Failed to unpause DAG')
            }
    
    def delete_dag(self, dag_id: str) -> Dict:
        """Delete a DAG using REST API (Airflow 3.x)"""
        result = self._api_request('DELETE', f'dags/{dag_id}')
        
        if result['success'] or result.get('status_code') in [200, 204]:
            return {
                'success': True,
                'dag_id': dag_id,
                'message': f'DAG {dag_id} deleted successfully'
            }
        else:
            return {
                'success': False,
                'dag_id': dag_id,
                'message': result.get('error', 'Failed to delete DAG')
            }
    
    def get_dag_runs(self, dag_id: str, limit: int = 10, state: Optional[str] = None) -> List[Dict]:
        """Get recent DAG runs using REST API"""
        try:
            params: Dict = {
                'limit': limit,
                'order_by': '-run_after',
            }
            if state:
                params['state'] = [state] if isinstance(state, str) else state
            
            result = self._api_request('GET', f'dags/{dag_id}/dagRuns', params=params)
            
            if result.get('success') and 'data' in result:
                try:
                    data = result['data']
                    if isinstance(data, dict):
                        runs = data.get('dag_runs', [])
                    elif isinstance(data, list):
                        runs = data
                    else:
                        runs = []
                    
                    # Ensure all items are dictionaries
                    return [r if isinstance(r, dict) else {} for r in runs[:limit]]
                except Exception as e:
                    logger.error(f"Error parsing DAG runs for {dag_id}: {str(e)}")
                    return []
            return []
        except Exception as e:
            logger.error(f"Error getting DAG runs for {dag_id}: {str(e)}")
            return []
    
    def get_next_run(self, dag_id: str) -> Optional[str]:
        """Get next scheduled run time for a DAG"""
        dag_detail = self.get_dag_details(dag_id)
        return self._next_logical_date(dag_detail)
    
    def get_dag_metrics(self, dag_id: str) -> Dict:
        """Get metrics for a DAG (success rate, avg duration, etc.)"""
        try:
            runs = self.get_dag_runs(dag_id, limit=50)
            
            if not runs or not isinstance(runs, list):
                return {
                    'total_runs': 0,
                    'successful': 0,
                    'failed': 0,
                    'success_rate': 0,
                    'avg_duration': 0,
                    'last_success': None,
                    'last_failure': None,
                    'last_run_state': None
                }
            
            total_runs = len(runs)
            successful = sum(1 for r in runs if isinstance(r, dict) and r.get('state') == 'success')
            failed = sum(1 for r in runs if isinstance(r, dict) and r.get('state') == 'failed')
            
            # Get last success and failure
            last_success = next((r for r in runs if isinstance(r, dict) and r.get('state') == 'success'), None)
            last_failure = next((r for r in runs if isinstance(r, dict) and r.get('state') == 'failed'), None)
            
            return {
                'total_runs': total_runs,
                'successful': successful,
                'failed': failed,
                'success_rate': round((successful / total_runs * 100) if total_runs > 0 else 0, 2),
                'avg_duration': 0,  # Can be calculated if needed
                'last_success': last_success.get('start_date') if last_success and isinstance(last_success, dict) else None,
                'last_failure': last_failure.get('start_date') if last_failure and isinstance(last_failure, dict) else None,
                'last_run_state': runs[0].get('state') if runs and isinstance(runs[0], dict) else None
            }
        except Exception as e:
            logger.error(f"Error getting metrics for DAG {dag_id}: {str(e)}")
            return {
                'total_runs': 0,
                'successful': 0,
                'failed': 0,
                'success_rate': 0,
                'avg_duration': 0,
                'last_success': None,
                'last_failure': None,
                'last_run_state': None
            }
    
    def is_airflow_running(self) -> bool:
        """Check if Airflow is running"""
        try:
            # Check if any Airflow processes are running (webserver, scheduler, or standalone)
            airflow_running = subprocess.run(
                ['pgrep', '-f', 'airflow (api_server|api-server|scheduler|webserver|standalone)'],
                capture_output=True,
            ).returncode == 0

            port_marker = self._listen_port_suffix()
            port_check = subprocess.run(
                ['ss', '-tuln'],
                capture_output=True,
                text=True,
            )
            ss_out = port_check.stdout or ''
            port_listening = port_marker in ss_out
            if not port_listening:
                ns = subprocess.run(
                    ['netstat', '-tuln'],
                    capture_output=True,
                    text=True,
                )
                port_listening = port_marker in (ns.stdout or '')

            return airflow_running or port_listening
        except Exception:
            # Fallback: try to connect to the API (Airflow 3: /api/v2/monitor/health)
            try:
                import urllib.request

                health_url = f'{self.airflow_base_url}/api/v2/monitor/health'
                response = urllib.request.urlopen(health_url, timeout=2)
                return response.getcode() == 200
            except Exception:
                return False
    
    def get_airflow_info(self) -> Dict:
        """Get Airflow system information"""
        # Check health via API
        health_result = self._api_request('GET', 'monitor/health')
        is_healthy = health_result.get('success', False)
        
        # Get DAG count
        dags = self.get_dag_list()
        
        return {
            'airflow_home': self.airflow_home,
            'is_running': self.is_airflow_running(),
            'is_healthy': is_healthy,
            'webserver_url': self.airflow_base_url,
            'api_url': self.api_base_url,
            'dags_directory': os.path.join(self.airflow_home, 'dags'),
            'logs_directory': os.path.join(self.airflow_home, 'logs'),
            'total_dags': len(dags),
            'active_dags': sum(1 for d in dags if not d.get('is_paused', True))
        }
    
    def get_task_logs(self, dag_id: str, task_id: str, run_id: str, try_number: int = 1) -> str:
        """Get logs for a specific task"""
        result = self._api_request('GET', f'dags/{dag_id}/dagRuns/{run_id}/taskInstances/{task_id}/logs/{try_number}')
        if result['success'] and 'data' in result:
            try:
                log_data = result['data']
                # Extract log content from response
                if isinstance(log_data, dict) and 'content' in log_data:
                    return log_data['content']
                elif isinstance(log_data, str):
                    return log_data
            except:
                pass
        return "Logs not available"

# Global instance
airflow_service = AirflowService()
