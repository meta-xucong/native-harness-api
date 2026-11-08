# Build from the reviewed code/ directory, pin the resulting image digest in deployment.
# This server-only image never includes native vendor assets or account profiles.
FROM python:3.13.17-slim
WORKDIR /opt/native-harness
COPY src ./src
COPY tools/public_harness_preflight.py ./tools/public_harness_preflight.py
ENV PYTHONPATH=/opt/native-harness/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65532:65532
ENTRYPOINT ["python", "-m", "native_harness_api.runtime", "facade", "--config", "/etc/native-harness/facade.json"]
