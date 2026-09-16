"""
Remove client status column - Reverting client status feature

This migration removes the status column from the client table that was added
as part of the inactive client filtering feature.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'remove_client_status_column'
down_revision = None  # Set this to the latest migration if needed
branch_labels = None
depends_on = None

def upgrade():
    """Remove status column from client table"""
    try:
        # Check if column exists before removing
        connection = op.get_bind()
        result = connection.execute(sa.text("SHOW COLUMNS FROM client LIKE 'status'"))
        if result.fetchone():
            op.drop_column('client', 'status')
            print("Successfully removed status column from client table")
        else:
            print("Status column does not exist - skipping removal")
    except Exception as e:
        print(f"Error removing status column: {e}")
        # Don't fail if column doesn't exist
        pass

def downgrade():
    """Add status column back to client table (for rollback)"""
    try:
        # Check if column doesn't exist before adding
        connection = op.get_bind()
        result = connection.execute(sa.text("SHOW COLUMNS FROM client LIKE 'status'"))
        if not result.fetchone():
            op.add_column('client', sa.Column('status', sa.String(20), server_default='active'))
            print("Successfully added status column back to client table")
        else:
            print("Status column already exists - skipping addition")
    except Exception as e:
        print(f"Error adding status column: {e}")


