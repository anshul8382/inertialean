from app import app, db
from models import User

def create_admin():
    with app.app_context():
        admin = User(username='admin', email='admin@example.com')
        admin.set_password('admin')
        db.session.add(admin)
        db.session.commit()
        print('Admin user created successfully!')

if __name__ == '__main__':
    create_admin() 