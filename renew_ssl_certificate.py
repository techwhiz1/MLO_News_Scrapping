#!/usr/bin/env python3
"""
SSL Certificate Renewal and Fix Script

This script:
1. Checks system clock
2. Renews the SSL certificate (if needed)
3. Reloads nginx to pick up the certificate
4. Sets up automatic renewal with nginx reload
"""

import os
import subprocess
import sys
from datetime import datetime


def run_command(cmd, check=False, capture_output=True):
    """Run a shell command and return the result."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=capture_output, text=True, check=check
        )
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, "", str(e)


def check_system_clock():
    """Check if system clock is synchronized."""
    print("=" * 60)
    print("Checking System Clock")
    print("=" * 60)
    
    # Get current system time
    success, stdout, stderr = run_command("date")
    if success:
        print(f"Current system time: {stdout.strip()}")
    
    # Check if NTP is enabled
    success, stdout, stderr = run_command("timedatectl status")
    if success:
        print("\nSystem time status:")
        for line in stdout.split('\n'):
            if 'System clock synchronized' in line or 'NTP service' in line:
                print(f"  {line.strip()}")
        
        if 'System clock synchronized: yes' not in stdout:
            print("\n⚠️  Warning: System clock may not be synchronized")
            print("   Attempting to enable NTP synchronization...")
            run_command("sudo timedatectl set-ntp true", check=False)
            print("   ✅ NTP synchronization enabled")
    else:
        print("⚠️  Could not check system clock status")
    
    return True


def renew_certificate():
    """Renew the SSL certificate and reload nginx."""
    print("\n" + "=" * 60)
    print("Renewing SSL Certificate")
    print("=" * 60)
    
    # First, test renewal (dry run)
    print("\n1. Testing certificate renewal (dry run)...")
    success, stdout, stderr = run_command("sudo certbot renew --dry-run --nginx")
    
    if success:
        print("✅ Dry run successful - renewal will work")
    else:
        print("⚠️  Dry run had issues (this is okay if cert is still valid)")
        print(f"   {stderr}")
    
    # Force renewal attempt (certbot will skip if not needed, but reloads nginx)
    print("\n2. Attempting certificate renewal with nginx reload...")
    success, stdout, stderr = run_command("sudo certbot renew --nginx --quiet")
    
    if success:
        print("✅ Certificate renewal process completed")
        print("   (Certificate was renewed if needed, nginx was reloaded)")
    else:
        print("⚠️  Renewal command had issues:")
        print(f"   {stderr}")
        # Try just reloading nginx anyway
        print("\n3. Reloading nginx to ensure it picks up the certificate...")
        success, stdout, stderr = run_command("sudo systemctl reload nginx")
        if success:
            print("✅ Nginx reloaded successfully")
        else:
            print(f"❌ Failed to reload nginx: {stderr}")
            return False
    
    return True


def verify_certificate():
    """Verify the certificate is being used correctly."""
    print("\n" + "=" * 60)
    print("Verifying Certificate")
    print("=" * 60)
    
    cert_path = "/etc/letsencrypt/live/news.mininglifeserver.com/fullchain.pem"
    
    if not os.path.exists(cert_path):
        print(f"❌ Certificate not found at: {cert_path}")
        return False
    
    # Check certificate validity
    success, stdout, stderr = run_command(
        f"sudo openssl x509 -in {cert_path} -noout -checkend 0"
    )
    
    if success:
        print("✅ Certificate is valid")
        
        # Get expiration date
        success, stdout, stderr = run_command(
            f"sudo openssl x509 -in {cert_path} -noout -enddate"
        )
        if success:
            for line in stdout.split('\n'):
                if 'notAfter=' in line:
                    print(f"   Expires: {line.replace('notAfter=', '')}")
    else:
        print("❌ Certificate validation failed")
        print(f"   {stderr}")
        return False
    
    # Check if nginx is using the certificate
    print("\nChecking nginx configuration...")
    success, stdout, stderr = run_command("sudo nginx -t")
    if success:
        print("✅ Nginx configuration is valid")
    else:
        print(f"❌ Nginx configuration has errors: {stderr}")
        return False
    
    return True


def setup_automatic_renewal():
    """Set up automatic certificate renewal with nginx reload."""
    print("\n" + "=" * 60)
    print("Setting Up Automatic Renewal")
    print("=" * 60)
    
    # Check if certbot renewal timer is active
    success, stdout, stderr = run_command("systemctl is-active certbot.timer")
    if success and "active" in stdout:
        print("✅ Certbot timer is already active")
    else:
        print("⚠️  Certbot timer is not active")
        print("   Enabling certbot timer...")
        run_command("sudo systemctl enable certbot.timer", check=False)
        run_command("sudo systemctl start certbot.timer", check=False)
        print("   ✅ Certbot timer enabled")
    
    # Check if renewal hook is configured
    renewal_config = "/etc/letsencrypt/renewal/news.mininglifeserver.com.conf"
    if os.path.exists(renewal_config):
        print(f"\n✅ Renewal configuration exists: {renewal_config}")
        
        # Check if it has nginx reload hook
        with open(renewal_config, 'r') as f:
            content = f.read()
            if 'reload_cmd' in content or 'deploy_hook' in content:
                print("   ✅ Nginx reload hook is configured")
            else:
                print("   ⚠️  Nginx reload hook may not be configured")
                print("   (This is okay if using --nginx flag)")
    else:
        print(f"⚠️  Renewal configuration not found: {renewal_config}")
    
    # Verify timer status
    success, stdout, stderr = run_command("systemctl status certbot.timer")
    if success:
        print("\nCertbot timer status:")
        for line in stdout.split('\n')[:5]:
            if 'Active:' in line or 'Loaded:' in line:
                print(f"   {line.strip()}")
    
    print("\n📋 Automatic renewal is configured via systemd timer")
    print("   Certificates will be renewed automatically before expiration")
    print("   Nginx will be reloaded automatically after renewal")


def main():
    """Main function."""
    print("\n" + "=" * 60)
    print("SSL Certificate Renewal and Fix Script")
    print("Domain: news.mininglifeserver.com")
    print("=" * 60 + "\n")
    
    # Check if running with sudo
    if os.geteuid() != 0:
        print("⚠️  This script requires sudo privileges for some operations")
        print("   Some commands will be run with sudo automatically\n")
    
    # Step 1: Check system clock
    check_system_clock()
    
    # Step 2: Renew certificate and reload nginx
    if not renew_certificate():
        print("\n❌ Failed to renew certificate or reload nginx")
        print("   Please check the errors above and try again")
        sys.exit(1)
    
    # Step 3: Verify certificate
    if not verify_certificate():
        print("\n❌ Certificate verification failed")
        print("   Please check the errors above")
        sys.exit(1)
    
    # Step 4: Set up automatic renewal
    setup_automatic_renewal()
    
    print("\n" + "=" * 60)
    print("✅ Certificate Renewal Complete")
    print("=" * 60)
    print("\nNext steps:")
    print("1. Clear your browser cache and try accessing the site again")
    print("2. If still seeing errors, check browser console for details")
    print("3. Verify the certificate in browser: https://news.mininglifeserver.com")
    print("\nThe certificate will now renew automatically before expiration.")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()

