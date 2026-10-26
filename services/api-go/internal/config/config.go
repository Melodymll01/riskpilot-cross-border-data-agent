package config

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

type Config struct {
	Listen          string
	PythonURL       string
	Database        string
	Frontend        string
	JWTSecret       string
	ServiceSecret   string
	CookieName      string
	CookieSecure    bool
	CookieSameSite  string
	SessionTTL      time.Duration
	ClientID        string
	ClientSecret    string
	RedirectURI     string
	AuthorizeURL    string
	TokenURL        string
	UserURL         string
	AdminIDs        []string
	MaxUploadBytes  int64
	MaxActive       int
	UpstreamTimeout time.Duration
	RateEnabled     bool
	RatePerMinute   int
}

func value(key, fallback string) string {
	if text := os.Getenv(key); text != "" {
		return text
	}
	return fallback
}

func number(key string, fallback int) (int, error) {
	result, err := strconv.Atoi(value(key, strconv.Itoa(fallback)))
	if err != nil || result <= 0 {
		return 0, errors.New(key + " must be a positive integer")
	}
	return result, nil
}

func Load() (Config, error) {
	root, err := filepath.Abs(value("RISKPILOT_ROOT", "."))
	if err != nil {
		return Config{}, err
	}
	key, err := os.ReadFile(value("INTERNAL_SERVICE_KEY_FILE", filepath.Join(root, "data/internal-service.key")))
	if err != nil {
		return Config{}, errors.New("internal service key unavailable; run make serve")
	}
	ttl, err := number("JWT_TTL_SECONDS", 86400)
	if err != nil {
		return Config{}, err
	}
	upload, err := number("MAX_UPLOAD_MB", 50)
	if err != nil {
		return Config{}, err
	}
	active, err := number("GATEWAY_MAX_ACTIVE_REQUESTS", 16)
	if err != nil {
		return Config{}, err
	}
	timeout, err := number("GATEWAY_UPSTREAM_TIMEOUT_SECONDS", 510)
	if err != nil {
		return Config{}, err
	}
	rate, err := number("GATEWAY_RATE_PER_MINUTE", 60)
	if err != nil {
		return Config{}, err
	}
	secure, err := strconv.ParseBool(value("COOKIE_SECURE", "false"))
	if err != nil {
		return Config{}, errors.New("invalid COOKIE_SECURE")
	}
	rateEnabled, err := strconv.ParseBool(value("RATE_LIMIT_ENABLED", "true"))
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
	database := value("SQLITE_DB_PATH", filepath.Join(root, "data/rag.sqlite3"))
	if !filepath.IsAbs(database) {
		database = filepath.Join(root, database)
	}
	cfg := Config{
		Listen: value("GATEWAY_LISTEN", "127.0.0.1:8765"), PythonURL: value("PYTHON_SERVICE_URL", "http://127.0.0.1:8766"),
		Database: database, Frontend: filepath.Join(root, "frontend"), JWTSecret: os.Getenv("JWT_SECRET"),
		ServiceSecret: strings.TrimSpace(string(key)), CookieName: value("COOKIE_NAME", "copilot_session"),
		CookieSecure: secure, CookieSameSite: value("COOKIE_SAMESITE", "lax"), SessionTTL: time.Duration(ttl) * time.Second,
		ClientID: os.Getenv("GITHUB_CLIENT_ID"), ClientSecret: os.Getenv("GITHUB_CLIENT_SECRET"),
		RedirectURI:  value("GITHUB_REDIRECT_URI", "http://127.0.0.1:8765/api/v2/auth/github/callback"),
		AuthorizeURL: "https://github.com/login/oauth/authorize", TokenURL: "https://github.com/login/oauth/access_token",
		UserURL: "https://api.github.com/user", AdminIDs: admins, MaxUploadBytes: int64(upload) * 1024 * 1024,
		MaxActive: active, UpstreamTimeout: time.Duration(timeout) * time.Second, RateEnabled: rateEnabled, RatePerMinute: rate,
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
	for _, target := range []string{cfg.PythonURL, cfg.RedirectURI} {
		parsed, parseErr := url.Parse(target)
		if parseErr != nil || parsed.Host == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.User != nil {
			return Config{}, errors.New("invalid service or OAuth URL")
		}
	}
	return cfg, nil
}
