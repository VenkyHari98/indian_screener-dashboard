#!/usr/bin/env python
"""
PKScreener initialization utility to fix TTY/config issues
This script pre-initializes the config file to bypass TTY checks
"""
import os
import sys
import configparser

os.chdir(r"D:\INVESTMENT\AI Automation\Indian Stock Screener")

# Ensure we can write to pkscreener.ini
config_file = "pkscreener.ini"

if os.path.exists(config_file):
    print(f"[*] Removing old config: {config_file}")
    try:
        os.remove(config_file)
    except Exception as e:
        print(f"[!] Could not remove old config: {e}")

# Cre a minimal valid config file
print(f"[+] Creating minimal config: {config_file}")

parser = configparser.ConfigParser()
parser.add_section('config')
parser.add_section('filters')

# Set some basic defaults
parser.set('config', 'alwaysExportToExcel', 'y')
parser.set('config', 'tosAccepted', 'y')
parser.set('config', 'defaultIndex', '0')
parser.set('config', 'logsEnabled', 'n')

parser.set('filters', 'minPrice', '0')
parser.set('filters', 'maxPrice', '50000')
parser.set('filters', 'minimumVolume', '0')

try:
    with open(config_file, 'w') as f:
        parser.write(f)
    print(f"[+] Config created successfully!")
    print(f"[+] You can now run: pkscreener -a Y -o 12:10 -e")
except Exception as e:
    print(f"[!] Failed to create config: {e}")
    sys.exit(1)
