-- FinVantage Database Setup
-- Run as MySQL root or user with CREATE DATABASE privilege:
--   mysql -u root -p < migrations/create_finvantage_db.sql
--
-- Then run: python migrations/create_finvantage_database.py

CREATE DATABASE IF NOT EXISTS finvantage
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- Grant full access to inertia_admin (adjust user@host if needed)
GRANT ALL PRIVILEGES ON finvantage.* TO 'inertia_admin'@'localhost';
GRANT ALL PRIVILEGES ON finvantage.* TO 'inertia_admin'@'127.0.0.1';
FLUSH PRIVILEGES;
