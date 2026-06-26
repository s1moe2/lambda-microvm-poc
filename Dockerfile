FROM python:3.13-slim
WORKDIR /app
COPY probe.py .
EXPOSE 8080
CMD ["python3", "probe.py"]
