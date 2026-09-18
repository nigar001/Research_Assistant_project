FROM python:3.12-slim

WORKDIR /app


COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Ensure the cache directory exists (matches CACHE_DIR, default .cache)
RUN mkdir -p /app/.cache

# The image runs the application. TOPIC.md asks for a container that works
# end to end, so the default is a real query; tests run by overriding the
# entrypoint:  docker run --rm --entrypoint pytest <image>
ENTRYPOINT ["python", "-m", "researcher"]
CMD ["ask", "What is photosynthesis and what are its main stages?"]
