# Use official Python lightweight runtime
FROM python:3.13-slim

# Set environment variables for Python & Django
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=milcom_project.settings

# Set the working directory inside the container
WORKDIR /app

# Install system dependencies (C compiler is often needed for Pandas/Numpy)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Install Gunicorn for production serving
RUN pip install gunicorn

# Copy the entire Django project
COPY . /app/

# Expose the port Gunicorn runs on
EXPOSE 8000

# Run the Django application via Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "3", "milcom_project.wsgi:application"]