# Contributing to DocuScore

Thank you for your interest in contributing to DocuScore! This guide will help you get started.

## Development Setup

### Prerequisites

- Python 3.12+
- Redis (optional — the app can use an SQLite broker for local dev)

### Getting Started

```bash
# Clone the repository
git clone https://github.com/<org>/docuscore.git
cd docuscore

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Install dependencies (with dev extras)
pip install -e ".[dev]"

# Copy environment config
cp .env.example .env
# Edit .env and set your API keys

# Run migrations
python manage.py migrate

# Create a superuser
python manage.py createsuperuser

# Run the development server
python manage.py runserver
```

### Running Tests

```bash
pytest
```

To run with coverage:

```bash
pytest --cov --cov-report=html
```

### Code Style

This project uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
ruff check .
ruff format .
```

- Target Python version: 3.12
- Line length: 100 characters
- Follow PEP 8 naming conventions

### Updating Dependencies

`pyproject.toml` is the single source of truth for dependencies. After changing
it, regenerate the lock file for reproducible builds:

```bash
pip-compile --strip-extras -o requirements.lock pyproject.toml
```

Commit both `pyproject.toml` and `requirements.lock` together.

## Making Changes

### Branch Naming

- `feat/<description>` for new features
- `fix/<description>` for bug fixes
- `docs/<description>` for documentation
- `refactor/<description>` for refactoring

### Commit Messages

Write clear, concise commit messages. Use the imperative mood:

- "Add duplicate detection endpoint"
- "Fix scoring calculation for empty projects"
- "Update dependencies to resolve vulnerability"

### Pull Request Process

1. Create a feature branch from `main`
2. Make your changes and add tests
3. Ensure all tests pass (`pytest`)
4. Ensure code is formatted (`ruff format --check .`) and linted (`ruff check .`)
5. Open a pull request against `main`
6. Fill in the PR template with a summary and test plan

### Code Review

All PRs require at least one review before merging. Reviewers will check for:

- Correctness and test coverage
- Code style consistency
- Security considerations
- No hardcoded secrets or credentials

## Project Structure

| Directory       | Purpose                                    |
|-----------------|--------------------------------------------|
| `tenants/`      | Multi-tenant models and middleware          |
| `connectors/`   | Data source connectors (SharePoint, etc.)   |
| `ingestion/`    | Document ingestion and chunking pipeline    |
| `vectorstore/`  | Vector storage and similarity search        |
| `analysis/`     | Duplicate, contradiction, gap analysis      |
| `reports/`      | PDF report generation                       |
| `dashboard/`    | Web UI views and templates                  |
| `chat/`         | RAG-based chat interface                    |
| `llm/`          | LLM client abstraction                      |
| `nsg/`          | Semantic graph utilities                    |
| `docuscore/`    | Django project settings and shared modules  |

## Reporting Issues

When reporting bugs, please include:

- Steps to reproduce
- Expected behavior
- Actual behavior
- Python version and OS
- Relevant log output

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
