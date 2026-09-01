# Pinned digest so rebuilds are reproducible. Refresh with:
#   docker pull python:3.13-slim && docker inspect python:3.13-slim --format '{{index .RepoDigests 0}}'
# Dependabot keeps it current weekly via .github/dependabot.yml.
FROM python:3.13-slim@sha256:881d80734ee05dca6f7f42dcb080975652a53c7eda9ba1f03bb8da31aa6a6ec2

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Hash-pinned lockfile; --require-hashes refuses anything that does not match.
# Regenerate with:
#   uv pip compile requirements.in -o requirements.lock --generate-hashes --universal --python-version 3.13
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY clients/ ./clients/
COPY server.py .
COPY healthcheck.py .

# Non-root, pinned UID 1000. This server writes nothing to disk: the refresh
# token arrives through the environment and access tokens live in memory only.
# No writable volume is needed, so the rootfs is read-only in compose.
# bootstrap.py is deliberately not copied: it is a one-time operator script
# that opens a browser flow and has no place in the runtime image.
RUN useradd --create-home --uid 1000 --shell /bin/bash mcp \
    && chown -R mcp:mcp /app

# Drop pip now that the dependencies above are installed. Nothing at runtime
# uses it: the entrypoint and the healthcheck are plain `python` calls, and the packages
# themselves are already unpacked into site-packages.
#
# This is the only fix for two recurring Trivy HIGHs. pip ships a vendored
# dependency set (see pip/_vendor/vendor.txt) that Trivy scans as real packages:
# msgpack 1.1.2 (GHSA-6v7p-g79w-8964) and setuptools 70.3.0 (CVE-2025-47273).
# Neither is an application dependency, so no lockfile change can move them, and
# no pip release ships fixed versions. Removing the unused component is the fix.
RUN python -m pip uninstall -y pip \
    && rm -rf /usr/local/lib/python3.*/site-packages/pip \
              /usr/local/lib/python3.*/site-packages/pip-*.dist-info \
              /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.*

USER mcp

EXPOSE 3703

# MCP_HEALTH_PATH is deliberately NOT set here, and must not be set to /mcp.
#
# healthcheck.py is a thin shim over pete_mcp_core.healthcheck.main, so the
# probe path comes from whichever pete-mcp-core commit requirements.in pins.
# That pin is now past 6bf0ceb, which moved the default off /mcp onto the
# unrouted sentinel /__pete_mcp_liveness and added 404 to the healthy codes.
# The sentinel never reaches the MCP transport mount, so no session is minted
# and nothing leaks.
#
# Setting MCP_HEALTH_PATH=/mcp here would undo all of that: a bare GET on the
# mount makes the SDK create a transport session before it returns 406, and
# nothing reaps it. Measured on this image at the old pin: 35.36 kB a probe,
# about 99 MiB/day at this interval.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=15s \
    CMD python healthcheck.py || exit 1

CMD ["python", "server.py"]
