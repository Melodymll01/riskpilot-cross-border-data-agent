package api

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"
	"riskpilot/api-go/internal/app"
	"riskpilot/api-go/internal/config"
	"riskpilot/api-go/internal/domain"
	"riskpilot/api-go/internal/infra"
)

const IdentityHeader = "X-Riskpilot-Identity"

type Server struct {
	Config config.Config
	Auth   *app.Auth
	Log    *slog.Logger
	Proxy  *httputil.ReverseProxy
	active chan struct{}
	rateMu sync.Mutex
	rates  map[string]*rateWindow
}
type rateWindow struct {
	since time.Time
	count int
}

func New(cfg config.Config, store *infra.Store, log *slog.Logger) (*Server, error) {
	upstream, err := url.Parse(cfg.PythonURL)
	if err != nil {
		return nil, err
	}
	if upstream.Host == "" || upstream.Path != "" || upstream.RawQuery != "" {
		return nil, errors.New("Python URL must contain only scheme and authority")
	}
	server := &Server{Config: cfg, Log: log, Auth: &app.Auth{Config: cfg, Store: store, Client: &http.Client{Timeout: 10 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}, active: make(chan struct{}, cfg.MaxActive), rates: map[string]*rateWindow{}}
	server.Proxy = &httputil.ReverseProxy{
		Rewrite: func(request *httputil.ProxyRequest) {
			request.SetURL(upstream)
			request.Out.Host = upstream.Host
			// Never propagate a browser's credentials or service-identity headers.
			for key := range request.Out.Header {
				if strings.HasPrefix(strings.ToLower(key), "x-riskpilot-") {
					request.Out.Header.Del(key)
				}
			}
			request.Out.Header.Del("Authorization")
			request.Out.Header.Del("Cookie")
			request.Out.Header.Del("X-Forwarded-For")
			request.Out.Header.Del("X-Forwarded-Host")
			request.Out.Header.Del("X-Forwarded-Proto")
			request.Out.Header.Set(IdentityHeader, request.In.Context().Value(identityKey{}).(string))
		},
		Transport:     &http.Transport{Proxy: nil, DialContext: (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext, ResponseHeaderTimeout: 15 * time.Second, IdleConnTimeout: 90 * time.Second, MaxIdleConns: 32, MaxIdleConnsPerHost: 16},
		FlushInterval: -1,
		ModifyResponse: func(response *http.Response) error {
			response.Header.Del("Set-Cookie")
			response.Header.Set("X-Riskpilot-Backend", "go-python")
			return nil
		},
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			if errors.Is(err, context.Canceled) {
				return
			}
			log.Error("Python request failed", "request_id", r.Header.Get("X-Request-ID"), "path", r.URL.Path, "error_type", "upstream_unavailable")
			writeError(w, http.StatusBadGateway, "AI_SERVICE_UNAVAILABLE", "Python Agent 服务暂时不可用，请稍后重试")
		},
	}
	return server, nil
}

type identityKey struct{}

func writeError(w http.ResponseWriter, status int, code, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"error_code": code, "message": message, "details": nil})
}
func (s *Server) fail(c *gin.Context, status int, code, message string) {
	writeError(c.Writer, status, code, message)
	c.Abort()
}
func (s *Server) cookieMode() http.SameSite {
	if s.Config.CookieSameSite == "strict" {
		return http.SameSiteStrictMode
	}
	if s.Config.CookieSameSite == "none" {
		return http.SameSiteNoneMode
	}
	return http.SameSiteLaxMode
}
func (s *Server) cookie(w http.ResponseWriter, name, value string, age int) {
	http.SetCookie(w, &http.Cookie{Name: name, Value: value, Path: "/", HttpOnly: true, Secure: s.Config.CookieSecure, SameSite: s.cookieMode(), MaxAge: age})
}
func (s *Server) current(c *gin.Context) (*domain.User, error) {
	count := 0
	for _, cookie := range c.Request.Cookies() {
		if cookie.Name == s.Config.CookieName {
			count++
		}
	}
	if count != 1 {
		return nil, nil
	}
	cookie, err := c.Request.Cookie(s.Config.CookieName)
	if errors.Is(err, http.ErrNoCookie) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return s.Auth.Identify(c.Request.Context(), cookie.Value)
}
func (s *Server) middleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		requestID, err := app.RandomID()
		if err != nil {
			s.fail(c, 500, "INTERNAL_ERROR", "无法创建请求标识")
			return
		}
		c.Request.Header.Set("X-Request-ID", requestID)
		c.Header("X-Request-ID", requestID)
		if c.Request.Method != http.MethodGet && c.Request.Method != http.MethodHead && c.Request.Method != http.MethodOptions {
			if origin := c.Request.Header.Get("Origin"); origin != "" {
				parsed, parseErr := url.Parse(origin)
				if parseErr != nil || parsed.Host != c.Request.Host || (parsed.Scheme != "http" && parsed.Scheme != "https") {
					s.fail(c, 403, "ORIGIN_REJECTED", "请求来源不匹配")
					return
				}
			}
		}
		started := time.Now()
		c.Next()
		route := c.FullPath()
		if route == "" {
			route = "proxy_or_unmatched"
		}
		s.Log.Info("http request", "request_id", requestID, "method", c.Request.Method, "route", route, "status", c.Writer.Status(), "duration_ms", time.Since(started).Milliseconds())
	}
}

func (s *Server) allowRate(key string, limit int) bool {
	if !s.Config.RateEnabled {
		return true
	}
	s.rateMu.Lock()
	defer s.rateMu.Unlock()
	now := time.Now()
	if len(s.rates) >= 10000 {
		for actor, window := range s.rates {
			if now.Sub(window.since) >= time.Minute {
				delete(s.rates, actor)
			}
		}
	}
	window := s.rates[key]
	if window == nil {
		if len(s.rates) >= 10000 {
			return false
		}
		window = &rateWindow{since: now}
		s.rates[key] = window
	}
	if now.Sub(window.since) >= time.Minute {
		window.since = now
		window.count = 0
	}
	if window.count >= limit {
		return false
	}
	window.count++
	return true
}
func (s *Server) authRate() gin.HandlerFunc {
	return func(c *gin.Context) {
		if !s.allowRate("auth:"+c.ClientIP(), s.Config.RatePerMinute) {
			s.fail(c, 429, "RATE_LIMITED", "请求过于频繁")
			return
		}
		c.Next()
	}
}
func (s *Server) audit(c *gin.Context, action, owner string) {
	if err := s.Auth.Store.Audit(c.Request.Context(), action, owner, c.Request.Header.Get("X-Request-ID")); err != nil {
		s.Log.Error("auth audit failed", "action", action)
	}
}
func (s *Server) Router() *gin.Engine {
	gin.SetMode(gin.ReleaseMode)
	router := gin.New()
	_ = router.SetTrustedProxies(nil)
	router.Use(gin.CustomRecovery(func(c *gin.Context, recovered any) {
		s.Log.Error("request panic")
		s.fail(c, 500, "INTERNAL_ERROR", "服务器内部错误")
	}), s.middleware())
	auth := router.Group("/api/v2/auth", s.authRate())
	auth.POST("/anonymous", func(c *gin.Context) {
		user, err := s.Auth.Anonymous(c.Request.Context())
		if err != nil {
			s.fail(c, 500, "AUTH_ERROR", "无法创建访客身份")
			return
		}
		token, err := s.Auth.Token(user.ID)
		if err != nil {
			s.fail(c, 500, "AUTH_ERROR", "无法创建登录状态")
			return
		}
		s.cookie(c.Writer, s.Config.CookieName, token, int(s.Config.SessionTTL.Seconds()))
		s.audit(c, "AUTH_ANONYMOUS_CREATE", user.ID)
		c.JSON(201, gin.H{"user": user})
	})
	begin := func(c *gin.Context, redirect bool) {
		target, state, err := s.Auth.Begin(c.Request.Context())
		if err != nil {
			s.fail(c, 503, "OAUTH_UNAVAILABLE", "GitHub 登录尚未配置或暂时不可用，可继续使用访客身份")
			return
		}
		s.cookie(c.Writer, s.Config.CookieName+"_oauth", state, 600)
		if redirect {
			c.Redirect(http.StatusSeeOther, target)
		} else {
			c.JSON(200, gin.H{"authorize_url": target, "state": state})
		}
	}
	auth.GET("/github/login", func(c *gin.Context) { begin(c, false) })
	auth.GET("/github/redirect", func(c *gin.Context) { begin(c, true) })
	auth.GET("/github/callback", func(c *gin.Context) {
		state := c.Query("state")
		bound, err := c.Request.Cookie(s.Config.CookieName + "_oauth")
		if err != nil || state == "" || subtle.ConstantTimeCompare([]byte(state), []byte(bound.Value)) != 1 {
			s.fail(c, 400, "OAUTH_STATE_INVALID", "登录状态不匹配，请重新发起 GitHub 登录")
			return
		}
		s.cookie(c.Writer, s.Config.CookieName+"_oauth", "", -1)
		user, err := s.Auth.Exchange(c.Request.Context(), c.Query("code"), state)
		if err != nil {
			s.audit(c, "AUTH_LOGIN_FAILURE", "")
			s.fail(c, 400, "OAUTH_EXCHANGE_FAILED", "GitHub 登录未完成，请重新发起登录")
			return
		}
		token, err := s.Auth.Token(user.ID)
		if err != nil {
			s.fail(c, 500, "AUTH_ERROR", "无法创建登录状态")
			return
		}
		s.cookie(c.Writer, s.Config.CookieName, token, int(s.Config.SessionTTL.Seconds()))
		s.audit(c, "AUTH_LOGIN_SUCCESS", user.ID)
		c.Redirect(http.StatusSeeOther, "/")
	})
	auth.GET("/me", func(c *gin.Context) {
		user, err := s.current(c)
		if err != nil {
			s.fail(c, 503, "AUTH_UNAVAILABLE", "账号数据暂时不可用")
			return
		}
		c.Header("Cache-Control", "no-store")
		c.JSON(200, gin.H{"authenticated": user != nil, "user": user})
	})
	auth.POST("/logout", func(c *gin.Context) {
		user, err := s.current(c)
		if err == nil && user != nil {
			s.audit(c, "AUTH_LOGOUT", user.ID)
		}
		s.cookie(c.Writer, s.Config.CookieName, "", -1)
		c.JSON(200, gin.H{"ok": true})
	})
	router.GET("/api/v2/health", func(c *gin.Context) { c.JSON(200, gin.H{"status": "ok", "version": "v2", "backend": "go"}) })
	router.GET("/api/v2/health/live", func(c *gin.Context) { c.JSON(200, gin.H{"status": "ok", "backend": "go"}) })
	router.GET("/api/v2/health/ready", func(c *gin.Context) {
		ctx, cancel := context.WithTimeout(c.Request.Context(), 3*time.Second)
		defer cancel()
		if err := s.Auth.Store.DB.PingContext(ctx); err != nil {
			s.fail(c, 503, "NOT_READY", "业务数据库尚未就绪")
			return
		}
		request, _ := http.NewRequestWithContext(ctx, http.MethodGet, s.Config.PythonURL+"/api/v2/health/ready", nil)
		response, err := s.Auth.Client.Do(request)
		if err != nil {
			s.fail(c, 503, "NOT_READY", "Python Agent 尚未就绪")
			return
		}
		defer response.Body.Close()
		if response.StatusCode != 200 {
			s.fail(c, 503, "NOT_READY", "Python Agent 尚未就绪")
			return
		}
		c.JSON(200, gin.H{"status": "ok", "checks": gin.H{"ready": true, "database": true, "agent": true}, "backend": "go-python"})
	})
	router.GET("/", func(c *gin.Context) { c.Header("Cache-Control", "no-cache"); c.File(s.Config.Frontend + "/index.html") })
	router.StaticFS("/static", http.Dir(s.Config.Frontend))
	router.NoRoute(func(c *gin.Context) {
		path := c.Request.URL.Path
		if strings.HasPrefix(path, "/api/v2/auth/") {
			s.fail(c, 404, "NOT_FOUND", "接口不存在")
			return
		}
		if path == "/docs" || path == "/openapi.json" || path == "/redoc" || strings.HasPrefix(path, "/api/v2/") {
			s.forward(c)
			return
		}
		s.fail(c, 404, "NOT_FOUND", "接口不存在")
	})
	return router
}

func (s *Server) forward(c *gin.Context) {
	user, err := s.current(c)
	if err != nil {
		s.fail(c, 503, "AUTH_UNAVAILABLE", "账号数据暂时不可用")
		return
	}
	docs := c.Request.URL.Path == "/docs" || c.Request.URL.Path == "/redoc" || c.Request.URL.Path == "/openapi.json"
	if user == nil && !docs {
		s.fail(c, 401, "AUTH_REQUIRED", "需要登录或先获取匿名身份")
		return
	}
	actor := "public"
	if user != nil {
		actor = user.ID
	}
	if !s.allowRate("api:"+actor, s.Config.RatePerMinute) {
		s.fail(c, 429, "RATE_LIMITED", "请求过于频繁")
		return
	}
	select {
	case s.active <- struct{}{}:
		defer func() { <-s.active }()
	default:
		s.fail(c, 503, "CAPACITY_EXCEEDED", "服务繁忙，请稍后重试")
		return
	}
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, s.Config.MaxUploadBytes+1<<20)
	principal := ""
	if user != nil {
		principal = user.ID
	}
	nonce, err := app.RandomID()
	if err != nil {
		s.fail(c, 500, "INTERNAL_ERROR", "无法创建服务身份")
		return
	}
	now := time.Now()
	claims := jwt.MapClaims{"iss": "riskpilot-go", "aud": "riskpilot-agent", "sub": principal, "iat": now.Unix(), "exp": now.Add(30 * time.Second).Unix(), "jti": nonce, "method": c.Request.Method, "target": c.Request.URL.RequestURI()}
	token, err := jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString([]byte(s.Config.ServiceSecret))
	if err != nil {
		s.fail(c, 500, "INTERNAL_ERROR", "无法签发服务身份")
		return
	}
	ctx, cancel := context.WithTimeout(c.Request.Context(), s.Config.UpstreamTimeout)
	defer cancel()
	c.Request = c.Request.WithContext(context.WithValue(ctx, identityKey{}, token))
	s.Proxy.ServeHTTP(c.Writer, c.Request)
}

func DefaultLogger() *slog.Logger { return slog.New(slog.NewJSONHandler(os.Stdout, nil)) }
