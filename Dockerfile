FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY deal_finder ./deal_finder
RUN pip install --no-cache-dir ".[postgres]" && useradd --uid 10001 --create-home appuser
USER appuser
EXPOSE 8000
CMD ["python", "-m", "deal_finder.serve"]
