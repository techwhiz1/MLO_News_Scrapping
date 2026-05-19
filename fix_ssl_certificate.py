#!/usr/bin/env python3
"""
SSL Certificate Fix Script for news.mininglifeserver.com

This script helps diagnose and fix SSL certificate issues with nginx.
"""

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def run_command(cmd, check=False):
    """Run a shell command and return the result."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, check=check
        )
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, "", str(e)


def check_certificate_status():
    """Check the current SSL certificate status."""
    print("=" * 60)
    print("Checking SSL Certificate Status")
    print("=" * 60)
    
    cert_path = "/etc/letsencrypt/live/news.mininglifeserver.com/fullchain.pem"
    
    if not os.path.exists(cert_path):
        print(f"❌ Certificate not found at: {cert_path}")
        print("\n📋 Action Required:")
        print("   You need to obtain a certificate using Let's Encrypt.")
        return False
    
    print(f"✅ Certificate found at: {cert_path}")
    
    # Check certificate expiration
    success, stdout, stderr = run_command(
        f"openssl x509 -in {cert_path} -noout -dates"
    )
    
    if success:
        print("\n📅 Certificate Dates:")
        print(stdout)
        
        # Check if expired
        success, stdout, stderr = run_command(
            f"openssl x509 -in {cert_path} -noout -checkend 0"
        )
        
        if not success:
            print("\n❌ Certificate has EXPIRED!")
            return False
        else:
            print("\n✅ Certificate is valid")
            
            # Check days until expiration
            success, stdout, stderr = run_command(
                f"openssl x509 -in {cert_path} -noout -enddate"
            )
            if success:
                # Extract date from output
                for line in stdout.split('\n'):
                    if 'notAfter=' in line:
                        print(f"   {line}")
    
    return True


def check_nginx_config():
    """Check nginx configuration."""
    print("\n" + "=" * 60)
    print("Checking Nginx Configuration")
    print("=" * 60)
    
    # Check if nginx is installed
    success, stdout, stderr = run_command("which nginx")
    if not success:
        print("❌ Nginx is not installed")
        return False
    
    print("✅ Nginx is installed")
    
    # Check nginx config syntax
    success, stdout, stderr = run_command("sudo nginx -t", check=False)
    if success:
        print("✅ Nginx configuration syntax is valid")
        print(stdout)
    else:
        print("❌ Nginx configuration has errors:")
        print(stderr)
        return False
    
    # Check if nginx is running
    success, stdout, stderr = run_command("systemctl is-active nginx")
    if success and "active" in stdout:
        print("✅ Nginx is running")
    else:
        print("⚠️  Nginx is not running")
    
    return True


def check_certbot():
    """Check if certbot is installed."""
    print("\n" + "=" * 60)
    print("Checking Certbot Installation")
    print("=" * 60)
    
    success, stdout, stderr = run_command("which certbot")
    if success:
        print("✅ Certbot is installed")
        print(f"   Location: {stdout.strip()}")
        return True
    else:
        print("❌ Certbot is not installed")
        print("\n📋 To install certbot, run:")
        print("   sudo apt update")
        print("   sudo apt install certbot python3-certbot-nginx")
        return False


def get_certificate_instructions():
    """Provide instructions for obtaining/renewing certificate."""
    print("\n" + "=" * 60)
    print("Certificate Setup/Renewal Instructions")
    print("=" * 60)
    
    print("\n📋 To obtain a NEW certificate:")
    print("   1. Make sure your domain DNS points to this server")
    print("   2. Make sure port 80 is open in your firewall")
    print("   3. Run: sudo certbot --nginx -d news.mininglifeserver.com")
    print("   4. Follow the prompts")
    
    print("\n📋 To RENEW an existing certificate:")
    print("   1. Run: sudo certbot renew")
    print("   2. Or test renewal: sudo certbot renew --dry-run")
    
    print("\n📋 To renew and reload nginx automatically:")
    print("   sudo certbot renew --nginx")
    
    print("\n📋 To set up automatic renewal (recommended):")
    print("   1. Test renewal: sudo certbot renew --dry-run")
    print("   2. Add to crontab: sudo crontab -e")
    print("   3. Add line: 0 0 * * * certbot renew --quiet --nginx")


def apply_nginx_config():
    """Provide instructions for applying the nginx config."""
    print("\n" + "=" * 60)
    print("Applying Nginx Configuration")
    print("=" * 60)
    
    config_file = "/home/ubuntu/News_Events_Scraper/nginx_config.conf"
    nginx_sites_available = "/etc/nginx/sites-available/news.mininglifeserver.com"
    nginx_sites_enabled = "/etc/nginx/sites-enabled/news.mininglifeserver.com"
    
    print(f"\n📋 To apply the updated nginx configuration:")
    print(f"   1. Copy config: sudo cp {config_file} {nginx_sites_available}")
    print(f"   2. Create symlink: sudo ln -sf {nginx_sites_available} {nginx_sites_enabled}")
    print(f"   3. Test config: sudo nginx -t")
    print(f"   4. Reload nginx: sudo systemctl reload nginx")
    
    print("\n⚠️  Note: Make sure to update the SSL certificate paths")
    print("   in the config file if they differ from the default Let's Encrypt paths.")


def main():
    """Main function."""
    print("\n" + "=" * 60)
    print("SSL Certificate Diagnostic Tool")
    print("Domain: news.mininglifeserver.com")
    print("=" * 60 + "\n")
    
    # Check if running as root for some operations
    is_root = os.geteuid() == 0
    if not is_root:
        print("⚠️  Note: Some checks require sudo privileges")
        print("   Run with sudo for full diagnostics\n")
    
    # Run diagnostics
    cert_ok = check_certificate_status()
    nginx_ok = check_nginx_config()
    certbot_ok = check_certbot()
    
    # Provide instructions
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    
    if not cert_ok:
        print("\n❌ Certificate issue detected!")
        if not certbot_ok:
            print("   - Certbot needs to be installed")
        get_certificate_instructions()
    else:
        print("\n✅ Certificate appears to be valid")
        print("   If you're still seeing ERR_CERT_DATE_INVALID:")
        print("   1. Check system clock: date")
        print("   2. Try renewing: sudo certbot renew --nginx")
        print("   3. Clear browser cache and try again")
    
    if not nginx_ok:
        print("\n❌ Nginx configuration issues detected!")
        print("   Fix the errors shown above before proceeding")
    
    apply_nginx_config()
    
    print("\n" + "=" * 60)
    print("Diagnostic Complete")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()

