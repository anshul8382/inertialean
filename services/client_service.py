"""
Client Service - Simple service for client operations with direct DB calls

This service provides a clean interface for client operations without
the complexity of HTTP calls or import issues.
"""

from models import Client
from extensions import db
from sqlalchemy import func, or_
import logging

logger = logging.getLogger(__name__)

class ClientService:
    """Simple service for client operations with direct DB calls"""
    
    @staticmethod
    def get_client(client_id):
        """
        Get client by ID
        
        Args:
            client_id: Client ID
            
        Returns:
            Client object or None if not found
        """
        try:
            return Client.query.get(client_id)
        except Exception as e:
            logger.error(f"Error getting client {client_id}: {str(e)}")
            return None
    
    @staticmethod
    def get_clients(filters=None):
        """
        Get clients with optional filtering, scoped to the current user.

        Admin/manager see all clients. Advisors (and other non-manager roles)
        only see clients where advisor_id == current_user.id. Outside a request
        context (scripts/jobs), no automatic scoping is applied.
        
        Args:
            filters: Optional filter dictionary
            
        Returns:
            List of Client objects
        """
        try:
            from access_control import scope_clients_query, user_can_view_all_clients

            query = scope_clients_query(Client.query)

            if filters:
                if 'search' in filters and filters['search']:
                    search = filters['search'].strip()
                    if search:
                        # Case-insensitive search using func.lower for cross-database compatibility
                        search_lower = search.lower()
                        # Build search conditions - handle None emails properly
                        conditions = [
                            func.lower(Client.name).contains(search_lower)
                        ]
                        # Only add email search if email column exists and is not None
                        if hasattr(Client, 'email'):
                            conditions.append(
                                func.lower(Client.email).contains(search_lower)
                            )
                        query = query.filter(or_(*conditions))
                if 'risk_profile' in filters:
                    query = query.filter(Client.risk_profile == filters['risk_profile'])
                if 'advisor_id' in filters:
                    # Advisors cannot widen scope via advisor_id filter
                    if user_can_view_all_clients():
                        query = query.filter(Client.advisor_id == filters['advisor_id'])
                if filters.get('active_only') or filters.get('is_active') is True:
                    query = query.filter(Client.is_active == True)
                elif filters.get('is_active') is False:
                    query = query.filter(Client.is_active == False)
            
            return query.order_by(Client.name).all()
        except Exception as e:
            logger.error(f"Error getting clients: {str(e)}")
            return []
    
    @staticmethod
    def create_client(client_data):
        """
        Create new client
        
        Args:
            client_data: Dictionary with client data
            
        Returns:
            Created Client object
        """
        try:
            client = Client(**client_data)
            db.session.add(client)
            db.session.commit()
            return client
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating client: {str(e)}")
            raise
    
    @staticmethod
    def update_client(client_id, client_data):
        """
        Update existing client
        
        Args:
            client_id: Client ID
            client_data: Dictionary with client data
            
        Returns:
            Updated Client object or None if not found
        """
        try:
            client = Client.query.get(client_id)
            if not client:
                return None
                
            for key, value in client_data.items():
                if hasattr(client, key):
                    setattr(client, key, value)
            
            db.session.commit()
            return client
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating client {client_id}: {str(e)}")
            raise
    
    @staticmethod
    def delete_client(client_id):
        """
        Delete client
        
        Args:
            client_id: Client ID
            
        Returns:
            True if deleted, False if not found
        """
        try:
            client = Client.query.get(client_id)
            if not client:
                return False
                
            db.session.delete(client)
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error deleting client {client_id}: {str(e)}")
            raise
    
    @staticmethod
    def get_client_count():
        """
        Get total number of clients
        
        Returns:
            Integer count of clients
        """
        try:
            return Client.query.count()
        except Exception as e:
            logger.error(f"Error getting client count: {str(e)}")
            return 0

