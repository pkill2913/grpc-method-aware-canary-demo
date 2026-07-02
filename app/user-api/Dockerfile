FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY proto/ ./proto/
RUN python -m grpc_tools.protoc -I proto --python_out=. --grpc_python_out=. proto/user.proto

COPY . .

ENV PORT=8080
ENV AVATAR_SERVICE_URL=http://avatar-service:8080
ENV USER_SERVICE_HOST=user-service:50051
ENV DEFAULT_USER_NAME=demo-user
ENV DEFAULT_THEME=blue

EXPOSE 8080

CMD ["python", "app.py"]
