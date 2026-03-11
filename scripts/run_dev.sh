#!/bin/bash
# SCORE development setup and run script
set -e

cd "$(dirname "$0")/.."

echo "=== SCORE Development Setup 2 ==="

# Detect Python command (python3 on macOS/Linux, python on Windows/some envs)
PYTHON=python

# Check Python version
$PYTHON --version

# Create virtual environment if needed
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    $PYTHON -m venv .venv
fi

#source .venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -q -r requirements.txt
pip install -q pytest pytest-django factory-boy

# Copy env if needed
if [ ! -f ".env" ]; then
    echo "Creating .env from .env.example..."
    cp .env.example .env
    # Set database broker for dev (no Redis needed)
    sed -i.bak 's/CELERY_BROKER_BACKEND=redis/CELERY_BROKER_BACKEND=database/' .env 2>/dev/null || true
    rm -f .env.bak
fi

# Run migrations
echo "Running migrations..."
python manage.py migrate --run-syncdb

# Load sample data
echo "Loading sample data..."
python scripts/load_sample_data.py

# Collect static files
python manage.py collectstatic --noinput -q 2>/dev/null || true

echo ""
echo "=== Setup Complete ==="
echo ""
echo "To start the development server:"
echo "  source .venv/bin/activate"
echo "  python manage.py runserver"
echo ""
echo "To start the Celery worker (in a separate terminal):"
echo "  source .venv/bin/activate"
echo "  celery -A score worker -l info"
echo ""
echo "Or for no-Redis dev mode (using DB broker):"
echo "  Set CELERY_BROKER_BACKEND=database in .env"
echo "  celery -A score worker -l info -P solo"
echo ""
echo "To run tests:"
echo "  pytest tests/"
echo ""
echo "Login at http://localhost:8000 with admin/admin or demo/demo"
