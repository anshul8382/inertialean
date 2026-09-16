# Domain Deployment Guide for inertiainvest.in

This guide will help you deploy your Flask application to the `inertiainvest.in` domain.

## Prerequisites

1. **Domain Registration**: Ensure `inertiainvest.in` is registered and DNS is configured
2. **Server Access**: Root or sudo access to your server
3. **SSL Certificate**: Obtain SSL certificate for HTTPS (Let's Encrypt recommended)

## Quick Deployment

### Option 1: Automated Deployment (Recommended)

```bash
# Run the automated deployment script
sudo /home/inertia/app/deploy-inertiainvest.in.sh
```

### Option 2: Manual Deployment

Follow the steps below for manual configuration.

## Manual Deployment Steps

### 1. Update DNS Records

Configure your DNS to point to your server:
- **A Record**: `inertiainvest.in` → Your server IP
- **CNAME Record**: `www.inertiainvest.in` → `inertiainvest.in`

### 2. Install Required Packages

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install web server and dependencies
sudo apt install -y apache2 apache2-utils libapache2-mod-wsgi-py3
sudo apt install -y nginx certbot python3-certbot-apache
sudo apt install -y python3-pip python3-venv
```

### 3. Configure SSL Certificate

```bash
# Get Let's Encrypt certificate
sudo certbot --apache -d inertiainvest.in -d www.inertiainvest.in

# Or manually place certificates in:
# /etc/ssl/certs/inertiainvest.in.crt
# /etc/ssl/private/inertiainvest.in.key
# /etc/ssl/certs/inertiainvest.in.chain.crt
```

### 4. Configure Web Server

#### Apache Configuration

```bash
# Copy Apache configuration
sudo cp /home/inertia/app/inertiainvest.in.conf /etc/apache2/sites-available/

# Enable required modules
sudo a2enmod ssl rewrite headers wsgi

# Enable the site
sudo a2ensite inertiainvest.in.conf

# Restart Apache
sudo systemctl restart apache2
```

#### Nginx Configuration (Alternative)

```bash
# Copy Nginx configuration
sudo cp /home/inertia/app/nginx-inertiainvest.in.conf /etc/nginx/sites-available/

# Create symlink
sudo ln -sf /etc/nginx/sites-available/nginx-inertiainvest.in.conf /etc/nginx/sites-enabled/

# Test configuration
sudo nginx -t

# Restart Nginx
sudo systemctl restart nginx
```

### 5. Set Up Python Environment

```bash
cd /home/inertia/app

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

### 6. Configure Gunicorn Service

```bash
# Copy systemd service file
sudo cp /home/inertia/app/inertia-app.service /etc/systemd/system/

# Enable and start service
sudo systemctl daemon-reload
sudo systemctl enable inertia-app
sudo systemctl start inertia-app
```

### 7. Set Permissions

```bash
# Set proper ownership
sudo chown -R inertia:inertia /home/inertia/app

# Set proper permissions
sudo chmod -R 755 /home/inertia/app
```

## Configuration Files

### Flask Configuration
- **File**: `config.py`
- **Changes**: Updated for `inertiainvest.in` domain with HTTPS settings

### Apache Virtual Host
- **File**: `inertiainvest.in.conf`
- **Location**: `/etc/apache2/sites-available/`

### Nginx Configuration
- **File**: `nginx-inertiainvest.in.conf`
- **Location**: `/etc/nginx/sites-available/`

### Gunicorn Configuration
- **File**: `gunicorn-inertiainvest.in.conf.py`
- **Usage**: `gunicorn -c gunicorn-inertiainvest.in.conf.py wsgi:app`

## Security Features

### SSL/TLS Configuration
- **HTTPS Redirect**: All HTTP traffic redirected to HTTPS
- **Security Headers**: HSTS, X-Frame-Options, X-Content-Type-Options
- **Modern SSL**: TLS 1.2+ with secure ciphers

### Session Security
- **Secure Cookies**: Only transmitted over HTTPS
- **Domain Restriction**: Cookies limited to `.inertiainvest.in`
- **CSRF Protection**: Enabled with SSL strict mode

## Monitoring and Maintenance

### Log Files
- **Apache**: `/var/log/apache2/inertiainvest.in_*.log`
- **Nginx**: `/var/log/nginx/inertiainvest.in_*.log`
- **Gunicorn**: `/home/inertia/app/logs/gunicorn_*.log`
- **Application**: `/home/inertia/app/logs/`

### Service Management
```bash
# Check service status
sudo systemctl status inertia-app

# Restart application
sudo systemctl restart inertia-app

# View logs
sudo journalctl -u inertia-app -f
```

### SSL Certificate Renewal
```bash
# Test renewal
sudo certbot renew --dry-run

# Set up auto-renewal (usually done automatically)
sudo crontab -e
# Add: 0 12 * * * /usr/bin/certbot renew --quiet
```

## Testing

### 1. DNS Resolution
```bash
nslookup inertiainvest.in
dig inertiainvest.in
```

### 2. SSL Certificate
```bash
openssl s_client -connect inertiainvest.in:443 -servername inertiainvest.in
```

### 3. Application Access
- **HTTP**: `http://inertiainvest.in` (should redirect to HTTPS)
- **HTTPS**: `https://inertiainvest.in`
- **WWW**: `https://www.inertiainvest.in`

## Troubleshooting

### Common Issues

1. **502 Bad Gateway**
   - Check if Gunicorn is running: `sudo systemctl status inertia-app`
   - Check Gunicorn logs: `tail -f /home/inertia/app/logs/gunicorn_error.log`

2. **SSL Certificate Issues**
   - Verify certificate files exist and are readable
   - Check certificate validity: `openssl x509 -in /etc/ssl/certs/inertiainvest.in.crt -text -noout`

3. **Permission Issues**
   - Ensure proper ownership: `sudo chown -R inertia:inertia /home/inertia/app`
   - Check file permissions: `ls -la /home/inertia/app/`

4. **Database Connection**
   - Verify database is running: `sudo systemctl status mysql`
   - Check database credentials in `config.py`

### Log Analysis
```bash
# Apache error logs
sudo tail -f /var/log/apache2/inertiainvest.in_error.log

# Nginx error logs
sudo tail -f /var/log/nginx/inertiainvest.in_error.log

# Application logs
tail -f /home/inertia/app/logs/gunicorn_error.log
```

## Performance Optimization

### 1. Enable Caching
- Static files cached for 1 year
- Database query optimization
- Redis caching (optional)

### 2. Load Balancing
- Multiple Gunicorn workers
- Nginx upstream configuration
- Database connection pooling

### 3. Monitoring
- Set up monitoring tools (Prometheus, Grafana)
- Log aggregation (ELK stack)
- Uptime monitoring

## Backup and Recovery

### 1. Database Backup
```bash
# Create backup
mysqldump -u inertia_admin -p inertia_app2025 > backup_$(date +%Y%m%d).sql

# Restore backup
mysql -u inertia_admin -p inertia_app2025 < backup_20250101.sql
```

### 2. Application Backup
```bash
# Create application backup
tar -czf app_backup_$(date +%Y%m%d).tar.gz /home/inertia/app
```

## Support

For issues or questions:
1. Check logs first
2. Verify configuration files
3. Test individual components
4. Contact system administrator

---

**Note**: This guide assumes a standard Ubuntu/Debian server setup. Adjust commands as needed for your specific environment.
