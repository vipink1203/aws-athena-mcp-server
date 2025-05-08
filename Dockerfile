FROM python:3.11-slim

WORKDIR /app

# Copy requirements file
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .

# Expose the port
EXPOSE 8050

# Set environment variables
ENV HOST=0.0.0.0
ENV PORT=8050
ENV PYTHONUNBUFFERED=1

# Run the application
CMD ["python", "main.py"]