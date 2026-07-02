FROM python:3.12-slim

WORKDIR /app

COPY . .
RUN pip install --no-cache-dir -r requirements.txt

COPY proto/ ./proto/
RUN python -m grpc_tools.protoc -I proto --python_out=. --grpc_python_out=. proto/user.proto

COPY . .

ENV PORT=50051
ENV APP_VERSION=v1

EXPOSE 50051

CMD ["python", "server.py"]
