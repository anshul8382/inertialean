import os
import sys
from flask import Flask
from flask_migrate import Migrate, upgrade
from extensions import db, migrate
from models import AssetClass, Security

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:root@localhost/inertia'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db.init_app(app)
migrate.init_app(app, db)

with app.app_context():
    # Run migrations
    upgrade()
    
    # Add sample asset classes if they don't exist
    asset_classes = {
        'Large Cap': 'EQUITY',
        'Mid Cap': 'EQUITY',
        'Small Cap': 'EQUITY',
        'Fixed Income': 'DEBT',
        'Gold': 'GOLD',
        'REIT': 'REAL_ESTATE',
        'International': 'EQUITY'
    }
    
    for name, type_name in asset_classes.items():
        asset_class = AssetClass.query.filter_by(name=name).first()
        if not asset_class:
            asset_class = AssetClass(
                name=name,
                type=type_name,
                description=f'{name} securities',
                created_at=db.func.now()
            )
            db.session.add(asset_class)
    
    try:
        db.session.commit()
        print("Successfully added asset classes!")
    except Exception as e:
        db.session.rollback()
        print(f"Error adding asset classes: {str(e)}") 