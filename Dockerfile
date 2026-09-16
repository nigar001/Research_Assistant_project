FROM python:3.12-slim

WORKDIR /app


COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Ensure the cache directory exists
RUN mkdir -p /app/cache

# Default command to run tests
CMD ["pytest"]