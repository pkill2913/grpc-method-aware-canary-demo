FROM python:3.12-alpine

WORKDIR /app

COPY main.py .
COPY proto ./proto

RUN chmod +x  main.py

ENTRYPOINT ["python3", "/app/main.py"]