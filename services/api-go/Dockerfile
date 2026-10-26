FROM golang:1.26-bookworm AS builder
WORKDIR /src
COPY services/api-go/go.mod services/api-go/go.sum ./
RUN go mod download
COPY services/api-go/ ./
RUN CGO_ENABLED=1 go build -trimpath -o /riskpilot-api ./cmd/riskpilot

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app
COPY --from=builder /riskpilot-api /usr/local/bin/riskpilot-api
COPY --chown=app:app frontend/ /app/frontend/
RUN mkdir -p /app/data /app/logs && chown -R app:app /app
USER app
ENV RISKPILOT_ROOT=/app GATEWAY_LISTEN=0.0.0.0:8000
EXPOSE 8000
STOPSIGNAL SIGTERM
CMD ["riskpilot-api"]
