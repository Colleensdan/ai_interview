FROM python:3.11-slim

WORKDIR /app

# Install system deps needed by some Python packages (e.g. spaCy's C extensions)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies first (layer-cached until requirements change)
COPY code/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source and supporting directories
COPY code/ ./code/
COPY prompts/ ./prompts/

# Create writable data directories (Render's filesystem is ephemeral;
# transcripts/times/backups will be written here at runtime)
RUN mkdir -p data/transcripts data/times data/backups

EXPOSE 8501

# Run Streamlit from inside the code directory so relative imports resolve correctly
CMD ["streamlit", "run", "code/interview.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]
