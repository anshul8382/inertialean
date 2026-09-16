# Airflow systemd Setup – Auto-start on Boot

This guide configures Airflow to start automatically when the server boots, so agents and DAGs run without manual intervention.

## Service Files

Two service files were created in `config/`:

- **`config/airflow-scheduler.service`** – Runs the Airflow scheduler (triggers DAG runs)
- **`config/airflow-webserver.service`** – Runs the Airflow web UI on port 8081

## Installation (as root)

### 1. Stop manually started Airflow (if running)

```bash
cd /home/inertia/app
./scripts/stop_airflow.sh
```

### 2. Copy service files to systemd

```bash
sudo cp /home/inertia/app/config/airflow-scheduler.service /etc/systemd/system/
sudo cp /home/inertia/app/config/airflow-webserver.service /etc/systemd/system/
```

### 3. Reload systemd

```bash
sudo systemctl daemon-reload
```

### 4. Enable and start services

```bash
sudo systemctl enable airflow-scheduler airflow-webserver
sudo systemctl start airflow-scheduler airflow-webserver
```

### 5. Verify

```bash
sudo systemctl status airflow-scheduler
sudo systemctl status airflow-webserver
```

## Useful Commands

| Action | Command |
|--------|---------|
| Check status | `sudo systemctl status airflow-scheduler airflow-webserver` |
| View logs | `sudo journalctl -u airflow-scheduler -f` |
| Restart | `sudo systemctl restart airflow-scheduler airflow-webserver` |
| Stop | `sudo systemctl stop airflow-scheduler airflow-webserver` |
| Disable (no auto-start on boot) | `sudo systemctl disable airflow-scheduler airflow-webserver` |

## Notes

- Services run as user `inertia` (same as the main app).
- Logs are written to:
  - `journalctl` (system logs)
  - `/home/inertia/app/airflow/logs/scheduler.log`
  - `/home/inertia/app/airflow/logs/webserver.log`
- After enabling, Airflow will start on every server reboot.
