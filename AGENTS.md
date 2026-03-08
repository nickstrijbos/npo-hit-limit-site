# AGENTS.md - Development Guidelines for NPO Hit Limit Site

## Project Overview

This is a Django web application that processes YATA CSV exports to track faction hit limits and calculate ticket payouts for the Torn game. The project uses Django 4.2+, pandas, and SQLite.

## Build / Test Commands

### Validate Configuration
```bash
python manage.py check
```

### Development Server
```bash
python manage.py runserver
```

### Run Tests
```bash
python manage.py test
```

### Run a Single Test
```bash
python manage.py test tracker.tests.<TestClassName>.<test_method_name>
# Example: python manage.py test tracker.tests.MyModelTestCase.test_something
```

### Database Migrations
```bash
python manage.py makemigrations
python manage.py migrate
```

### Django Shell
```bash
python manage.py shell
```

### Docker Build
```bash
docker build -t npo-hit-limit-site .
```

## Code Style Guidelines

### General Principles
- Follow Django conventions and PEP 8 style guide
- Use 4 spaces for indentation (no tabs)
- Maximum line length: 120 characters
- Use type hints where beneficial
- Add docstrings to complex functions

### Naming Conventions
- **Classes**: PascalCase (e.g., `AttackReport`)
- **Functions/Variables**: snake_case (e.g., `csv_file`, `process_csv`)
- **Views**: snake_case, descriptive (e.g., `index_view`)
- **URLs**: lowercase with hyphens (e.g., `/hit-tracker/`)

### Imports (sorted alphabetically within groups)
```python
import os
from datetime import datetime

import pandas as pd
from django.conf import settings
from django.db import models

from tracker.models import Attack
```

### Error Handling
- Use try/except blocks for operations that may fail
- Catch specific exceptions rather than bare except
- Provide meaningful error messages to users

### Database
- Use Django ORM for all database operations
- Create migrations for any model changes: `makemigrations`
- Keep migrations small and focused
- Use appropriate field types (CharField, IntegerField, DateTimeField, etc.)

### Security
- Never commit secrets to version control
- Use environment variables for sensitive configuration
- Always use CSRF tokens in forms (`{% csrf_token %}`)
- Validate all user input
- Sanitize file uploads before processing

### Git Conventions
- Write concise, descriptive commit messages
- Reference issues in commit messages when applicable
- Create feature branches for new features
- Use meaningful branch names (e.g., `feature/add-export-csv`)

## Project Structure

```
npo-hit-limit-site/
├── milcom_project/       # Django project settings
│   ├── settings.py       # Main configuration
│   ├── urls.py           # URL routing
│   └── wsgi.py           # WSGI entry point
├── tracker/              # Main application
│   ├── models.py         # Database models
│   ├── views.py          # View functions
│   ├── urls.py           # App URL patterns
│   ├── admin.py          # Admin configuration
│   ├── apps.py           # App configuration
│   ├── tests.py          # Unit tests
│   └── templates/tracker/
│       └── index.html    # Main template
├── static/               # Static files
├── db.sqlite3            # SQLite database
├── manage.py             # Django CLI
├── requirements.txt      # Python dependencies
└── Dockerfile            # Docker configuration
```

## Configuration

### Environment Variables
- `DJANGO_SETTINGS_MODULE`: Set to `milcom_project.settings`
- `SECRET_KEY`: Django secret key (keep private)
- `DEBUG`: Set to `True` for development, `False` for production
- `ALLOWED_HOSTS`: Comma-separated list of allowed hosts

Production settings are in `milcom_project/settings.py`. Adjust DEBUG and ALLOWED_HOSTS for production.

## Testing Guidelines

- Write tests for all new features
- Test both success and error paths
- Use Django's test client for view testing
- Use `@override_settings` for test-specific configuration

## Common Tasks

### Adding a New View
1. Add view function in `tracker/views.py`
2. Add URL pattern in `tracker/urls.py`
3. Create template in `tracker/templates/tracker/`
4. Add tests in `tracker/tests.py`

### Processing CSV Data
The application uses pandas to read YATA CSV exports. Handle both comma and semicolon delimiters:

```python
df = pd.read_csv(csv_file)
if 'timestamp_started' not in df.columns:
    csv_file.seek(0)
    df = pd.read_csv(csv_file, sep=';')
```

### Deployment
GitHub Actions builds and pushes Docker image to GHCR on push to main.
