-- Create SQL query history table (admin tools)
-- Run manually against the MySQL database if needed.

CREATE TABLE IF NOT EXISTS sql_query_history (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  query_text LONGTEXT NOT NULL,
  query_hash VARCHAR(64) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_executed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  execute_count INT NOT NULL DEFAULT 1,
  last_success BOOLEAN NOT NULL DEFAULT TRUE,
  last_error LONGTEXT NULL,
  last_row_count INT NULL,
  last_execution_ms DOUBLE NULL,

  UNIQUE KEY uq_sql_query_history_user_hash (user_id, query_hash),
  KEY idx_sql_query_history_user (user_id),
  KEY idx_sql_query_history_last_executed (last_executed_at),

  CONSTRAINT fk_sql_query_history_user
    FOREIGN KEY (user_id) REFERENCES user(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;




