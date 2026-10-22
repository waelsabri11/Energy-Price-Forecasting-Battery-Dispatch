FROM python:3.12-slim
RUN pip install --no-cache-dir mlflow==2.22.5 "sqlalchemy>=2.0.30,<2.1" "alembic<1.18" psycopg2-binary
EXPOSE 5000
CMD ["mlflow", "server", "--host", "0.0.0.0", "--port", "5000", \
     "--backend-store-uri", "sqlite:////mlflow/mlflow.db", \
     "--artifacts-destination", "/mlflow/artifacts", "--serve-artifacts"]
