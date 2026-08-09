ARG PYTHON_IMAGE=scratch
FROM ${PYTHON_IMAGE}

WORKDIR /opt/ctxc

COPY src/ /opt/ctxc/src/
COPY integrations/openhands/src/ /opt/ctxc/integrations/openhands/src/
COPY integrations/openhands/compatibility/ /opt/ctxc/integrations/openhands/compatibility/

ENV PYTHONPATH=/opt/ctxc/src:/opt/ctxc/integrations/openhands/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 65532:65532

ENTRYPOINT ["python", "-m", "ctxc_openhands.cli"]
CMD ["doctor"]
