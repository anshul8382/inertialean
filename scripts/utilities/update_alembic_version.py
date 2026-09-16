from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from config import Config
from sqlalchemy import text

app = Flask(__name__)
app.config.from_object(Config)
db = SQLAlchemy(app)
migrate = Migrate(app, db)

def update_alembic_version():
    with app.app_context():
        # Delete all existing versions
        db.session.execute(text('DELETE FROM alembic_version'))
        
        # Insert the new version
        db.session.execute(
            text('INSERT INTO alembic_version (version_num) VALUES (:version)'),
            {'version': 'make_call_log_client_id_nullable'}
        )
        
        db.session.commit()
        print("Alembic version updated successfully!")

if __name__ == '__main__':
    update_alembic_version() 