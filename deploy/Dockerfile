# docs/specs/host-on-fe-pro-Q.md §4.1. Pinned to the exact patch tag, not a
# floating minor tag -- a floating tag would silently move the production
# interpreter on an unrelated rebuild. Matches the laptop's Python exactly,
# which is what makes the host's own (older, irrelevant) Python moot.
FROM python:3.14.5-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Matches finne's uid:gid on the host, so bind-mounted files under /data stay
# Finn-owned rather than becoming root-owned.
RUN groupadd --gid 1000 symr && useradd --uid 1000 --gid 1000 --create-home symr && chown -R 1000:1000 /app
USER 1000:1000

EXPOSE 45660

CMD ["python", "serve.py"]
