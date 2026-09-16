-- Make email column nullable in lead table
ALTER TABLE lead MODIFY COLUMN email VARCHAR(120) NULL; 