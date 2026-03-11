# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | Yes                |

## Reporting a Vulnerability

If you discover a security vulnerability in DocuScore, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, please email: **_to_be_defined**

Include the following in your report:

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### What to Expect

- Acknowledgment within 48 hours
- An assessment and timeline within 1 week
- A fix or mitigation plan communicated privately before public disclosure

## Security Best Practices for Deployment

- Never use the default `SECRET_KEY` in production
- Generate a proper key: `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`
- Keep `.env` files out of version control (already in `.gitignore`)
- Use HTTPS in production (HSTS is enabled when `DEBUG=False`)
- Rotate API keys regularly
- Change default credentials immediately after setup
- Use a production-grade database (PostgreSQL) for concurrent deployments
