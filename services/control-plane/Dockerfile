FROM golang:1.26-bookworm AS builder
WORKDIR /src
COPY services/control-plane/go.mod services/control-plane/go.sum ./
RUN go mod download
COPY services/control-plane/ ./
RUN CGO_ENABLED=1 go build -trimpath -o /riskpilot-control-plane ./cmd/riskpilot

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app
COPY --from=builder /riskpilot-control-plane /usr/local/bin/riskpilot-control-plane
COPY --chown=app:app frontend/ /app/frontend/
RUN mkdir -p /app/data /app/logs && chown -R app:app /app
USER app
ENV RISKPILOT_ROOT=/app CONTROL_PLANE_LISTEN=0.0.0.0:8000
EXPOSE 8000
STOPSIGNAL SIGTERM
CMD ["riskpilot-control-plane"]
