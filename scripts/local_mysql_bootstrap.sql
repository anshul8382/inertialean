-- Run after MySQL is installed: mysql -u root -p < scripts/local_mysql_bootstrap.sql
-- Password in .env must match: DB_PASSWORD=local_inertia_dev

CREATE DATABASE IF NOT EXISTS inertia_app2025_dev
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'inertia_admin'@'localhost' IDENTIFIED BY 'local_inertia_dev';
GRANT ALL PRIVILEGES ON inertia_app2025_dev.* TO 'inertia_admin'@'localhost';
GRANT ALL PRIVILEGES ON inertia_app2025.* TO 'inertia_admin'@'localhost';
FLUSH PRIVILEGES;
