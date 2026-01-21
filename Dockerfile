FROM python:3.11-slim

WORKDIR /app

# Kopiera requirements och installera
COPY api/requirements.txt ./api/
COPY rapport_extraktor/requirements.txt ./rapport_extraktor/
RUN pip install --no-cache-dir -r api/requirements.txt -r rapport_extraktor/requirements.txt

# Kopiera kod
COPY api/ ./api/
COPY rapport_extraktor/ ./rapport_extraktor/

# Exponera port
EXPOSE 8000

# Starta server
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
