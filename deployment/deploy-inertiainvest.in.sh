#!/bin/bash

# Deployment script for inertiainvest.in domain
# Run this script as root or with sudo

set -e

echo "🚀 Starting deployment for inertiainvest.in domain..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    print_error "Please run this script as root or with sudo"
    exit 1
fi

# Update system packages
print_status "Updating system packages..."
apt update && apt upgrade -y

# Install required packages
print_status "Installing required packages..."
apt install -y apache2 apache2-utils libapache2-mod-wsgi-py3 python3-pip python3-venv nginx certbot python3-certbot-apache

# Create SSL certificate directory
print_status "Creating SSL certificate directory..."
mkdir -p /etc/ssl/certs
mkdir -p /etc/ssl/private

# Note: You need to obtain SSL certificates from a CA like Let's Encrypt
print_warning "IMPORTANT: You need to obtain SSL certificates for inertiainvest.in"
print_warning "Run the following command to get Let's Encrypt certificates:"
print_warning "certbot --apache -d inertiainvest.in -d www.inertiainvest.in"

# Configure Apache
print_status "Configuring Apache..."
if [ -f "/home/inertia/app/inertiainvest.in.conf" ]; then
    cp /home/inertia/app/inertiainvest.in.conf /etc/apache2/sites-available/
    a2ensite inertiainvest.in.conf
    a2enmod ssl
    a2enmod rewrite
    a2enmod headers
    a2enmod wsgi
    systemctl restart apache2
    print_status "Apache configured successfully"
else
    print_error "Apache configuration file not found!"
fi

# Configure Nginx (alternative)
print_status "Configuring Nginx..."
if [ -f "/home/inertia/app/nginx-inertiainvest.in.conf" ]; then
    cp /home/inertia/app/nginx-inertiainvest.in.conf /etc/nginx/sites-available/
    ln -sf /etc/nginx/sites-available/nginx-inertiainvest.in.conf /etc/nginx/sites-enabled/
    nginx -t
    systemctl restart nginx
    print_status "Nginx configured successfully"
else
    print_error "Nginx configuration file not found!"
fi

# Set up Python virtual environment
print_status "Setting up Python virtual environment..."
cd /home/inertia/app
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Set proper permissions
print_status "Setting proper permissions..."
chown -R inertia:inertia /home/inertia/app
chmod -R 755 /home/inertia/app
chmod +x /home/inertia/app/deploy-inertiainvest.in.sh

# Create systemd service for Gunicorn
print_status "Creating systemd service for Gunicorn..."
cat > /etc/systemd/system/inertia-app.service << EOF
[Unit]
Description=Inertia Investment App
After=network.target

[Service]
User=inertia
Group=inertia
WorkingDirectory=/home/inertia/app
Environment="PATH=/home/inertia/app/venv/bin"
ExecStart=/home/inertia/app/venv/bin/gunicorn -c gunicorn-inertiainvest.in.conf.py wsgi:app
ExecReload=/bin/kill -s HUP \$MAINPID
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# Enable and start the service
systemctl daemon-reload
systemctl enable inertia-app
systemctl start inertia-app

print_status "✅ Deployment completed successfully!"
print_status "Your app should now be accessible at https://inertiainvest.in"

# Show status
print_status "Service status:"
systemctl status inertia-app --no-pager

print_status "Apache status:"
systemctl status apache2 --no-pager

print_warning "Next steps:"
print_warning "1. Obtain SSL certificates: certbot --apache -d inertiainvest.in -d www.inertiainvest.in"
print_warning "2. Update DNS records to point inertiainvest.in to this server"
print_warning "3. Test the application at https://inertiainvest.in"
print_warning "4. Monitor logs: tail -f /home/inertia/app/logs/gunicorn_error.log"
