package app

import (
	"encoding/json"
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// Config contains process-level wiring. Business policy lives in ControlPlane.
type Config struct {
	Listen         string
	RuntimeURL     string
	Database       string
	Frontend       string
	JWTSecret      string
	ServiceSecret  string
	CookieName     string
	CookieSecure   bool
	CookieSameSite string
	SessionTTL     time.Duration
	ClientID       string
	ClientSecret   string
	RedirectURI    string
	AuthorizeURL   string
	TokenURL       string
	UserURL        string
	AdminIDs       []string
	MaxUploadBytes int64
	MaxActive      int
	RuntimeTimeout time.Duration
	RunTimeout     time.Duration
	MaxModelCalls  int
	MaxToolCalls   int
	RateEnabled    bool
	RatePerMinute  int
	SSEKeepAlive   time.Duration
}

func envValue(key, fallback string) string {
	if text := os.Getenv(key); text != "" {
		return text
	}
	return fallback
}

func positiveNumber(key string, fallback int) (int, error) {
	result, err := strconv.Atoi(envValue(key, strconv.Itoa(fallback)))
	if err != nil || result <= 0 {
		return 0, errors.New(key + " must be a positive integer")
	}
	return result, nil
}

func LoadConfig() (Config, error) {
	root, err := filepath.Abs(envValue("RISKPILOT_ROOT", "."))
	if err != nil {
		return Config{}, err
	}
	key, err := os.ReadFile(envValue("INTERNAL_SERVICE_KEY_FILE", filepath.Join(root, "data/internal-service.key")))
	if err != nil {
		return Config{}, errors.New("internal service key unavailable; run make serve")
	}
	ttl, err := positiveNumber("JWT_TTL_SECONDS", 86400)
	if err != nil {
		return Config{}, err
	}
	upload, err := positiveNumber("MAX_UPLOAD_MB", 50)
	if err != nil {
		return Config{}, err
	}
	active, err := positiveNumber("CONTROL_PLANE_MAX_ACTIVE_REQUESTS", 16)
	if err != nil {
		return Config{}, err
	}
	runtimeTimeout, err := positiveNumber("CONTROL_PLANE_RUNTIME_TIMEOUT_SECONDS", 510)
	if err != nil {
		return Config{}, err
	}
	runTimeout, err := positiveNumber("RUN_MAX_DURATION_SECONDS", 120)
	if err != nil {
		return Config{}, err
	}
	modelCalls, err := positiveNumber("RUN_MAX_MODEL_CALLS", 12)
	if err != nil || modelCalls > 50 {
		return Config{}, errors.New("RUN_MAX_MODEL_CALLS must be between 1 and 50")
	}
	toolCalls, err := positiveNumber("RUN_MAX_TOOL_CALLS", 6)
	if err != nil || toolCalls > 50 {
		return Config{}, errors.New("RUN_MAX_TOOL_CALLS must be between 1 and 50")
	}
	rate, err := positiveNumber("CONTROL_PLANE_RATE_PER_MINUTE", 60)
	if err != nil {
		return Config{}, err
	}
	keepalive, err := positiveNumber("SSE_KEEPALIVE_SECONDS", 15)
	if err != nil {
		return Config{}, err
	}
	secure, err := strconv.ParseBool(envValue("COOKIE_SECURE", "false"))
	if err != nil {
		return Config{}, errors.New("invalid COOKIE_SECURE")
	}
	rateEnabled, err := strconv.ParseBool(envValue("RATE_LIMIT_ENABLED", "true"))
	if err != nil {
		return Config{}, errors.New("invalid RATE_LIMIT_ENABLED")
	}
	admins := []string{}
	raw := strings.TrimSpace(os.Getenv("ADMIN_USER_IDS"))
	if strings.HasPrefix(raw, "[") {
		if err = json.Unmarshal([]byte(raw), &admins); err != nil {
			return Config{}, errors.New("invalid ADMIN_USER_IDS")
		}
	} else {
		for _, id := range strings.Split(raw, ",") {
			if id = strings.TrimSpace(id); id != "" {
				admins = append(admins, id)
			}
		}
	}
	database := envValue("CONTROL_PLANE_DB_PATH", filepath.Join(root, "data/control-plane.sqlite3"))
	if !filepath.IsAbs(database) {
		database = filepath.Join(root, database)
	}
	cfg := Config{
		Listen:         envValue("CONTROL_PLANE_LISTEN", "127.0.0.1:8765"),
		RuntimeURL:     envValue("AGENT_RUNTIME_URL", "http://127.0.0.1:8766"),
		Database:       database,
		Frontend:       filepath.Join(root, "frontend"),
		JWTSecret:      os.Getenv("JWT_SECRET"),
		ServiceSecret:  strings.TrimSpace(string(key)),
		CookieName:     envValue("COOKIE_NAME", "copilot_session"),
		CookieSecure:   secure,
		CookieSameSite: envValue("COOKIE_SAMESITE", "lax"),
		SessionTTL:     time.Duration(ttl) * time.Second,
		ClientID:       os.Getenv("GITHUB_CLIENT_ID"), ClientSecret: os.Getenv("GITHUB_CLIENT_SECRET"),
		RedirectURI:  envValue("GITHUB_REDIRECT_URI", "http://127.0.0.1:8765/api/v2/auth/github/callback"),
		AuthorizeURL: "https://github.com/login/oauth/authorize", TokenURL: "https://github.com/login/oauth/access_token", UserURL: "https://api.github.com/user",
		AdminIDs: admins, MaxUploadBytes: int64(upload) * 1024 * 1024, MaxActive: active,
		RuntimeTimeout: time.Duration(runtimeTimeout) * time.Second, RunTimeout: time.Duration(runTimeout) * time.Second,
		MaxModelCalls: modelCalls, MaxToolCalls: toolCalls, RateEnabled: rateEnabled, RatePerMinute: rate,
		SSEKeepAlive: time.Duration(keepalive) * time.Second,
	}
	if len(cfg.JWTSecret) < 32 || len(cfg.ServiceSecret) < 32 || cfg.JWTSecret == cfg.ServiceSecret {
		return Config{}, errors.New("session and service secrets must be separate and at least 32 characters")
	}
	if cfg.CookieSameSite != "lax" && cfg.CookieSameSite != "strict" && cfg.CookieSameSite != "none" {
		return Config{}, errors.New("invalid COOKIE_SAMESITE")
	}
	if cfg.CookieSameSite == "none" && !cfg.CookieSecure {
		return Config{}, errors.New("SameSite=None requires Secure cookies")
	}
	for _, target := range []string{cfg.RuntimeURL, cfg.RedirectURI} {
		parsed, parseErr := url.Parse(target)
		if parseErr != nil || parsed.Host == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.User != nil {
			return Config{}, errors.New("invalid runtime or OAuth URL")
		}
	}
	return cfg, nil
}
