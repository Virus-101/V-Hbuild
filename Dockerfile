# V-Hbuild platform server: the web app + PlatformIO, so every build ships compiled firmware.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home vhbuild
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt platformio

COPY vhbuild ./vhbuild
USER vhbuild
# Toolchains download on the first compile of each board family; keep them in a volume.
ENV PLATFORMIO_CORE_DIR=/home/vhbuild/.platformio \
    VHBUILD_OLLAMA_URL=http://ollama:11434 \
    VHBUILD_PROVIDER=auto
EXPOSE 8780
CMD ["uvicorn", "vhbuild.web:app", "--host", "0.0.0.0", "--port", "8780"]
