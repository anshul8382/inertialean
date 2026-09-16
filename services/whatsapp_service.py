"""
WhatsApp Business API Service
Handles all WhatsApp API interactions
"""

import requests
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from flask import current_app
from models.whatsapp_models import WhatsAppMessage, MessageStatus, MessageType, WhatsAppTemplate
from extensions import db

logger = logging.getLogger(__name__)

class WhatsAppService:
    """Service for WhatsApp Business API integration"""
    
    def __init__(self):
        self.base_url = current_app.config.get('WHATSAPP_API_URL', 'https://graph.facebook.com/v18.0')
        # Clean and strip the access token (remove quotes, whitespace)
        raw_token = current_app.config.get('WHATSAPP_ACCESS_TOKEN', '')
        self.access_token = raw_token.strip().strip('"').strip("'") if raw_token else None
        self.phone_number_id = (current_app.config.get('WHATSAPP_PHONE_NUMBER_ID') or '').strip()
        self.verify_token = (current_app.config.get('WHATSAPP_VERIFY_TOKEN') or '').strip()
    
    def _validate_config(self) -> Tuple[bool, Optional[str]]:
        """Validate WhatsApp configuration"""
        if not self.access_token:
            return False, "WhatsApp Access Token is not configured. Please set WHATSAPP_ACCESS_TOKEN in your environment variables."
        
        # Check if token is still a placeholder
        if self.access_token in ['your_access_token_here', '']:
            return False, "WhatsApp Access Token is not configured. Please set WHATSAPP_ACCESS_TOKEN in your .env file with your actual token from Meta Business Manager."
        
        # Basic token format validation (WhatsApp tokens are typically long strings, usually 200+ chars)
        if len(self.access_token) < 50:
            return False, f"Invalid access token format. Token appears too short ({len(self.access_token)} chars). WhatsApp tokens are usually 200+ characters. Please verify your token from Meta Business Manager."
        
        # Check for common token format issues
        if not self.access_token.startswith('EAA'):
            logger.warning(
                "WhatsApp access credential may be invalid (expected EAA prefix)"
            )
        
        # Check for placeholder values
        if 'your_' in self.access_token.lower() or 'placeholder' in self.access_token.lower():
            return False, "Access token appears to be a placeholder. Please replace with your actual token from Meta Business Manager."
        
        if not self.phone_number_id:
            return False, "WhatsApp Phone Number ID is not configured. Please set WHATSAPP_PHONE_NUMBER_ID in your environment variables."
        
        # Check if phone number ID is still a placeholder
        if self.phone_number_id in ['your_phone_number_id_here', '']:
            return False, "WhatsApp Phone Number ID is not configured. Please set WHATSAPP_PHONE_NUMBER_ID in your .env file with your actual phone number ID from Meta Business Manager."
        
        if not self.base_url:
            return False, "WhatsApp API URL is not configured."
        return True, None

    def _validate_token_with_meta(self) -> Tuple[bool, Optional[str]]:
        """Verify the access token with Meta's API (lightweight GET). Returns (ok, error_message)."""
        if not self.access_token or not self.phone_number_id:
            return True, None  # Let _validate_config handle missing config
        try:
            # Use Graph API to fetch phone number info; invalid token returns 400/401 with error
            url = f"{self.base_url}/{self.phone_number_id}"
            params = {"access_token": self.access_token}
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            if resp.status_code >= 400:
                msg = self._parse_error_response(data, resp.status_code)
                return False, msg
            return True, None
        except requests.exceptions.RequestException as e:
            logger.warning(
                "WhatsApp credential validation request failed: %s", type(e).__name__
            )
            return True, None  # Don't fail status on network errors
        except Exception as e:
            logger.warning(
                "WhatsApp credential validation error: %s", type(e).__name__
            )
            return True, None

    def get_connection_status(self) -> Tuple[bool, Optional[str]]:
        """Validate config and optionally token. Returns (configured_ok, error_message)."""
        is_valid, error_msg = self._validate_config()
        if not is_valid:
            return False, error_msg
        token_ok, token_error = self._validate_token_with_meta()
        if not token_ok:
            return False, token_error
        return True, None

    def get_meta_groups_configuration(self) -> Dict[str, Any]:
        """Read Meta WhatsApp Groups API settings from app config (no network calls)."""
        raw = (current_app.config.get("WHATSAPP_GROUPS_DEFAULT_JOIN_APPROVAL") or "auto_approve").strip()
        join_mode = raw if raw in ("auto_approve", "approval_required") else "auto_approve"
        return {
            "enabled": bool(current_app.config.get("WHATSAPP_GROUPS_ENABLED")),
            "default_join_approval_mode": join_mode,
            "api_url": current_app.config.get("WHATSAPP_API_URL"),
            "phone_number_id_configured": bool(self.phone_number_id),
            "access_token_configured": bool(self.access_token),
            "webhook_url": current_app.config.get("WHATSAPP_WEBHOOK_URL"),
            "webhook_fields_recommended": [
                "group_lifecycle_update",
                "group_participants_update",
                "group_settings_update",
                "group_status_update",
            ],
            "docs": {
                "group_management": "https://developers.facebook.com/docs/whatsapp/cloud-api/groups/reference/",
                "group_messaging": "https://developers.facebook.com/documentation/business-messaging/whatsapp/groups/groups-messaging",
                "group_webhooks": "https://developers.facebook.com/documentation/business-messaging/whatsapp/groups/webhooks",
            },
        }

    def _make_graph_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """GET Graph API. path is relative to base_url (e.g. '{phone_number_id}/groups')."""
        if not self.access_token:
            return {"success": False, "error": "Access token not configured", "status_code": None}
        path = path.lstrip("/")
        url = f"{self.base_url.rstrip('/')}/{path}"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        try:
            response = requests.get(url, headers=headers, params=params or {}, timeout=30)
            try:
                response_data = response.json()
            except json.JSONDecodeError:
                response_data = {"raw_response": response.text}
            if response.status_code >= 400:
                error_message = self._parse_error_response(response_data, response.status_code)
                return {
                    "success": False,
                    "error": error_message,
                    "status_code": response.status_code,
                    "error_details": response_data,
                }
            return {"success": True, "data": response_data, "status_code": response.status_code}
        except requests.exceptions.RequestException as e:
            logger.error(f"Graph GET failed: {e}", exc_info=True)
            return {"success": False, "error": str(e), "status_code": None}

    def verify_meta_groups_api(self, limit: int = 25) -> Dict[str, Any]:
        """
        Call Meta GET /<PHONE_NUMBER_ID>/groups to verify the access token can use the Groups API.
        Requires a Graph API version that supports WhatsApp Groups and appropriate permissions on the token.
        """
        is_valid, error_msg = self._validate_config()
        if not is_valid:
            return {"success": False, "error": error_msg, "groups": [], "group_count": 0}
        lim = max(1, min(int(limit) if limit else 25, 1024))
        result = self._make_graph_get(f"{self.phone_number_id}/groups", params={"limit": lim})
        if not result.get("success"):
            return {
                "success": False,
                "error": result.get("error", "Unknown error"),
                "status_code": result.get("status_code"),
                "groups": [],
                "group_count": 0,
            }
        payload = result.get("data") or {}
        groups: List[Dict[str, Any]] = []
        data_block = payload.get("data")
        if isinstance(data_block, dict):
            raw_list = data_block.get("groups") or []
            if isinstance(raw_list, list):
                for g in raw_list:
                    if isinstance(g, dict):
                        groups.append(
                            {
                                "id": g.get("id"),
                                "subject": g.get("subject"),
                                "created_at": g.get("created_at"),
                            }
                        )
        return {
            "success": True,
            "groups": groups,
            "group_count": len(groups),
            "paging": payload.get("paging"),
        }

    def send_text_message(self, phone_number: str, message: str, client_id: int = None, retry_count: int = 0) -> Dict[str, Any]:
        """Send a simple text message with optional retry"""
        try:
            # Validate configuration
            is_valid, error_msg = self._validate_config()
            if not is_valid:
                return {"success": False, "error": error_msg}
            
            # Validate phone number
            if not phone_number:
                return {"success": False, "error": "Phone number is required"}
            
            # Validate message
            if not message or not message.strip():
                return {"success": False, "error": "Message content is required"}
            
            # Format phone number (remove + and ensure proper format)
            formatted_phone = self._format_phone_number(phone_number)
            
            if not formatted_phone or len(formatted_phone) < 10:
                return {"success": False, "error": f"Invalid phone number format: {phone_number}"}
            
            payload = {
                "messaging_product": "whatsapp",
                "to": formatted_phone,
                "type": "text",
                "text": {
                    "body": message
                }
            }
            
            response = self._make_api_call(payload)
            
            # Store message in database
            if response.get('success'):
                try:
                    response_data = response.get('data', {})
                    messages = response_data.get('messages', [])
                    if messages and len(messages) > 0:
                        message_id = messages[0].get('id')
                        if message_id:
                            self._store_message(
                                message_id=message_id,
                                phone_number=formatted_phone,
                                message_type=MessageType.TEXT,
                                content=message,
                                client_id=client_id,
                                status=MessageStatus.SENT
                            )
                except Exception as store_error:
                    logger.warning(f"Failed to store message in database: {str(store_error)}")
                    # Don't fail the request if storage fails
            else:
                # Check if error is retryable
                status_code = response.get('status_code')
                if self._is_retryable_error(status_code, response.get('error', '')) and retry_count < 3:
                    logger.info(f"Retrying message send (attempt {retry_count + 1}/3)")
                    import time
                    time.sleep(2 ** retry_count)  # Exponential backoff: 1s, 2s, 4s
                    return self.send_text_message(phone_number, message, client_id, retry_count + 1)
                else:
                    # Store failed message
                    try:
                        self._store_message(
                            message_id=f"failed_{datetime.utcnow().timestamp()}",
                            phone_number=formatted_phone,
                            message_type=MessageType.TEXT,
                            content=message,
                            client_id=client_id,
                            status=MessageStatus.FAILED,
                            error_message=response.get('error', 'Unknown error')
                        )
                    except Exception as store_error:
                        logger.warning(f"Failed to store failed message: {str(store_error)}")
            
            return response
            
        except Exception as e:
            logger.error(f"Error sending text message: {str(e)}", exc_info=True)
            return {"success": False, "error": f"Failed to send message: {str(e)}"}
    
    def _is_retryable_error(self, status_code: Optional[int], error_message: str) -> bool:
        """Check if an error is retryable"""
        if not status_code:
            return False
        
        # Retry on server errors and rate limits
        retryable_status_codes = [429, 500, 502, 503, 504]
        if status_code in retryable_status_codes:
            return True
        
        # Don't retry on client errors (4xx except 429)
        if 400 <= status_code < 500 and status_code != 429:
            return False
        
        # Check error message for specific retryable errors
        retryable_errors = ['timeout', 'connection', 'rate limit', 'temporarily unavailable']
        error_lower = error_message.lower()
        if any(retryable in error_lower for retryable in retryable_errors):
            return True
        
        return False
    
    def send_template_message(self, phone_number: str, template_name: str, 
                            template_params: List[str] = None, client_id: int = None,
                            language_code: Optional[str] = None) -> Dict[str, Any]:
        """Send a template message"""
        try:
            # Validate configuration
            is_valid, error_msg = self._validate_config()
            if not is_valid:
                return {"success": False, "error": error_msg}

            formatted_phone = self._format_phone_number(phone_number)
            resolved_language_code = (
                language_code
                or current_app.config.get('WHATSAPP_TEMPLATE_LANGUAGE', 'en_US')
            ).strip()
            
            payload = {
                "messaging_product": "whatsapp",
                "to": formatted_phone,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {
                        "code": resolved_language_code
                    }
                }
            }
            
            # Add parameters if provided
            if template_params:
                payload["template"]["components"] = [{
                    "type": "body",
                    "parameters": [{"type": "text", "text": param} for param in template_params]
                }]
            
            response = self._make_api_call(payload)
            
            # Store message in database
            if response.get('success'):
                try:
                    response_data = response.get('data', {})
                    messages = response_data.get('messages', [])
                    if messages and len(messages) > 0:
                        message_id = messages[0].get('id')
                        if message_id:
                            self._store_message(
                                message_id=message_id,
                                phone_number=formatted_phone,
                                message_type=MessageType.TEMPLATE,
                                template_name=template_name,
                                template_params=template_params,
                                client_id=client_id,
                                status=MessageStatus.SENT
                            )
                except Exception as store_error:
                    logger.warning(f"Failed to store template message in database: {str(store_error)}")
            
            return response
            
        except Exception as e:
            logger.error(f"Error sending template message: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def send_portfolio_alert(self, client_id: int, alert_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Send portfolio alert to client"""
        try:
            # Get client's WhatsApp number
            from models import Client
            client = Client.query.get(client_id)
            if not client or not client.whatsapp_number:
                return {"success": False, "error": "Client WhatsApp number not found"}
            
            # Generate alert message based on type
            message = self._generate_portfolio_alert_message(alert_type, data)
            
            return self.send_text_message(
                phone_number=client.whatsapp_number,
                message=message,
                client_id=client_id
            )
            
        except Exception as e:
            logger.error(f"Error sending portfolio alert: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def send_transaction_confirmation(self, client_id: int, transaction_data: Dict[str, Any]) -> Dict[str, Any]:
        """Send transaction confirmation to client"""
        try:
            from models import Client
            client = Client.query.get(client_id)
            if not client or not client.whatsapp_number:
                return {"success": False, "error": "Client WhatsApp number not found"}
            
            message = self._generate_transaction_confirmation_message(transaction_data)
            
            return self.send_text_message(
                phone_number=client.whatsapp_number,
                message=message,
                client_id=client_id
            )
            
        except Exception as e:
            logger.error(f"Error sending transaction confirmation: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def send_performance_update(self, client_id: int, performance_data: Dict[str, Any]) -> Dict[str, Any]:
        """Send performance update to client"""
        try:
            from models import Client
            client = Client.query.get(client_id)
            if not client or not client.whatsapp_number:
                return {"success": False, "error": "Client WhatsApp number not found"}
            
            message = self._generate_performance_update_message(performance_data)
            
            return self.send_text_message(
                phone_number=client.whatsapp_number,
                message=message,
                client_id=client_id
            )
            
        except Exception as e:
            logger.error(f"Error sending performance update: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def handle_webhook(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle incoming webhook from WhatsApp"""
        try:
            # Log webhook data
            from models.whatsapp_models import WhatsAppWebhookLog
            webhook_log = WhatsAppWebhookLog(
                event_type=webhook_data.get('entry', [{}])[0].get('changes', [{}])[0].get('field', 'unknown'),
                webhook_data=webhook_data
            )
            db.session.add(webhook_log)
            db.session.commit()
            
            # Process different types of webhooks
            from services.whatsapp_group_service import WhatsAppGroupService, GROUP_WEBHOOK_FIELDS
            group_svc = WhatsAppGroupService()
            entries = webhook_data.get('entry', [])
            for entry in entries:
                changes = entry.get('changes', [])
                for change in changes:
                    field = change.get('field')
                    value = change.get('value', {}) or {}
                    if field == 'messages':
                        group_svc.process_webhook_change(field, value)
                        self._process_message_webhook(value)
                        if value.get('statuses'):
                            self._process_status_webhook(value)
                    elif field in GROUP_WEBHOOK_FIELDS:
                        group_svc.process_webhook_change(field, value)
                    elif field == 'message_status':
                        self._process_status_webhook(value)
            
            return {"success": True, "message": "Webhook processed successfully"}
            
        except Exception as e:
            logger.error(f"Error handling webhook: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def _make_api_call(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Make API call to WhatsApp"""
        try:
            url = f"{self.base_url}/{self.phone_number_id}/messages"
            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json'
            }
            
            logger.debug(f"Making WhatsApp API call to: {url}")
            logger.debug(f"Payload: {json.dumps(payload, indent=2)}")
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            
            # Try to parse response
            try:
                response_data = response.json()
            except json.JSONDecodeError:
                response_data = {"raw_response": response.text}
            
            # Check for errors in response
            if response.status_code >= 400:
                error_message = self._parse_error_response(response_data, response.status_code)
                logger.error(f"WhatsApp API error: {error_message}")
                # Don't expose raw Meta JSON for token errors - we show a clear message instead
                is_token_error = (
                    response.status_code in (400, 401)
                    and isinstance(response_data.get('error'), dict)
                    and response_data['error'].get('code') == 190
                )
                return {
                    "success": False,
                    "error": error_message,
                    "status_code": response.status_code,
                    "error_details": None if is_token_error else response_data
                }
            
            response.raise_for_status()
            
            return {
                "success": True,
                "data": response_data
            }
            
        except requests.exceptions.Timeout:
            error_msg = "Request to WhatsApp API timed out. Please try again later."
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "status_code": 408
            }
        except requests.exceptions.ConnectionError as e:
            error_msg = f"Failed to connect to WhatsApp API: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "status_code": None
            }
        except requests.exceptions.RequestException as e:
            error_msg = f"WhatsApp API request failed: {str(e)}"
            status_code = getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
            
            # Try to get error details from response
            error_details = {}
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_details = e.response.json()
                    error_msg = self._parse_error_response(error_details, status_code)
                except Exception:
                    error_details = {"raw_response": e.response.text if hasattr(e.response, 'text') else str(e)}
            is_token_error = (
                isinstance(error_details.get('error'), dict)
                and error_details['error'].get('code') == 190
            )
            logger.error(f"API call failed: {error_msg}", exc_info=True)
            return {
                "success": False,
                "error": error_msg,
                "status_code": status_code,
                "error_details": None if is_token_error else error_details
            }
        except Exception as e:
            error_msg = f"Unexpected error during API call: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "success": False,
                "error": error_msg,
                "status_code": None
            }
    
    def _parse_error_response(self, error_data: Dict[str, Any], status_code: int = None) -> str:
        """Parse WhatsApp API error response to get user-friendly error message"""
        try:
            TOKEN_ERROR_MSG = (
                "Invalid or expired access token. Generate a new token from Meta Business Manager "
                "(https://business.facebook.com/) and set WHATSAPP_ACCESS_TOKEN in your .env file. "
                "See WHATSAPP_SETUP_GUIDE.md for steps."
            )
            # WhatsApp API error structure
            if isinstance(error_data, dict):
                error = error_data.get('error', {})
                if isinstance(error, dict):
                    message = (error.get('message') or '')
                    message_lower = str(message).lower()
                    error_type = error.get('type', '')
                    error_code = error.get('code', '')
                    error_subcode = error.get('error_subcode', '')
                    # OAuth / access token errors (check first so we return friendly message)
                    if (
                        error_code == 190
                        or 'oauth' in str(error_type).lower()
                        or 'access token' in message_lower
                        or 'validating access token' in message_lower
                        or 'error validating' in message_lower
                    ):
                        return TOKEN_ERROR_MSG
                    # Build detailed error message for other errors
                    error_parts = []
                    if message:
                        error_parts.append(message)
                    if error_type:
                        error_parts.append(f"Type: {error_type}")
                    if error_code:
                        error_parts.append(f"Code: {error_code}")
                    if error_subcode:
                        error_parts.append(f"Subcode: {error_subcode}")
                    if error_parts:
                        return " | ".join(error_parts)
                # Fallback to message field
                if 'message' in error_data:
                    msg = str(error_data['message']).lower()
                    if 'access token' in msg or 'validating' in msg:
                        return TOKEN_ERROR_MSG
                    return str(error_data['message'])
            # Check for specific OAuth errors (nested error structure)
            if isinstance(error_data, dict):
                error_obj = error_data.get('error', {})
                if isinstance(error_obj, dict):
                    error_code = error_obj.get('code')
                    error_type = error_obj.get('type', '')
                    msg = str(error_obj.get('message', '')).lower()
                    if (
                        error_code == 190
                        or 'OAuth' in error_type
                        or 'access token' in msg
                        or 'validating access token' in msg
                    ):
                        return TOKEN_ERROR_MSG
            
            # Generic error based on status code
            if status_code:
                status_messages = {
                    400: "Bad request - Invalid parameters or payload",
                    401: "Unauthorized - Invalid or expired access token. Please verify your WHATSAPP_ACCESS_TOKEN in the .env file.",
                    403: "Forbidden - Access denied. Check your permissions and token scope.",
                    404: "Not found - Phone number ID or endpoint not found. Verify your WHATSAPP_PHONE_NUMBER_ID.",
                    429: "Rate limit exceeded - Too many requests. Please try again later.",
                    500: "WhatsApp API server error - Please try again later",
                    503: "Service unavailable - WhatsApp API is temporarily unavailable"
                }
                return status_messages.get(status_code, f"API error (Status: {status_code})")
            
            return "Unknown error occurred"
            
        except Exception as e:
            logger.error(f"Error parsing error response: {str(e)}")
            return f"Error: {str(error_data)}"
    
    def _format_phone_number(self, phone_number: str) -> str:
        """Format phone number for WhatsApp API (E.164 format without +)"""
        if not phone_number:
            return ""
        
        # Remove all non-digit characters except +
        cleaned = phone_number.strip()
        
        # Remove + if present
        if cleaned.startswith('+'):
            cleaned = cleaned[1:]
        
        # Remove all non-digit characters
        digits = ''.join(filter(str.isdigit, cleaned))
        
        if not digits:
            return ""
        
        # Handle different formats
        # If 10 digits, assume India (+91)
        if len(digits) == 10:
            return '91' + digits
        
        # If 11 digits starting with 0, remove leading 0 and add country code
        if len(digits) == 11 and digits.startswith('0'):
            return '91' + digits[1:]
        
        # If already has country code (12+ digits), return as is
        if len(digits) >= 11:
            return digits
        
        # If less than 10 digits, return as is (might be invalid, but let API handle it)
        return digits
    
    def _store_message(self, message_id: str, phone_number: str, message_type: MessageType,
                      content: str = None, template_name: str = None, template_params: List = None,
                      client_id: int = None, status: MessageStatus = MessageStatus.PENDING,
                      error_message: str = None, group_id: str = None, direction: str = None,
                      sender_wa_id: str = None, user_id: int = None):
        """Store message in database"""
        try:
            message = WhatsAppMessage(
                message_id=message_id,
                phone_number=phone_number,
                message_type=message_type,
                content=content,
                template_name=template_name,
                template_params=template_params,
                client_id=client_id,
                user_id=user_id,
                group_id=group_id,
                direction=direction,
                sender_wa_id=sender_wa_id,
                status=status,
                error_message=error_message
            )
            
            db.session.add(message)
            db.session.commit()
            
        except Exception as e:
            logger.error(f"Error storing message: {str(e)}")
            db.session.rollback()
    
    def _generate_portfolio_alert_message(self, alert_type: str, data: Dict[str, Any]) -> str:
        """Generate portfolio alert message"""
        if alert_type == "price_alert":
            return f"""📈 *Price Alert*
            
{data.get('stock_symbol', 'Stock')} has reached your target price of ₹{data.get('target_price', 'N/A')}

Current Price: ₹{data.get('current_price', 'N/A')}
Change: {data.get('change_percent', 'N/A')}%

Portfolio Value: ₹{data.get('portfolio_value', 'N/A')}

View full portfolio: {current_app.config.get('BASE_URL', 'https://your-app.com')}/portfolio/{data.get('client_id', '')}"""
        
        elif alert_type == "performance_update":
            return f"""📊 *Portfolio Performance Update*
            
Your portfolio performance for {data.get('period', 'this period')}:

Total Value: ₹{data.get('total_value', 'N/A')}
Gain/Loss: ₹{data.get('gain_loss', 'N/A')} ({data.get('gain_loss_percent', 'N/A')}%)

Top Performers:
{data.get('top_performers', 'N/A')}

View detailed report: {current_app.config.get('BASE_URL', 'https://your-app.com')}/portfolio/{data.get('client_id', '')}"""
        
        return f"Portfolio Alert: {alert_type}"
    
    def _generate_transaction_confirmation_message(self, transaction_data: Dict[str, Any]) -> str:
        """Generate transaction confirmation message"""
        return f"""✅ *Transaction Confirmed*

Type: {transaction_data.get('type', 'N/A')}
Stock: {transaction_data.get('stock_symbol', 'N/A')}
Quantity: {transaction_data.get('quantity', 'N/A')}
Price: ₹{transaction_data.get('price', 'N/A')}
Total Amount: ₹{transaction_data.get('total_amount', 'N/A')}
Date: {transaction_data.get('date', 'N/A')}

Transaction ID: {transaction_data.get('transaction_id', 'N/A')}

View transaction details: {current_app.config.get('BASE_URL', 'https://your-app.com')}/transactions/{transaction_data.get('transaction_id', '')}"""
    
    def _generate_performance_update_message(self, performance_data: Dict[str, Any]) -> str:
        """Generate performance update message"""
        return f"""📈 *Performance Update*

Portfolio Value: ₹{performance_data.get('total_value', 'N/A')}
Today's Change: ₹{performance_data.get('daily_change', 'N/A')} ({performance_data.get('daily_change_percent', 'N/A')}%)

Top Movers:
{performance_data.get('top_movers', 'N/A')}

View full report: {current_app.config.get('BASE_URL', 'https://your-app.com')}/portfolio/{performance_data.get('client_id', '')}"""
    
    def _process_message_webhook(self, value: Dict[str, Any]):
        """Process incoming 1:1 message webhook (skips group messages)."""
        try:
            messages = value.get('messages', [])
            for message in messages:
                if message.get('group_id') or (message.get('context') or {}).get('group_id'):
                    continue
                # Handle incoming messages from clients
                phone_number = message.get('from', '')
                message_text = message.get('text', {}).get('body', '')
                
                # Store as client communication
                self._store_client_communication(
                    phone_number=phone_number,
                    content=message_text,
                    direction='inbound'
                )
                
        except Exception as e:
            logger.error(f"Error processing message webhook: {str(e)}")
    
    def _process_status_webhook(self, value: Dict[str, Any]):
        """Process message status webhook"""
        try:
            statuses = value.get('statuses', [])
            for status in statuses:
                message_id = status.get('id', '')
                status_value = status.get('status', '')
                
                # Update message status in database
                message = WhatsAppMessage.query.filter_by(message_id=message_id).first()
                if message:
                    if status_value == 'delivered':
                        message.status = MessageStatus.DELIVERED
                        message.delivered_at = datetime.utcnow()
                    elif status_value == 'read':
                        message.status = MessageStatus.READ
                        message.read_at = datetime.utcnow()
                    elif status_value == 'failed':
                        message.status = MessageStatus.FAILED
                        message.error_message = status.get('errors', [{}])[0].get('title', 'Unknown error')
                    
                    db.session.commit()
                    
        except Exception as e:
            logger.error(f"Error processing status webhook: {str(e)}")
    
    def _store_client_communication(self, phone_number: str, content: str, direction: str):
        """Store client communication"""
        try:
            from models.whatsapp_models import ClientCommunication
            from models import Client
            
            # Find client by phone number
            client = Client.query.filter_by(whatsapp_number=phone_number).first()
            
            communication = ClientCommunication(
                client_id=client.id if client else None,
                communication_type='whatsapp',
                direction=direction,
                content=content,
                phone_number=phone_number
            )
            
            db.session.add(communication)
            db.session.commit()
            
        except Exception as e:
            logger.error(f"Error storing client communication: {str(e)}")
            db.session.rollback()
