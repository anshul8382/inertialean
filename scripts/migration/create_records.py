from app import create_app
from extensions import db
from sqlalchemy import text

app = create_app()
with app.app_context():
    print('Step 1: Creating security record...')
    try:
        db.session.execute(text('INSERT INTO security (id, name, created_by) VALUES (1, "Test Security", 1)'))
        db.session.commit()
        print('Security created successfully')
    except Exception as e:
        print(f'Error creating security: {str(e)}')

    print('\nStep 2: Creating portfolio record...')
    try:
        db.session.execute(text('INSERT INTO portfolio (id, client_id, name) VALUES (1, 1, "Test Portfolio")'))
        db.session.commit()
        print('Portfolio created successfully')
    except Exception as e:
        print(f'Error creating portfolio: {str(e)}')

    print('\nStep 3: Creating asset_class_customization record...')
    try:
        db.session.execute(text('INSERT INTO asset_class_customization (id, client_assignment_id, asset_class_id, target_allocation) VALUES (1, 21, 1, 100.00)'))
        db.session.commit()
        print('Asset class customization created successfully')
    except Exception as e:
        print(f'Error creating asset class customization: {str(e)}')

    print('\nStep 4: Creating model_allocation record...')
    try:
        db.session.execute(text('INSERT INTO model_allocation (id, customization_id, security_model_id, allocation_percentage, security_id) VALUES (1, 1, 1, 100.00, 1)'))
        db.session.commit()
        print('Model allocation created successfully')
    except Exception as e:
        print(f'Error creating model allocation: {str(e)}') 