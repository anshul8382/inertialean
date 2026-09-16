import pymysql
from config import ProductionConfig
import re

def get_db_connection():
    uri = ProductionConfig.SQLALCHEMY_DATABASE_URI
    match = re.match(r'mysql\+pymysql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)', uri)
    if not match:
        raise ValueError("Invalid database URI format")
    
    user, password, host, port, db = match.groups()
    return pymysql.connect(
        host=host,
        user=user,
        password=password,
        database=db,
        port=int(port)
    )

def cleanup_migrations():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Drop the alembic_version table
            cursor.execute("DROP TABLE IF EXISTS alembic_version")
            conn.commit()
            print("Successfully cleaned up migration history")
    except Exception as e:
        print(f"Error cleaning up migrations: {str(e)}")
    finally:
        conn.close()

if __name__ == "__main__":
    cleanup_migrations() 