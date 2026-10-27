package infra

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"path"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"riskpilot/control-plane/internal/domain"
)

const IdentityHeader = "X-Riskpilot-Identity"

type RuntimeClient struct {
	baseURL       *url.URL
	serviceSecret string
	client        *http.Client
}

func NewRuntimeClient(rawURL, serviceSecret string, timeout time.Duration) (*RuntimeClient, error) {
	base, err := url.Parse(rawURL)
	if err != nil || base.Scheme == "" || base.Host == "" || base.Path != "" || base.RawQuery != "" || base.User != nil {
		return nil, errors.New("agent runtime URL must contain only scheme and authority")
	}
	transport := &http.Transport{Proxy: nil, ResponseHeaderTimeout: 15 * time.Second, IdleConnTimeout: 90 * time.Second, MaxIdleConns: 32, MaxIdleConnsPerHost: 16}
	return &RuntimeClient{baseURL: base, serviceSecret: serviceSecret, client: &http.Client{Timeout: timeout, Transport: transport}}, nil
}

func (c *RuntimeClient) Execute(ctx context.Context, owner string, execute domain.RunExecute, accept func(domain.RunEvent) error) error {
	return c.postEvents(ctx, owner, "/internal/v1/runs/execute", execute, accept)
}

func (c *RuntimeClient) Control(ctx context.Context, owner, action string, control domain.RunControl, accept func(domain.RunEvent) error) error {
	if action != "cancel" {
		return domain.ErrInvalid
	}
	target := path.Join("/internal/v1/runs", control.RunID, action)
	return c.postEvents(ctx, owner, target, control, accept)
}

func (c *RuntimeClient) ProcessDocument(ctx context.Context, actor string, command domain.DocumentProcess) (domain.DocumentResult, error) {
	encoded, err := json.Marshal(command)
	if err != nil {
		return domain.DocumentResult{}, err
	}
	request, err := c.request(ctx, http.MethodPost, "/internal/v1/documents/process", actor, bytes.NewReader(encoded))
	if err != nil {
		return domain.DocumentResult{}, err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	response, err := c.client.Do(request)
	if err != nil {
		return domain.DocumentResult{}, fmt.Errorf("%w: %v", domain.ErrRuntime, err)
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		message, _ := io.ReadAll(io.LimitReader(response.Body, 4096))
		return domain.DocumentResult{}, fmt.Errorf("%w: document process status=%d body=%q", domain.ErrRuntime, response.StatusCode, strings.TrimSpace(string(message)))
	}
	decoder := json.NewDecoder(io.LimitReader(response.Body, 1<<20))
	decoder.DisallowUnknownFields()
	var result domain.DocumentResult
	if err = decoder.Decode(&result); err != nil || result.SchemaVersion != "1.0" || result.DocumentID != command.DocumentID || !oneOfString(result.Status, "ready", "deleted", "failed") || result.ChunkCount < 0 {
		return domain.DocumentResult{}, fmt.Errorf("%w: invalid document result", domain.ErrRuntime)
	}
	return result, nil
}

func (c *RuntimeClient) Ready(ctx context.Context) error {
	// The private runtime intentionally exposes no generic health surface. A signed,
	// read-only method probe proves that the strict execute route and middleware exist.
	request, err := c.request(ctx, http.MethodGet, "/internal/v1/runs/execute", "system:readiness", nil)
	if err != nil {
		return err
	}
	response, err := c.client.Do(request)
	if err != nil {
		return fmt.Errorf("%w: %v", domain.ErrRuntime, err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusMethodNotAllowed {
		return fmt.Errorf("%w: readiness status %d", domain.ErrRuntime, response.StatusCode)
	}
	return nil
}

func (c *RuntimeClient) postEvents(ctx context.Context, owner, path string, body any, accept func(domain.RunEvent) error) error {
	encoded, err := json.Marshal(body)
	if err != nil {
		return err
	}
	request, err := c.request(ctx, http.MethodPost, path, owner, bytes.NewReader(encoded))
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/x-ndjson, text/event-stream")
	response, err := c.client.Do(request)
	if err != nil {
		return fmt.Errorf("%w: %v", domain.ErrRuntime, err)
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		message, _ := io.ReadAll(io.LimitReader(response.Body, 4096))
		return fmt.Errorf("%w: status=%d body=%q", domain.ErrRuntime, response.StatusCode, strings.TrimSpace(string(message)))
	}
	return decodeRuntimeEvents(response.Body, accept)
}

func (c *RuntimeClient) request(ctx context.Context, method, path, owner string, body io.Reader) (*http.Request, error) {
	target := *c.baseURL
	target.Path = path
	request, err := http.NewRequestWithContext(ctx, method, target.String(), body)
	if err != nil {
		return nil, err
	}
	now := time.Now()
	nonce, err := newID("")
	if err != nil {
		return nil, err
	}
	claims := jwt.MapClaims{
		"iss": "riskpilot-go", "aud": "riskpilot-agent", "sub": owner,
		"iat": now.Unix(), "exp": now.Add(30 * time.Second).Unix(), "jti": nonce,
		"method": method, "target": path,
	}
	token, err := jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString([]byte(c.serviceSecret))
	if err != nil {
		return nil, err
	}
	request.Header.Set(IdentityHeader, token)
	return request, nil
}

func decodeRuntimeEvents(reader io.Reader, accept func(domain.RunEvent) error) error {
	scanner := bufio.NewScanner(io.LimitReader(reader, 32<<20))
	scanner.Buffer(make([]byte, 64<<10), 2<<20)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, ":") || strings.HasPrefix(line, "event:") {
			continue
		}
		if strings.HasPrefix(line, "data:") {
			line = strings.TrimSpace(strings.TrimPrefix(line, "data:"))
		}
		decoder := json.NewDecoder(strings.NewReader(line))
		decoder.DisallowUnknownFields()
		var event domain.RunEvent
		if err := decoder.Decode(&event); err != nil {
			return fmt.Errorf("%w: invalid run event: %v", domain.ErrRuntime, err)
		}
		if event.SchemaVersion != "1.0" || len(event.Payload) == 0 {
			return fmt.Errorf("%w: invalid run event envelope", domain.ErrRuntime)
		}
		if err := accept(event); err != nil {
			return err
		}
	}
	if err := scanner.Err(); err != nil {
		return fmt.Errorf("%w: %v", domain.ErrRuntime, err)
	}
	return nil
}

func oneOfString(value string, choices ...string) bool {
	for _, choice := range choices {
		if value == choice {
			return true
		}
	}
	return false
}
