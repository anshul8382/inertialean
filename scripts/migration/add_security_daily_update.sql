-- Create security_daily_update table to track daily security updates
CREATE TABLE security_daily_update (
    id INT AUTO_INCREMENT PRIMARY KEY,
    update_date DATE NOT NULL UNIQUE,
    update_type VARCHAR(50) NOT NULL,
    updated_securities_count INT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by INT NOT NULL,
    FOREIGN KEY (created_by) REFERENCES user(id)
); 