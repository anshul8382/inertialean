"""
Client Status API Routes
Provides endpoints for client status calculation based on SLA violations
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from client_status_service import ClientStatusService
from models import Client
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Create blueprint
client_status_api = Blueprint('client_status_api', __name__, url_prefix='/api/client-status')


@client_status_api.before_request
def _client_status_api_enforce_client_access():
    from access_control import enforce_client_id_from_view_args
    return enforce_client_id_from_view_args()


@client_status_api.route('/<int:client_id>', methods=['GET'])
@login_required
def get_client_status(client_id):
    """
    Get status for a specific client
    
    Args:
        client_id (int): Client ID
        
    Returns:
        JSON: Client status information
    """
    try:
        # Check if client exists and user has access
        client = Client.query.get(client_id)
        if not client:
            return jsonify({'error': 'Client not found'}), 404
        
        # Check user permissions (basic check - can be enhanced)
        if not current_user.is_authenticated:
            return jsonify({'error': 'Authentication required'}), 401
        
        # Calculate status
        as_of = None
        as_of_str = request.args.get('as_of') or request.args.get('as_of_date')
        if as_of_str:
            try:
                as_of = datetime.fromisoformat(as_of_str.replace('Z', '+00:00'))
                if as_of.tzinfo is not None:
                    as_of = as_of.astimezone(tz=None).replace(tzinfo=None)
            except Exception:
                as_of = None

        status_data = ClientStatusService.calculate_client_status(client_id, as_of=as_of)
        
        if 'error' in status_data:
            return jsonify(status_data), 500
        
        return jsonify(status_data)
        
    except Exception as e:
        logger.error(f"Error in get_client_status API: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@client_status_api.route('/all', methods=['GET'])
@login_required
def get_all_clients_status():
    """
    Get status for all clients
    
    Returns:
        JSON: List of client status information
    """
    try:
        # Check user permissions
        if not current_user.is_authenticated:
            return jsonify({'error': 'Authentication required'}), 401
        
        as_of = None
        as_of_str = request.args.get('as_of') or request.args.get('as_of_date')
        if as_of_str:
            try:
                as_of = datetime.fromisoformat(as_of_str.replace('Z', '+00:00'))
                if as_of.tzinfo is not None:
                    as_of = as_of.astimezone(tz=None).replace(tzinfo=None)
            except Exception:
                as_of = None

        # Get all clients status
        status_data = ClientStatusService.get_all_clients_status(as_of=as_of)
        
        return jsonify({
            'clients': status_data,
            'total_clients': len(status_data),
            'summary': _generate_summary(status_data)
        })
        
    except Exception as e:
        logger.error(f"Error in get_all_clients_status API: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@client_status_api.route('/summary', methods=['GET'])
@login_required
def get_status_summary():
    """
    Get summary of client statuses
    
    Returns:
        JSON: Summary statistics
    """
    try:
        # Check user permissions
        if not current_user.is_authenticated:
            return jsonify({'error': 'Authentication required'}), 401
        
        as_of = None
        as_of_str = request.args.get('as_of') or request.args.get('as_of_date')
        if as_of_str:
            try:
                as_of = datetime.fromisoformat(as_of_str.replace('Z', '+00:00'))
                if as_of.tzinfo is not None:
                    as_of = as_of.astimezone(tz=None).replace(tzinfo=None)
            except Exception:
                as_of = None

        # Get all clients status
        status_data = ClientStatusService.get_all_clients_status(as_of=as_of)
        
        return jsonify(_generate_summary(status_data))
        
    except Exception as e:
        logger.error(f"Error in get_status_summary API: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@client_status_api.route('/bulk-update', methods=['POST'])
@login_required
def bulk_update_status():
    """
    Update status for multiple clients (for caching/performance)
    
    Returns:
        JSON: Update results
    """
    try:
        # Check user permissions
        if not current_user.is_authenticated:
            return jsonify({'error': 'Authentication required'}), 401
        
        # Get client IDs from request
        data = request.get_json()
        client_ids = data.get('client_ids', [])
        
        if not client_ids:
            return jsonify({'error': 'No client IDs provided'}), 400
        
        results = []
        for client_id in client_ids:
            status_data = ClientStatusService.calculate_client_status(client_id)
            results.append(status_data)
        
        return jsonify({
            'results': results,
            'updated_count': len(results)
        })
        
    except Exception as e:
        logger.error(f"Error in bulk_update_status API: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

def _generate_summary(status_data):
    """Generate summary statistics from status data"""
    if not status_data:
        return {
            'total_clients': 0,
            'status_breakdown': {},
            'avg_score': 0,
            'critical_clients': 0,
            'healthy_clients': 0
        }
    
    # Count by status
    status_counts = {}
    total_score = 0
    critical_count = 0
    healthy_count = 0
    
    for client in status_data:
        status = client['status']
        status_counts[status] = status_counts.get(status, 0) + 1
        total_score += client['score']
        
        if status in ['critical', 'warning']:
            critical_count += 1
        else:
            healthy_count += 1
    
    return {
        'total_clients': len(status_data),
        'status_breakdown': status_counts,
        'avg_score': round(total_score / len(status_data), 1),
        'critical_clients': critical_count,
        'healthy_clients': healthy_count,
        'health_percentage': round((healthy_count / len(status_data)) * 100, 1)
    }










