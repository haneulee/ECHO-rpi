#!/bin/bash
# Setup script for ECHO Station on Raspberry Pi

set -e

echo "========================================"
echo "ECHO Station - Setup Script"
echo "========================================"
echo ""

# Check if running as root
if [[ $EUID -eq 0 ]]; then
   echo "This script should be run as a regular user, not root."
   echo "It will use sudo for package installation."
   exit 1
fi

# Detect OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
else
    echo "Cannot detect OS. Please install dependencies manually."
    exit 1
fi

echo "Detected OS: $OS"
echo ""

# Install system dependencies based on OS
case $OS in
    raspbian|debian|ubuntu)
        echo "Installing system dependencies for Debian-based system..."
        sudo apt-get update
        sudo apt-get install -y \
            python3-pip \
            python3-dbus \
            libglib2.0-dev \
            libdbus-1-dev \
            bluez
        ;;
    fedora)
        echo "Installing system dependencies for Fedora..."
        sudo dnf install -y \
            python3-pip \
            dbus-python \
            glib2-devel \
            dbus-devel \
            bluez
        ;;
    arch)
        echo "Installing system dependencies for Arch Linux..."
        sudo pacman -S --noconfirm \
            python-pip \
            dbus \
            glib2 \
            bluez
        ;;
    *)
        echo "Unsupported OS: $OS"
        echo "Please install: python3-pip, python3-dbus, libglib2.0-dev, libdbus-1-dev, bluez"
        exit 1
        ;;
esac

echo ""
echo "Installing Python dependencies..."
pip3 install -r requirements.txt

echo ""
echo "========================================"
echo "Setup Complete!"
echo "========================================"
echo ""
echo "To run the ECHO Station:"
echo "  1. Run test suite (optional):"
echo "     python3 test_station.py"
echo ""
echo "  2. Start the station (requires sudo or bluetooth group):"
echo "     sudo python3 run_station.py"
echo ""
echo "     OR add your user to bluetooth group (requires re-login):"
echo "     sudo usermod -a -G bluetooth \$USER"
echo "     python3 run_station.py"
echo ""
echo "For more information, see README.md"
echo ""
