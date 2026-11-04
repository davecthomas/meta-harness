# The local egress proxy for per-run sandboxes. See proxy.py.
#
#   docker build -t mh-egress-proxy:dev samples/temporal-run-task/egress-proxy

FROM python:3.12.8-alpine3.21
WORKDIR /app
COPY proxy.py .
USER nobody
EXPOSE 3128 8091
CMD ["python", "-u", "proxy.py"]
