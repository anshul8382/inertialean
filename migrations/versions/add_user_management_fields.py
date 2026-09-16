"""Add user management fields

Revision ID: add_user_management_fields
Revises: add_referral_by_to_lead
Create Date: 2025-06-21 18:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_user_management_fields'
down_revision = 'add_referral_by_to_lead'
branch_labels = None
depends_on = None


def upgrade():
    # Add new fields to user table
    op.add_column('user', sa.Column('is_active', sa.Boolean(), nullable=True, default=True))
    op.add_column('user', sa.Column('is_admin', sa.Boolean(), nullable=True, default=False))
    op.add_column('user', sa.Column('email_verified', sa.Boolean(), nullable=True, default=False))
    op.add_column('user', sa.Column('password_reset_token', sa.String(255), nullable=True))
    op.add_column('user', sa.Column('password_reset_expires', sa.DateTime(), nullable=True))
    op.add_column('user', sa.Column('created_at', sa.DateTime(), nullable=True))
    op.add_column('user', sa.Column('last_login', sa.DateTime(), nullable=True))
    
    # Create unique index on password_reset_token
    op.create_index('ix_user_password_reset_token', 'user', ['password_reset_token'], unique=True)


def downgrade():
    # Remove unique index
    op.drop_index('ix_user_password_reset_token', 'user')
    
    # Remove columns
    op.drop_column('user', 'last_login')
    op.drop_column('user', 'created_at')
    op.drop_column('user', 'password_reset_expires')
    op.drop_column('user', 'password_reset_token')
    op.drop_column('user', 'email_verified')
    op.drop_column('user', 'is_admin')
    op.drop_column('user', 'is_active') 