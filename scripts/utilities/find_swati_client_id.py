from app import create_app
from models import Client

app = create_app()
with app.app_context():
    client = Client.query.filter_by(name='Swati Jain').first()
    print(f'Client ID: {client.id if client else "Not found"}') 