# docs/specs/host-on-fe-pro-Q.md §4.1. Pinned to the exact patch tag, not a
# floating minor tag -- a floating tag would silently move the production
# interpreter on an unrelated rebuild. Matches the laptop's Python exactly,
# which is what makes the host's own (older, irrelevant) Python moot.
FROM python:3.14.5-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# svc (2003), fe-pro's shared account for ordinary web services (SERVER.md,
# "Accounts"), which owns /srv/stacks/symr/data on the host. Every writable
# path is under /data (host-on-fe-pro-Q.md §4.4), so /app stays root-owned:
# the service can't rewrite its own code. No bytecode cache for the same reason.
ENV PYTHONDONTWRITEBYTECODE=1
RUN groupadd --gid 2003 svc && useradd --uid 2003 --gid 2003 --create-home svc
USER 2003:2003

EXPOSE 45660

CMD ["python", "serve.py"]
