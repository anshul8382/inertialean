"""
WhatsApp Business API Routes
"""

from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from functools import wraps
import hashlib
import hmac
import logging
from services.whatsapp_service import WhatsAppService
from services.whatsapp_group_service import WhatsAppGroupService, groups_feature_enabled, whatsapp_groups_db_ready
from services.notification_service import NotificationService
from models.whatsapp_models import WhatsAppNotification, NotificationType, ClientCommunication, WhatsAppTemplate
from models import Client, User
from extensions import db

logger = logging.getLogger(__name__)

# Create blueprint
whatsapp_bp = Blueprint('whatsapp', __name__, url_prefix='/api/v1/whatsapp')


def _verify_whatsapp_signature() -> bool:
    """Verify Meta X-Hub-Signature-256 on inbound webhook POSTs."""
    secret = (current_app.config.get('WHATSAPP_APP_SECRET') or '').strip()
    if not secret:
        logger.warning("WHATSAPP_APP_SECRET not configured; rejecting webhook POST")
        return False
    header = request.headers.get('X-Hub-Signature-256', '')
    if not header.startswith('sha256='):
        return False
    expected = header[7:]
    computed = hmac.new(secret.encode('utf-8'), request.get_data(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, expected)

def handle_errors(f):
    """Decorator to handle errors in WhatsApp API routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {f.__name__}: {str(e)}")
            return jsonify({
                "success": False,
                "error": "Internal server error",
                "message": str(e)
            }), 500
    return decorated_function

@whatsapp_bp.route('/webhook', methods=['GET', 'POST'])
def webhook():
    """WhatsApp webhook endpoint"""
    try:
        if request.method == 'GET':
            # Verify webhook
            verify_token = request.args.get('hub.verify_token')
            challenge = request.args.get('hub.challenge')
            
            if verify_token == current_app.config.get('WHATSAPP_VERIFY_TOKEN'):
                logger.info("WhatsApp webhook verified successfully")
                return challenge, 200
            else:
                logger.warning("WhatsApp webhook verification failed")
                return "Verification failed", 403
        
        elif request.method == 'POST':
            if not _verify_whatsapp_signature():
                logger.warning("WhatsApp webhook signature verification failed")
                return "Invalid signature", 403
            # Handle webhook events
            webhook_data = request.get_json()
            logger.info(f"Received WhatsApp webhook: {webhook_data}")
            
            whatsapp_service = WhatsAppService()
            result = whatsapp_service.handle_webhook(webhook_data)
            
            return jsonify(result), 200
            
    except Exception as e:
        logger.error(f"Error in webhook: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@whatsapp_bp.route('/send-message', methods=['POST'])
@login_required
@handle_errors
def send_message():
    """Send WhatsApp message to client"""
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['phone_number', 'message']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    "success": False,
                    "error": f"Missing required field: {field}"
                }), 400
        
        whatsapp_service = WhatsAppService()
        result = whatsapp_service.send_text_message(
            phone_number=data['phone_number'],
            message=data['message'],
            client_id=data.get('client_id')
        )
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error sending message: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@whatsapp_bp.route('/send-template', methods=['POST'])
@login_required
@handle_errors
def send_template():
    """Send WhatsApp template message"""
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['phone_number', 'template_name']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    "success": False,
                    "error": f"Missing required field: {field}"
                }), 400
        
        whatsapp_service = WhatsAppService()
        result = whatsapp_service.send_template_message(
            phone_number=data['phone_number'],
            template_name=data['template_name'],
            template_params=data.get('template_params'),
            client_id=data.get('client_id'),
            language_code=data.get('language_code')
        )
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error sending template: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@whatsapp_bp.route('/notifications/preferences', methods=['GET', 'POST'])
@login_required
@handle_errors
def notification_preferences():
    """Get or create notification preferences"""
    try:
        if request.method == 'GET':
            client_id = request.args.get('client_id')
            
            if not client_id:
                return jsonify({
                    "success": False,
                    "error": "Client ID is required"
                }), 400
            
            # Get client's notification preferences
            preferences = WhatsAppNotification.query.filter_by(client_id=client_id).all()
            
            result = []
            for pref in preferences:
                result.append({
                    'id': pref.id,
                    'notification_type': pref.notification_type.value,
                    'is_enabled': pref.is_enabled,
                    'phone_number': pref.phone_number,
                    'threshold_value': pref.threshold_value,
                    'threshold_type': pref.threshold_type,
                    'last_sent': pref.last_sent.isoformat() if pref.last_sent else None,
                    'created_at': pref.created_at.isoformat()
                })
            
            return jsonify({
                "success": True,
                "preferences": result
            })
        
        elif request.method == 'POST':
            data = request.get_json()
            
            # Validate required fields
            required_fields = ['client_id', 'notification_type', 'phone_number']
            for field in required_fields:
                if field not in data:
                    return jsonify({
                        "success": False,
                        "error": f"Missing required field: {field}"
                    }), 400
            
            notification_service = NotificationService()
            result = notification_service.create_notification_preference(
                client_id=data['client_id'],
                notification_type=data['notification_type'],
                phone_number=data['phone_number'],
                threshold_value=data.get('threshold_value')
            )
            
            return jsonify(result)
            
    except Exception as e:
        logger.error(f"Error with notification preferences: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@whatsapp_bp.route('/notifications/send-alerts', methods=['POST'])
@login_required
@handle_errors
def send_alerts():
    """Send portfolio alerts to clients"""
    try:
        data = request.get_json() or {}
        client_id = data.get('client_id')
        
        notification_service = NotificationService()
        result = notification_service.send_portfolio_alerts(client_id)
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error sending alerts: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@whatsapp_bp.route('/notifications/send-performance', methods=['POST'])
@login_required
@handle_errors
def send_performance_updates():
    """Send performance updates to clients"""
    try:
        notification_service = NotificationService()
        result = notification_service.send_daily_performance_updates()
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error sending performance updates: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@whatsapp_bp.route('/notifications/check-price-alerts', methods=['POST'])
@login_required
@handle_errors
def check_price_alerts():
    """Check and send price alerts"""
    try:
        notification_service = NotificationService()
        result = notification_service.check_price_alerts()
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error checking price alerts: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@whatsapp_bp.route('/communications', methods=['GET'])
@login_required
@handle_errors
def get_communications():
    """Get client communications"""
    try:
        client_id = request.args.get('client_id')
        status = request.args.get('status')
        
        notification_service = NotificationService()
        communications = notification_service.get_client_communications(client_id, status)
        
        return jsonify({
            "success": True,
            "communications": communications
        })
        
    except Exception as e:
        logger.error(f"Error getting communications: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@whatsapp_bp.route('/communications/<int:communication_id>/status', methods=['PUT'])
@login_required
@handle_errors
def update_communication_status(communication_id):
    """Update communication status"""
    try:
        data = request.get_json()
        
        if 'status' not in data:
            return jsonify({
                "success": False,
                "error": "Status is required"
            }), 400
        
        notification_service = NotificationService()
        result = notification_service.update_communication_status(
            communication_id=communication_id,
            status=data['status'],
            resolution_notes=data.get('resolution_notes')
        )
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error updating communication status: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@whatsapp_bp.route('/test-message', methods=['POST'])
@login_required
@handle_errors
def test_message():
    """Send test message to verify WhatsApp integration"""
    try:
        data = request.get_json()
        phone_number = data.get('phone_number')
        
        if not phone_number:
            return jsonify({
                "success": False,
                "error": "Phone number is required"
            }), 400
        
        whatsapp_service = WhatsAppService()
        result = whatsapp_service.send_text_message(
            phone_number=phone_number,
            message="🚀 WhatsApp integration test successful! Your portfolio management system is now connected to WhatsApp Business API."
        )
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error sending test message: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@whatsapp_bp.route('/clients/<int:client_id>/whatsapp-number', methods=['PUT'])
@login_required
@handle_errors
def update_client_whatsapp_number(client_id):
    """Update client's WhatsApp number"""
    try:
        data = request.get_json()
        whatsapp_number = data.get('whatsapp_number')
        
        if not whatsapp_number:
            return jsonify({
                "success": False,
                "error": "WhatsApp number is required"
            }), 400
        
        client = Client.query.get(client_id)
        if not client:
            return jsonify({
                "success": False,
                "error": "Client not found"
            }), 404
        
        client.whatsapp_number = whatsapp_number
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": "WhatsApp number updated successfully"
        })
        
    except Exception as e:
        logger.error(f"Error updating WhatsApp number: {str(e)}")
        db.session.rollback()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@whatsapp_bp.route('/status', methods=['GET'])
@login_required
@handle_errors
def get_status():
    """Get WhatsApp integration status and configuration (includes validating token with Meta)."""
    try:
        whatsapp_service = WhatsAppService()
        is_valid, error_msg = whatsapp_service.get_connection_status()
        
        status = {
            "configured": is_valid,
            "error": error_msg if not is_valid else None,
            "config": {
                "api_url": current_app.config.get('WHATSAPP_API_URL', 'Not set'),
                "phone_number_id_set": bool(current_app.config.get('WHATSAPP_PHONE_NUMBER_ID')),
                "access_token_set": bool(current_app.config.get('WHATSAPP_ACCESS_TOKEN')),
                "verify_token_set": bool(current_app.config.get('WHATSAPP_VERIFY_TOKEN'))
            },
            "meta_groups": {
                "enabled": bool(current_app.config.get('WHATSAPP_GROUPS_ENABLED')),
                "default_join_approval": current_app.config.get(
                    'WHATSAPP_GROUPS_DEFAULT_JOIN_APPROVAL', 'auto_approve'
                ),
                "db_ready": whatsapp_groups_db_ready(),
                "feature_active": groups_feature_enabled(),
            },
        }
        
        # Get message statistics
        from models.whatsapp_models import WhatsAppMessage, MessageStatus
        status["statistics"] = {
            "total_messages": WhatsAppMessage.query.count(),
            "sent": WhatsAppMessage.query.filter_by(status=MessageStatus.SENT).count(),
            "delivered": WhatsAppMessage.query.filter_by(status=MessageStatus.DELIVERED).count(),
            "read": WhatsAppMessage.query.filter_by(status=MessageStatus.READ).count(),
            "failed": WhatsAppMessage.query.filter_by(status=MessageStatus.FAILED).count()
        }
        
        return jsonify({
            "success": True,
            "status": status
        })
        
    except Exception as e:
        logger.error(f"Error getting status: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@whatsapp_bp.route('/templates', methods=['GET', 'POST'])
@login_required
@handle_errors
def manage_templates():
    """List or create WhatsApp templates"""
    try:
        if request.method == 'GET':
            templates = WhatsAppTemplate.query.order_by(WhatsAppTemplate.updated_at.desc()).all()
            return jsonify({
                "success": True,
                "templates": [{
                    "id": template.id,
                    "name": template.name,
                    "category": template.category,
                    "language": template.language,
                    "header_type": template.header_type,
                    "header_content": template.header_content,
                    "body": template.body,
                    "footer": template.footer,
                    "status": template.status,
                    "created_at": template.created_at.isoformat() if template.created_at else None,
                    "updated_at": template.updated_at.isoformat() if template.updated_at else None
                } for template in templates]
            })

        data = request.get_json() or {}
        required_fields = ['name', 'category', 'body']
        for field in required_fields:
            if not data.get(field):
                return jsonify({
                    "success": False,
                    "error": f"Missing required field: {field}"
                }), 400

        template = WhatsAppTemplate(
            name=data['name'].strip(),
            category=data['category'].strip(),
            language=(data.get('language') or 'en_US').strip(),
            header_type=(data.get('header_type') or None),
            header_content=(data.get('header_content') or None),
            body=data['body'].strip(),
            footer=(data.get('footer') or None),
            status=(data.get('status') or 'draft').strip()
        )
        db.session.add(template)
        db.session.commit()

        return jsonify({"success": True, "template_id": template.id})
    except Exception as e:
        logger.error(f"Error managing templates: {str(e)}")
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@whatsapp_bp.route('/templates/<int:template_id>', methods=['PUT', 'DELETE'])
@login_required
@handle_errors
def update_template(template_id):
    """Update or delete a WhatsApp template"""
    try:
        template = WhatsAppTemplate.query.get(template_id)
        if not template:
            return jsonify({"success": False, "error": "Template not found"}), 404

        if request.method == 'DELETE':
            db.session.delete(template)
            db.session.commit()
            return jsonify({"success": True, "message": "Template deleted"})

        data = request.get_json() or {}
        fields = ['name', 'category', 'language', 'header_type', 'header_content', 'body', 'footer', 'status']
        for field in fields:
            if field in data:
                value = data.get(field)
                setattr(template, field, value.strip() if isinstance(value, str) else value)

        db.session.commit()
        return jsonify({"success": True, "message": "Template updated"})
    except Exception as e:
        logger.error(f"Error updating template: {str(e)}")
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@whatsapp_bp.route('/meta-groups/config', methods=['GET'])
@login_required
@handle_errors
def meta_groups_config():
    """Environment-backed Meta WhatsApp Groups API configuration (no call to Meta)."""
    try:
        svc = WhatsAppService()
        return jsonify({"success": True, "config": svc.get_meta_groups_configuration()})
    except Exception as e:
        logger.error(f"Error reading Meta Groups config: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


@whatsapp_bp.route('/meta-groups/verify', methods=['POST'])
@login_required
@handle_errors
def meta_groups_verify():
    """
    Verify Meta Groups API access by listing groups for the configured business phone number.
    Optional JSON body: { "limit": 25 }
    """
    try:
        data = request.get_json() or {}
        limit = data.get("limit", 25)
        svc = WhatsAppService()
        result = svc.verify_meta_groups_api(limit=limit)
        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code
    except Exception as e:
        logger.error(f"Error verifying Meta Groups API: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


@whatsapp_bp.route('/groups', methods=['GET'])
@login_required
@handle_errors
def list_groups():
    """List available client groupings for WhatsApp sends"""
    try:
        risk_profiles = db.session.query(Client.risk_profile).filter(
            Client.risk_profile.isnot(None)
        ).distinct().all()
        risk_profile_values = sorted({row[0] for row in risk_profiles if row[0]})

        advisor_rows = db.session.query(User.id, User.name).join(
            Client, Client.advisor_id == User.id
        ).distinct().all()
        advisors = [{"id": row[0], "name": row[1]} for row in advisor_rows]

        return jsonify({
            "success": True,
            "groups": {
                "all_clients": True,
                "risk_profiles": risk_profile_values,
                "advisors": advisors
            }
        })
    except Exception as e:
        logger.error(f"Error listing groups: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@whatsapp_bp.route('/send-group-message', methods=['POST'])
@login_required
@handle_errors
def send_group_message():
    """Send WhatsApp message to a client group"""
    try:
        data = request.get_json() or {}
        group_type = data.get('group_type')
        message = data.get('message', '').strip()

        if not group_type:
            return jsonify({"success": False, "error": "Group type is required"}), 400
        if not message:
            return jsonify({"success": False, "error": "Message is required"}), 400

        query = Client.query.filter(Client.whatsapp_number.isnot(None))

        if group_type == 'risk_profile':
            value = data.get('group_value')
            if not value:
                return jsonify({"success": False, "error": "Risk profile value is required"}), 400
            query = query.filter(Client.risk_profile == value)
        elif group_type == 'advisor':
            value = data.get('group_value')
            if not value:
                return jsonify({"success": False, "error": "Advisor is required"}), 400
            query = query.filter(Client.advisor_id == value)
        elif group_type == 'all':
            pass
        else:
            return jsonify({"success": False, "error": f"Unsupported group type: {group_type}"}), 400

        clients = query.all()
        whatsapp_service = WhatsAppService()
        sent = 0
        failed = 0
        errors = []

        for client in clients:
            result = whatsapp_service.send_text_message(
                phone_number=client.whatsapp_number,
                message=message,
                client_id=client.id
            )
            if result.get('success'):
                sent += 1
            else:
                failed += 1
                errors.append({
                    "client_id": client.id,
                    "error": result.get('error')
                })

        return jsonify({
            "success": True,
            "sent": sent,
            "failed": failed,
            "errors": errors
        })
    except Exception as e:
        logger.error(f"Error sending group message: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


# --- Meta WhatsApp Groups (client group monitoring) ---


@whatsapp_bp.route('/meta-groups/list', methods=['GET'])
@login_required
@handle_errors
def meta_groups_list():
    """List groups from Meta API (live)."""
    limit = request.args.get('limit', 100, type=int)
    after = request.args.get('after')
    svc = WhatsAppGroupService()
    result = svc.list_groups_from_meta(limit=limit, after=after)
    status_code = 200 if result.get('success') else 400
    return jsonify(result), status_code


@whatsapp_bp.route('/meta-groups/sync', methods=['POST'])
@login_required
@handle_errors
def meta_groups_sync():
    """Sync Meta groups into local whatsapp_groups table."""
    data = request.get_json() or {}
    limit = data.get('limit', 500)
    svc = WhatsAppGroupService()
    result = svc.sync_groups_from_meta(limit=limit)
    status_code = 200 if result.get('success') else 400
    return jsonify(result), status_code


@whatsapp_bp.route('/meta-groups/mappings', methods=['GET'])
@login_required
@handle_errors
def meta_groups_mappings():
    """List locally synced groups with optional client mapping."""
    mapped_only = request.args.get('mapped_only', 'false').lower() in ('1', 'true', 'yes')
    unmapped_only = request.args.get('unmapped_only', 'false').lower() in ('1', 'true', 'yes')
    svc = WhatsAppGroupService()
    groups = svc.list_local_groups(mapped_only=mapped_only, unmapped_only=unmapped_only)
    return jsonify({
        'success': True,
        'groups': groups,
        'db_ready': whatsapp_groups_db_ready(),
        'feature_enabled': groups_feature_enabled(),
    })


@whatsapp_bp.route('/meta-groups/map', methods=['POST'])
@login_required
@handle_errors
def meta_groups_map():
    """Link or unlink a Meta group to a client."""
    data = request.get_json() or {}
    group_id = data.get('group_id')
    client_id = data.get('client_id')
    if client_id is not None and client_id != '':
        client_id = int(client_id)
    else:
        client_id = None
    if not group_id:
        return jsonify({'success': False, 'error': 'group_id is required'}), 400
    svc = WhatsAppGroupService()
    result = svc.map_group_to_client(group_id, client_id, user_id=current_user.id)
    status_code = 200 if result.get('success') else 400
    return jsonify(result), status_code


@whatsapp_bp.route('/meta-groups/suggest-matches', methods=['GET'])
@login_required
@handle_errors
def meta_groups_suggest_matches():
    """Fuzzy name suggestions for unmapped groups."""
    svc = WhatsAppGroupService()
    suggestions = svc.suggest_client_matches()
    return jsonify({'success': True, 'suggestions': suggestions})


@whatsapp_bp.route('/meta-groups/<path:group_id>/messages', methods=['GET'])
@login_required
@handle_errors
def meta_groups_messages(group_id):
    """Message history for a group."""
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)
    svc = WhatsAppGroupService()
    result = svc.get_group_messages(group_id, limit=limit, offset=offset)
    status_code = 200 if result.get('success') else 400
    return jsonify(result), status_code


@whatsapp_bp.route('/meta-groups/<path:group_id>/send', methods=['POST'])
@login_required
@handle_errors
def meta_groups_send(group_id):
    """Send a text message to a Meta WhatsApp group."""
    data = request.get_json() or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({'success': False, 'error': 'message is required'}), 400
    svc = WhatsAppGroupService()
    result = svc.send_group_text(group_id, message, user_id=current_user.id)
    status_code = 200 if result.get('success') else 400
    return jsonify(result), status_code


@whatsapp_bp.route('/meta-groups/inbox', methods=['GET'])
@login_required
@handle_errors
def meta_groups_inbox():
    """Thread list for group monitoring."""
    limit = request.args.get('limit', 30, type=int)
    group_id = request.args.get('group_id')
    client_id = request.args.get('client_id', type=int)
    svc = WhatsAppGroupService()
    result = svc.get_inbox(limit=limit, group_id=group_id, client_id=client_id)
    return jsonify(result)
