"""
Create WhatsApp integration tables
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = 'create_whatsapp_tables'
down_revision = 'add_2fa_policy_fields'
branch_labels = None
depends_on = None

def upgrade():
    """Create WhatsApp tables"""
    
    # Create WhatsAppMessage table
    op.create_table('whatsapp_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('message_id', sa.String(length=255), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('phone_number', sa.String(length=20), nullable=False),
        sa.Column('message_type', sa.Enum('TEXT', 'TEMPLATE', 'MEDIA', 'INTERACTIVE', name='messagetype'), nullable=False),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('template_name', sa.String(length=100), nullable=True),
        sa.Column('template_params', sa.JSON(), nullable=True),
        sa.Column('status', sa.Enum('PENDING', 'SENT', 'DELIVERED', 'READ', 'FAILED', name='messagestatus'), nullable=True),
        sa.Column('whatsapp_timestamp', sa.DateTime(), nullable=True),
        sa.Column('delivered_at', sa.DateTime(), nullable=True),
        sa.Column('read_at', sa.DateTime(), nullable=True),
        sa.Column('error_code', sa.String(length=50), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('message_id')
    )
    
    # Create WhatsAppNotification table
    op.create_table('whatsapp_notifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('notification_type', sa.Enum('PORTFOLIO_ALERT', 'TRANSACTION_CONFIRMATION', 'PRICE_ALERT', 'PERFORMANCE_UPDATE', 'SYSTEM_NOTIFICATION', 'CLIENT_QUERY', name='notificationtype'), nullable=False),
        sa.Column('is_enabled', sa.Boolean(), nullable=True),
        sa.Column('phone_number', sa.String(length=20), nullable=False),
        sa.Column('threshold_value', sa.String(length=50), nullable=True),
        sa.Column('threshold_type', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('last_sent', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create ClientCommunication table
    op.create_table('client_communications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('communication_type', sa.String(length=50), nullable=False),
        sa.Column('direction', sa.String(length=20), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('whatsapp_message_id', sa.String(length=255), nullable=True),
        sa.Column('phone_number', sa.String(length=20), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('priority', sa.String(length=20), nullable=True),
        sa.Column('resolution_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create WhatsAppTemplate table
    op.create_table('whatsapp_templates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('category', sa.String(length=50), nullable=False),
        sa.Column('language', sa.String(length=10), nullable=True),
        sa.Column('header_type', sa.String(length=20), nullable=True),
        sa.Column('header_content', sa.Text(), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('footer', sa.String(length=60), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('whatsapp_template_id', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )
    
    # Create WhatsAppWebhookLog table
    op.create_table('whatsapp_webhook_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('webhook_data', sa.JSON(), nullable=False),
        sa.Column('processed', sa.Boolean(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Add whatsapp_number column to clients table
    op.add_column('clients', sa.Column('whatsapp_number', sa.String(length=20), nullable=True))
    
    # Create indexes for better performance
    op.create_index('idx_whatsapp_messages_client_id', 'whatsapp_messages', ['client_id'])
    op.create_index('idx_whatsapp_messages_status', 'whatsapp_messages', ['status'])
    op.create_index('idx_whatsapp_messages_created_at', 'whatsapp_messages', ['created_at'])
    
    op.create_index('idx_whatsapp_notifications_client_id', 'whatsapp_notifications', ['client_id'])
    op.create_index('idx_whatsapp_notifications_type', 'whatsapp_notifications', ['notification_type'])
    
    op.create_index('idx_client_communications_client_id', 'client_communications', ['client_id'])
    op.create_index('idx_client_communications_status', 'client_communications', ['status'])
    op.create_index('idx_client_communications_created_at', 'client_communications', ['created_at'])
    
    op.create_index('idx_whatsapp_webhook_logs_processed', 'whatsapp_webhook_logs', ['processed'])
    op.create_index('idx_whatsapp_webhook_logs_created_at', 'whatsapp_webhook_logs', ['created_at'])

def downgrade():
    """Drop WhatsApp tables"""
    
    # Drop indexes
    op.drop_index('idx_whatsapp_webhook_logs_created_at', table_name='whatsapp_webhook_logs')
    op.drop_index('idx_whatsapp_webhook_logs_processed', table_name='whatsapp_webhook_logs')
    op.drop_index('idx_client_communications_created_at', table_name='client_communications')
    op.drop_index('idx_client_communications_status', table_name='client_communications')
    op.drop_index('idx_client_communications_client_id', table_name='client_communications')
    op.drop_index('idx_whatsapp_notifications_type', table_name='whatsapp_notifications')
    op.drop_index('idx_whatsapp_notifications_client_id', table_name='whatsapp_notifications')
    op.drop_index('idx_whatsapp_messages_created_at', table_name='whatsapp_messages')
    op.drop_index('idx_whatsapp_messages_status', table_name='whatsapp_messages')
    op.drop_index('idx_whatsapp_messages_client_id', table_name='whatsapp_messages')
    
    # Drop tables
    op.drop_table('whatsapp_webhook_logs')
    op.drop_table('whatsapp_templates')
    op.drop_table('client_communications')
    op.drop_table('whatsapp_notifications')
    op.drop_table('whatsapp_messages')
    
    # Remove whatsapp_number column from clients table
    op.drop_column('clients', 'whatsapp_number')
    
    # Drop enums
    op.execute('DROP TYPE IF EXISTS messagestatus')
    op.execute('DROP TYPE IF EXISTS messagetype')
    op.execute('DROP TYPE IF EXISTS notificationtype')
