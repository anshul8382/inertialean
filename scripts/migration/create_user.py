from app import create_app
from extensions import db
from models import User

app = create_app()
with app.app_context():
    # Check if user already exists
    user = User.query.filter_by(email='admin@example.com').first()
    if not user:
        user = User(email='admin@example.com', username='admin')
        user.set_password('admin')
        db.session.add(user)
        db.session.commit()
        print('User created successfully!')
    else:
        print('User already exists!') 