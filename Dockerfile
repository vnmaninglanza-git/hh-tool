FROM python:3.12-slim

WORKDIR /app

COPY hh-applicant-tool-main /app/hh-applicant-tool-main
RUN pip install --no-cache-dir /app/hh-applicant-tool-main flask requests

COPY webui.py /app/webui.py
COPY entrypoint.sh /app/entrypoint.sh

EXPOSE 5050

ENV HH_LOG_DIR=/app/logs
ENV HH_CONFIG=/app/config.json
ENV HH_TOOL_DIR=/app/hh-applicant-tool-main
ENV HH_NO_BROWSER=1
ENV PORT=5050

CMD ["/app/entrypoint.sh"]
