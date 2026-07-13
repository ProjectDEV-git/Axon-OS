#!/bin/bash
# Development environment setup script for Axon OS

set -euo pipefail

echo "🚀 Setting up Axon OS development environment..."

# Update package lists
sudo apt-get update
sudo apt-get install -y \
    python3-dbus \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-4.0 \
    gir1.2-adw-1 \
    python3-vte-2.91 \
    build-essential \
    libffi-dev \
    libssl-dev \
    pkg-config

# Create virtual environment if needed
if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate virtual environment
source .venv/bin/activate

# Install dependencies
echo "📚 Installing Python dependencies..."
pip install --upgrade pip wheel setuptools
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Install pre-commit hooks
echo "🪝 Setting up pre-commit hooks..."
pre-commit install
pre-commit run --all-files || true

# Run tests to verify setup
echo "✅ Running tests to verify setup..."
pytest tests/ -v --tb=short || true

echo "✨ Development environment ready!"
echo ""
echo "💡 Next steps:"
echo "  - Run tests: pytest tests/ -v"
echo "  - Format code: black ."
echo "  - Lint code: ruff check . --fix"
echo "  - Type check: mypy apps services"
echo "  - Pre-commit: pre-commit run --all-files"
