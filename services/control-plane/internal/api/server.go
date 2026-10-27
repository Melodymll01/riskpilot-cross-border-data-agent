package api

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"mime/multipart"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
	"riskpilot/control-plane/internal/app"
	"riskpilot/control-plane/internal/domain"
)

type Store interface {
	app.AuthStore
	app.Repository
}

type Server struct {
	Config  app.Config
	Auth    *app.Auth
	Control *app.ControlPlane
	Log     *slog.Logger
	active  chan struct{}
	rateMu  sync.Mutex
	rates   map[string]*rateWindow
}

type rateWindow struct {
	since time.Time
	count int
}

func New(cfg app.Config, store Store, runtime app.Runtime, log *slog.Logger) (*Server, error) {
	if store == nil || runtime == nil {
		return nil, errors.New("control-plane dependencies are required")
	}
	auth := &app.Auth{Config: cfg, Store: store, Client: &http.Client{Timeout: 10 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
	return &Server{Config: cfg, Auth: auth, Control: app.NewControlPlane(store, runtime, cfg), Log: log, active: make(chan struct{}, cfg.MaxActive), rates: map[string]*rateWindow{}}, nil
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"error_code": code, "message": message, "details": nil})
}

func (s *Server) fail(c *gin.Context, status int, code, message string) {
	writeError(c.Writer, status, code, message)
	c.Abort()
}

func (s *Server) failError(c *gin.Context, err error) {
	switch {
	case errors.Is(err, domain.ErrUnsupportedDocumentType):
		s.fail(c, http.StatusUnsupportedMediaType, "UNSUPPORTED_DOCUMENT_TYPE", "不支持的文档类型")
	case errors.Is(err, domain.ErrInvalidDocumentContent):
		s.fail(c, http.StatusBadRequest, "INVALID_DOCUMENT_CONTENT", "文档内容无效")
	case errors.Is(err, domain.ErrUploadTooLarge):
		s.fail(c, http.StatusRequestEntityTooLarge, "UPLOAD_TOO_LARGE", "文件超过上传大小限制")
	case errors.Is(err, domain.ErrInvalid):
		s.fail(c, http.StatusUnprocessableEntity, "VALIDATION_ERROR", "请求字段无效")
	case errors.Is(err, domain.ErrIdempotency):
		s.fail(c, http.StatusConflict, "IDEMPOTENCY_CONFLICT", "幂等键已用于不同请求")
	case errors.Is(err, domain.ErrRevision):
		s.fail(c, http.StatusConflict, "REVISION_CONFLICT", "记录已被更新，请刷新后重试")
	case errors.Is(err, domain.ErrConflict), errors.Is(err, domain.ErrTerminalRun), errors.Is(err, domain.ErrEventSequence):
		s.fail(c, http.StatusConflict, "CONVERSATION_RUN_CONFLICT", "运行状态冲突")
	case errors.Is(err, domain.ErrForbidden):
		s.fail(c, http.StatusForbidden, "FORBIDDEN", "当前身份无权执行此操作")
	case errors.Is(err, domain.ErrNotFound):
		s.fail(c, http.StatusNotFound, "NOT_FOUND", "资源不存在")
	case errors.Is(err, domain.ErrRuntime):
		s.fail(c, http.StatusBadGateway, "AI_SERVICE_UNAVAILABLE", "Agent Runtime 暂时不可用，请稍后重试")
	default:
		s.Log.Error("request failed", "error", err.Error())
		s.fail(c, http.StatusInternalServerError, "INTERNAL_ERROR", "服务器内部错误")
	}
}

func (s *Server) cookieMode() http.SameSite {
	switch s.Config.CookieSameSite {
	case "strict":
		return http.SameSiteStrictMode
	case "none":
		return http.SameSiteNoneMode
	default:
		return http.SameSiteLaxMode
	}
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

func (s *Server) requireOwner(c *gin.Context) (*domain.User, bool) {
	user, err := s.current(c)
	if err != nil {
		s.fail(c, http.StatusServiceUnavailable, "AUTH_UNAVAILABLE", "账号数据暂时不可用")
		return nil, false
	}
	if user == nil {
		s.fail(c, http.StatusUnauthorized, "AUTH_REQUIRED", "需要登录或先获取匿名身份")
		return nil, false
	}
	return user, true
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
			route = "unmatched"
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
	window := s.rates[key]
	if window == nil || now.Sub(window.since) >= time.Minute {
		window = &rateWindow{since: now}
		s.rates[key] = window
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

func (s *Server) businessRate(c *gin.Context, owner string) bool {
	if !s.allowRate("api:"+owner, s.Config.RatePerMinute) {
		s.fail(c, 429, "RATE_LIMITED", "请求过于频繁")
		return false
	}
	return true
}

func (s *Server) acquire(c *gin.Context) bool {
	select {
	case s.active <- struct{}{}:
		return true
	default:
		s.fail(c, 503, "CAPACITY_EXCEEDED", "服务繁忙，请稍后重试")
		return false
	}
}

func (s *Server) audit(c *gin.Context, action, owner string) {
	if err := s.Auth.Store.Audit(c.Request.Context(), action, owner, c.Request.Header.Get("X-Request-ID")); err != nil {
		s.Log.Error("auth audit failed", "action", action)
	}
}

func decodeJSON(c *gin.Context, limit int64, target any) error {
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, limit)
	decoder := json.NewDecoder(c.Request.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return domain.ErrInvalid
	}
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		return domain.ErrInvalid
	}
	return nil
}

func (s *Server) Router() *gin.Engine {
	gin.SetMode(gin.ReleaseMode)
	router := gin.New()
	router.UseRawPath = true
	router.UnescapePathValues = true
	_ = router.SetTrustedProxies(nil)
	router.Use(gin.CustomRecovery(func(c *gin.Context, recovered any) {
		s.Log.Error("request panic", "value", fmt.Sprint(recovered))
		s.fail(c, 500, "INTERNAL_ERROR", "服务器内部错误")
	}), s.middleware())
	s.registerAuth(router)
	s.registerHealth(router)
	s.registerTasks(router)
	s.registerCopilot(router)
	s.registerFeedback(router)
	s.registerMemory(router)
	s.registerFiles(router)
	s.registerDocuments(router)
	router.GET("/", func(c *gin.Context) {
		c.Header("Cache-Control", "no-cache")
		c.File(filepath.Join(s.Config.Frontend, "index.html"))
	})
	router.StaticFS("/static", http.Dir(s.Config.Frontend))
	router.NoRoute(func(c *gin.Context) { s.fail(c, 404, "NOT_FOUND", "接口不存在") })
	return router
}

func (s *Server) registerAuth(router *gin.Engine) {
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
}

func (s *Server) registerHealth(router *gin.Engine) {
	router.GET("/api/v2/health", func(c *gin.Context) {
		c.JSON(200, gin.H{"status": "ok", "version": "v2", "backend": "go-control-plane"})
	})
	router.GET("/api/v2/health/live", func(c *gin.Context) { c.JSON(200, gin.H{"status": "ok", "backend": "go-control-plane"}) })
	router.GET("/api/v2/health/ready", func(c *gin.Context) {
		ctx, cancel := context.WithTimeout(c.Request.Context(), 3*time.Second)
		defer cancel()
		if err := s.Control.Runtime.Ready(ctx); err != nil {
			s.fail(c, 503, "NOT_READY", "Agent Runtime 尚未就绪")
			return
		}
		c.JSON(200, gin.H{"status": "ok", "checks": gin.H{"ready": true, "database": true, "agent_runtime": true}, "backend": "go-control-plane"})
	})
}

func (s *Server) registerTasks(router *gin.Engine) {
	router.GET("/api/v2/tasks", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		limit := 50
		if _, err := fmt.Sscan(c.Query("limit"), &limit); err != nil && c.Query("limit") != "" {
			limit = 50
		}
		tasks, err := s.Control.ListTasks(c.Request.Context(), user.ID, limit)
		if err != nil {
			s.failError(c, err)
			return
		}
		c.JSON(200, gin.H{"tasks": tasks})
	})
	router.GET("/api/v2/tasks/:task_id", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		detail, err := s.Control.TaskDetail(c.Request.Context(), user.ID, c.Param("task_id"))
		if err != nil {
			s.failError(c, err)
			return
		}
		if detail == nil {
			s.fail(c, 404, "TASK_NOT_FOUND", "task not found")
			return
		}
		c.JSON(200, detail)
	})
	router.PATCH("/api/v2/tasks/:task_id", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		var body struct {
			Title          *string        `json:"title"`
			CollectedFacts map[string]any `json:"collected_facts"`
		}
		if err := decodeJSON(c, 1<<20, &body); err != nil {
			s.failError(c, err)
			return
		}
		task, err := s.Control.Repo.UpdateTask(c.Request.Context(), user.ID, c.Param("task_id"), body.Title, body.CollectedFacts)
		if err != nil {
			s.failError(c, err)
			return
		}
		if task == nil {
			s.fail(c, 404, "TASK_NOT_FOUND", "task not found")
			return
		}
		c.JSON(200, task)
	})
	router.DELETE("/api/v2/tasks/:task_id", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		deleted, err := s.Control.Repo.DeleteTask(c.Request.Context(), user.ID, c.Param("task_id"))
		if err != nil {
			s.failError(c, err)
			return
		}
		if !deleted {
			s.fail(c, 404, "TASK_NOT_FOUND", "task not found")
			return
		}
		c.JSON(200, gin.H{"ok": true})
	})
}

func (s *Server) registerCopilot(router *gin.Engine) {
	decode := func(c *gin.Context) (app.ChatInput, bool) {
		var body struct {
			TaskID      *string  `json:"task_id"`
			Message     string   `json:"message"`
			Attachments []string `json:"attachment_doc_ids"`
		}
		if err := decodeJSON(c, s.Config.MaxUploadBytes, &body); err != nil {
			s.failError(c, err)
			return app.ChatInput{}, false
		}
		return app.ChatInput{TaskID: body.TaskID, Message: body.Message, AttachmentDocumentIDs: body.Attachments}, true
	}
	router.POST("/api/v2/copilot/chat", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok || !s.businessRate(c, user.ID) || !s.acquire(c) {
			return
		}
		defer func() { <-s.active }()
		input, ok := decode(c)
		if !ok {
			return
		}
		events := []domain.ExternalEvent{}
		run, err := s.Control.Start(c.Request.Context(), user.ID, c.GetHeader("Idempotency-Key"), input, func(event domain.ExternalEvent) error { events = append(events, event); return nil })
		if err != nil && !run.Terminal() {
			s.failError(c, err)
			return
		}
		c.JSON(200, gin.H{"task_id": run.TaskID, "run_id": run.RunID, "events": events})
	})
	router.POST("/api/v2/copilot/chat/stream", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok || !s.businessRate(c, user.ID) || !s.acquire(c) {
			return
		}
		defer func() { <-s.active }()
		input, ok := decode(c)
		if !ok {
			return
		}
		c.Header("Content-Type", "text/event-stream; charset=utf-8")
		c.Header("Cache-Control", "no-cache")
		c.Header("X-Accel-Buffering", "no")
		c.Status(200)
		flusher, canFlush := c.Writer.(http.Flusher)
		emit := func(event domain.ExternalEvent) error {
			data, err := json.Marshal(event.Payload)
			if err != nil {
				return err
			}
			if _, err = fmt.Fprintf(c.Writer, "event: %s\ndata: %s\n\n", event.EventType, data); err != nil {
				return err
			}
			if canFlush {
				flusher.Flush()
			}
			return nil
		}
		_, err := s.Control.Start(c.Request.Context(), user.ID, c.GetHeader("Idempotency-Key"), input, emit)
		if err != nil && !errors.Is(err, context.Canceled) {
			_ = emit(domain.ExternalEvent{EventType: "error", Payload: map[string]any{"error_code": "RUN_FAILED", "message": err.Error()}})
		}
	})
	router.GET("/api/v2/copilot/runs/:run_id", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		run, err := s.Control.GetRun(c.Request.Context(), user.ID, c.Param("run_id"))
		if err != nil {
			s.failError(c, err)
			return
		}
		if run == nil {
			s.fail(c, 404, "RUN_NOT_FOUND", "run not found")
			return
		}
		c.JSON(200, run)
	})
	router.POST("/api/v2/copilot/runs/:run_id/cancel", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		run, err := s.Control.Cancel(c.Request.Context(), user.ID, c.Param("run_id"))
		if err != nil {
			s.failError(c, err)
			return
		}
		view := domain.ViewRun(run, time.Now())
		c.JSON(200, view)
	})
	router.POST("/api/v2/copilot/runs/:run_id/resume", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok || !s.businessRate(c, user.ID) || !s.acquire(c) {
			return
		}
		defer func() { <-s.active }()
		current, err := s.Control.GetRun(c.Request.Context(), user.ID, c.Param("run_id"))
		if err != nil {
			s.failError(c, err)
			return
		}
		if current == nil {
			s.fail(c, 404, "RUN_NOT_FOUND", "run not found")
			return
		}
		if current.Status != "interrupted" {
			s.fail(c, 409, "CONVERSATION_RUN_CONFLICT", "仅已中断运行可以恢复")
			return
		}
		c.Header("Content-Type", "text/event-stream; charset=utf-8")
		c.Header("Cache-Control", "no-cache")
		c.Header("X-Accel-Buffering", "no")
		c.Status(200)
		flusher, canFlush := c.Writer.(http.Flusher)
		emit := func(event domain.ExternalEvent) error {
			data, _ := json.Marshal(event.Payload)
			_, err := fmt.Fprintf(c.Writer, "event: %s\ndata: %s\n\n", event.EventType, data)
			if canFlush {
				flusher.Flush()
			}
			return err
		}
		_, err = s.Control.Resume(c.Request.Context(), user.ID, c.Param("run_id"), emit)
		if err != nil {
			_ = emit(domain.ExternalEvent{EventType: "error", Payload: map[string]any{"error_code": "CONVERSATION_RUN_CONFLICT", "message": err.Error()}})
		}
	})
}

func (s *Server) registerFeedback(router *gin.Engine) {
	router.POST("/api/v2/feedback", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		var body struct {
			MessageID string `json:"msg_id"`
			TaskID    string `json:"task_id"`
			Rating    string `json:"rating"`
		}
		if err := decodeJSON(c, 1<<20, &body); err != nil || !oneOf(body.Rating, "up", "down", "none") || body.MessageID == "" || body.TaskID == "" {
			s.failError(c, domain.ErrInvalid)
			return
		}
		var rating *string
		if body.Rating != "none" {
			rating = &body.Rating
		}
		stored, err := s.Control.Repo.SubmitFeedback(c.Request.Context(), domain.Feedback{MessageID: body.MessageID, TaskID: body.TaskID, OwnerID: user.ID, Rating: rating})
		if err != nil {
			s.failError(c, err)
			return
		}
		c.JSON(200, gin.H{"msg_id": body.MessageID, "rating": stored})
	})
	router.GET("/api/v2/feedback", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		ratings, err := s.Control.Repo.FeedbackForTask(c.Request.Context(), user.ID, c.Query("task_id"))
		if err != nil {
			s.failError(c, err)
			return
		}
		c.JSON(200, gin.H{"ratings": ratings})
	})
}

func (s *Server) registerMemory(router *gin.Engine) {
	require := func(c *gin.Context) (*domain.User, bool) {
		user, ok := s.requireOwner(c)
		if !ok {
			return nil, false
		}
		if err := s.Control.EnsureAccount(user); err != nil {
			s.failError(c, err)
			return nil, false
		}
		c.Header("Cache-Control", "no-store")
		return user, true
	}
	router.GET("/api/v2/memory", func(c *gin.Context) {
		user, ok := require(c)
		if !ok {
			return
		}
		memories, enabled, err := s.Control.Repo.ListMemories(c.Request.Context(), user.ID)
		if err != nil {
			s.failError(c, err)
			return
		}
		c.JSON(200, gin.H{"enabled": enabled, "memories": memories, "policy": s.Control.MemoryPolicy()})
	})
	router.PUT("/api/v2/memory/settings", func(c *gin.Context) {
		user, ok := require(c)
		if !ok {
			return
		}
		var body struct {
			Enabled bool `json:"enabled"`
		}
		if err := decodeJSON(c, 1<<20, &body); err != nil {
			s.failError(c, err)
			return
		}
		if err := s.Control.Repo.SetMemoryEnabled(c.Request.Context(), user.ID, body.Enabled); err != nil {
			s.failError(c, err)
			return
		}
		c.JSON(200, gin.H{"enabled": body.Enabled})
	})
	router.POST("/api/v2/memory", func(c *gin.Context) {
		user, ok := require(c)
		if !ok {
			return
		}
		var body struct {
			Content         string   `json:"content"`
			Kind            string   `json:"kind"`
			ProfileField    *string  `json:"profile_field"`
			ExpiresAt       *float64 `json:"expires_at"`
			SourceTaskID    *string  `json:"source_task_id"`
			SourceMessageID *string  `json:"source_message_id"`
		}
		if err := decodeJSON(c, 1<<20, &body); err != nil {
			s.failError(c, err)
			return
		}
		memory, unchanged, err := s.Control.CreateMemory(c.Request.Context(), user.ID, body.Content, body.Kind, body.ProfileField, body.ExpiresAt, body.SourceTaskID, body.SourceMessageID)
		if err != nil {
			s.failError(c, err)
			return
		}
		payload := map[string]any{}
		data, _ := json.Marshal(memory)
		_ = json.Unmarshal(data, &payload)
		payload["saved_count"] = 1
		payload["related_memories"] = []any{}
		payload["unchanged"] = unchanged
		c.JSON(201, payload)
	})
	router.PATCH("/api/v2/memory/:memory_id", func(c *gin.Context) {
		user, ok := require(c)
		if !ok {
			return
		}
		var raw map[string]json.RawMessage
		if err := decodeJSON(c, 1<<20, &raw); err != nil {
			s.failError(c, err)
			return
		}
		for key := range raw {
			if key != "content" && key != "expected_revision" && key != "expires_at" {
				s.failError(c, domain.ErrInvalid)
				return
			}
		}
		var content string
		var revision int
		if json.Unmarshal(raw["content"], &content) != nil || json.Unmarshal(raw["expected_revision"], &revision) != nil {
			s.failError(c, domain.ErrInvalid)
			return
		}
		content = strings.TrimSpace(content)
		if content == "" || len([]rune(content)) > 800 || revision < 1 {
			s.failError(c, domain.ErrInvalid)
			return
		}
		var expiry *float64
		provided := false
		if value, exists := raw["expires_at"]; exists {
			provided = true
			if string(value) != "null" && json.Unmarshal(value, &expiry) != nil {
				s.failError(c, domain.ErrInvalid)
				return
			}
		}
		memory, err := s.Control.Repo.UpdateMemory(c.Request.Context(), user.ID, c.Param("memory_id"), revision, content, expiry, provided)
		if err != nil {
			s.failError(c, err)
			return
		}
		c.JSON(200, memory)
	})
	router.POST("/api/v2/memory/:memory_id/confirm", func(c *gin.Context) {
		user, ok := require(c)
		if !ok {
			return
		}
		var body struct {
			ExpectedRevision int `json:"expected_revision"`
		}
		if err := decodeJSON(c, 1<<20, &body); err != nil {
			s.failError(c, err)
			return
		}
		memory, err := s.Control.Repo.ConfirmMemory(c.Request.Context(), user.ID, c.Param("memory_id"), body.ExpectedRevision, float64(time.Now().UnixNano())/1e9)
		if err != nil {
			s.failError(c, err)
			return
		}
		c.JSON(200, memory)
	})
	router.DELETE("/api/v2/memory/:memory_id", func(c *gin.Context) {
		user, ok := require(c)
		if !ok {
			return
		}
		deleted, err := s.Control.Repo.DeleteMemory(c.Request.Context(), user.ID, c.Param("memory_id"))
		if err != nil {
			s.failError(c, err)
			return
		}
		if !deleted {
			s.fail(c, 404, "MEMORY_NOT_FOUND", "记忆不存在")
			return
		}
		c.JSON(200, gin.H{"deleted": true})
	})
	router.DELETE("/api/v2/memory", func(c *gin.Context) {
		user, ok := require(c)
		if !ok {
			return
		}
		count, err := s.Control.Repo.ClearMemories(c.Request.Context(), user.ID)
		if err != nil {
			s.failError(c, err)
			return
		}
		c.JSON(200, gin.H{"deleted_count": count})
	})
}

func (s *Server) registerFiles(router *gin.Engine) {
	router.POST("/api/v2/files", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, s.Config.MaxUploadBytes+1<<20)
		header, err := c.FormFile("file")
		if err != nil {
			s.failError(c, domain.ErrInvalid)
			return
		}
		content, mimeType, err := readUpload(header, s.Config.MaxUploadBytes)
		if err != nil {
			s.failError(c, err)
			return
		}
		documentID, _ := app.RandomID()
		versionID, _ := app.RandomID()
		jobID, _ := app.RandomID()
		documentID = "doc_" + documentID
		versionID = "ver_" + versionID
		jobID = "job_" + jobID
		now := float64(time.Now().UnixNano()) / 1e9
		digest := sha256.Sum256(content)
		document := domain.Document{DocumentID: documentID, OwnerID: user.ID, LogicalName: filepath.Base(header.Filename), DocumentType: mimeType, Status: "queued", CreatedBy: user.ID, CurrentVersionID: versionID, CreatedAt: now, UpdatedAt: now}
		version := domain.DocumentVersion{VersionID: versionID, DocumentID: documentID, VersionNumber: 1, SHA256: hex.EncodeToString(digest[:]), MIMEType: mimeType, SizeBytes: int64(len(content)), ParserVersion: "", CreatedAt: now}
		job := domain.ProcessingJob{JobID: jobID, DocumentVersionID: versionID, Status: "queued", CurrentStage: "extract_structure", Progress: 0, CreatedAt: now, UpdatedAt: now}
		if err = s.Control.Repo.CreateFile(c.Request.Context(), domain.FileRecord{Document: document, Version: version, Job: &job, Content: content}); err != nil {
			s.failError(c, err)
			return
		}
		c.JSON(201, gin.H{"document": document, "version": version, "job": job})
	})
	router.GET("/api/v2/files", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		files, err := s.Control.Repo.ListFiles(c.Request.Context(), user.ID)
		if err != nil {
			s.failError(c, err)
			return
		}
		items := make([]any, 0, len(files))
		for _, file := range files {
			items = append(items, gin.H{"document": file.Document, "job": file.Job})
		}
		c.JSON(200, gin.H{"documents": items})
	})
	router.GET("/api/v2/files/search", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		hits, err := s.Control.Repo.SearchFiles(c.Request.Context(), user.ID, c.Query("query"), 20)
		if err != nil {
			s.failError(c, err)
			return
		}
		c.JSON(200, gin.H{"hits": hits})
	})
	router.GET("/api/v2/files/jobs/:job_id", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		job, err := s.Control.Repo.GetFileJob(c.Request.Context(), user.ID, c.Param("job_id"))
		if err != nil {
			s.failError(c, err)
			return
		}
		if job == nil {
			s.fail(c, 404, "PROCESSING_JOB_NOT_FOUND", "processing job not found")
			return
		}
		c.JSON(200, job)
	})
	router.POST("/api/v2/files/jobs/:job_id/retry", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		job, err := s.Control.Repo.RetryFileJob(c.Request.Context(), user.ID, c.Param("job_id"))
		if err != nil {
			s.failError(c, err)
			return
		}
		if job == nil {
			s.fail(c, 404, "PROCESSING_JOB_NOT_FOUND", "processing job not found")
			return
		}
		c.JSON(200, job)
	})
	router.POST("/api/v2/files/jobs/:job_id/cancel", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		job, err := s.Control.Repo.CancelFileJob(c.Request.Context(), user.ID, c.Param("job_id"))
		if err != nil {
			s.failError(c, err)
			return
		}
		if job == nil {
			s.fail(c, 404, "PROCESSING_JOB_NOT_FOUND", "processing job not found")
			return
		}
		c.JSON(200, job)
	})
	router.GET("/api/v2/files/:document_id", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		file, err := s.Control.Repo.GetFile(c.Request.Context(), user.ID, c.Param("document_id"))
		if err != nil {
			s.failError(c, err)
			return
		}
		if file == nil {
			s.fail(c, 404, "DOCUMENT_NOT_FOUND", "document not found")
			return
		}
		c.JSON(200, gin.H{"document": file.Document, "job": file.Job})
	})
	router.GET("/api/v2/files/:document_id/download", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		file, err := s.Control.Repo.GetFile(c.Request.Context(), user.ID, c.Param("document_id"))
		if err != nil {
			s.failError(c, err)
			return
		}
		if file == nil {
			s.fail(c, 404, "DOCUMENT_NOT_FOUND", "document not found")
			return
		}
		c.Header("Content-Disposition", "attachment; filename*=UTF-8''"+url.QueryEscape(file.Document.LogicalName))
		c.Data(200, file.Version.MIMEType, file.Content)
	})
}

func (s *Server) registerDocuments(router *gin.Engine) {
	resolveScope := func(c *gin.Context, user *domain.User) (string, *string, bool) {
		scope := c.DefaultQuery("scope", "public")
		switch scope {
		case "public":
			return scope, nil, true
		case "private", "all":
			owner := user.ID
			return scope, &owner, true
		default:
			s.failError(c, domain.ErrInvalid)
			return "", nil, false
		}
	}
	router.GET("/api/v2/documents", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		scope, owner, ok := resolveScope(c, user)
		if !ok {
			return
		}
		documents, err := s.Control.Repo.ListKBDocuments(c.Request.Context(), scope, owner)
		if err != nil {
			s.failError(c, err)
			return
		}
		ready := make([]domain.KBDocument, 0, len(documents))
		total := 0
		for _, document := range documents {
			if document.Status == "ready" {
				ready = append(ready, document)
				total += document.ChunkCount
			}
		}
		c.JSON(200, gin.H{"documents": ready, "total_chunks": total})
	})
	router.GET("/api/v2/documents/stats", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		scope, owner, ok := resolveScope(c, user)
		if !ok {
			return
		}
		documents, err := s.Control.Repo.ListKBDocuments(c.Request.Context(), scope, owner)
		if err != nil {
			s.failError(c, err)
			return
		}
		count, chunks := 0, 0
		for _, document := range documents {
			if document.Status == "ready" {
				count++
				chunks += document.ChunkCount
			}
		}
		c.JSON(200, gin.H{"document_count": count, "chunk_count": chunks})
	})
	router.POST("/api/v2/documents/file", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		asPublic := c.Query("as_public") == "true"
		if asPublic && !user.Admin {
			s.fail(c, 403, "ADMIN_REQUIRED", "仅管理员可写公共知识库")
			return
		}
		c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, s.Config.MaxUploadBytes+1<<20)
		header, err := c.FormFile("file")
		if err != nil {
			s.failError(c, domain.ErrInvalid)
			return
		}
		content, mimeType, err := readUpload(header, s.Config.MaxUploadBytes)
		if err != nil {
			s.failError(c, err)
			return
		}
		var owner *string
		if !asPublic {
			value := user.ID
			owner = &value
		}
		document, command, err := newKBDocument(user.ID, owner, filepath.Base(header.Filename), "file", nil, c.Query("category"), mimeType, content)
		if err != nil {
			s.failError(c, err)
			return
		}
		if err = s.Control.Repo.CreateKBDocument(c.Request.Context(), document, content); err != nil {
			s.failError(c, err)
			return
		}
		result, err := s.Control.Runtime.ProcessDocument(c.Request.Context(), user.ID, command)
		if err != nil {
			code, message := "RUNTIME_UNAVAILABLE", err.Error()
			_, _ = s.Control.Repo.CompleteKBDocument(context.WithoutCancel(c.Request.Context()), document.DocumentID, domain.DocumentResult{SchemaVersion: "1.0", DocumentID: document.DocumentID, Status: "failed", ErrorCode: &code, Message: message})
			s.failError(c, err)
			return
		}
		stored, err := s.Control.Repo.CompleteKBDocument(c.Request.Context(), document.DocumentID, result)
		if err != nil {
			s.failError(c, err)
			return
		}
		c.JSON(201, gin.H{"success": result.Status == "ready", "source_name": stored.SourceName, "chunk_count": stored.ChunkCount, "message": result.Message})
	})
	router.POST("/api/v2/documents/web", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		var body struct {
			URL      string `json:"url"`
			Category string `json:"category"`
		}
		if err := decodeJSON(c, 1<<20, &body); err != nil {
			s.failError(c, err)
			return
		}
		parsed, err := url.Parse(body.URL)
		if err != nil || parsed.Host == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.User != nil {
			s.failError(c, domain.ErrInvalid)
			return
		}
		asPublic := c.Query("as_public") == "true"
		if asPublic && !user.Admin {
			s.fail(c, 403, "ADMIN_REQUIRED", "仅管理员可写公共知识库")
			return
		}
		var owner *string
		if !asPublic {
			value := user.ID
			owner = &value
		}
		sourceURL := parsed.String()
		document, command, err := newKBDocument(user.ID, owner, sourceURL, "web", &sourceURL, body.Category, "text/html", nil)
		if err != nil {
			s.failError(c, err)
			return
		}
		if err = s.Control.Repo.CreateKBDocument(c.Request.Context(), document, nil); err != nil {
			s.failError(c, err)
			return
		}
		result, err := s.Control.Runtime.ProcessDocument(c.Request.Context(), user.ID, command)
		if err != nil {
			code, message := "RUNTIME_UNAVAILABLE", err.Error()
			_, _ = s.Control.Repo.CompleteKBDocument(context.WithoutCancel(c.Request.Context()), document.DocumentID, domain.DocumentResult{SchemaVersion: "1.0", DocumentID: document.DocumentID, Status: "failed", ErrorCode: &code, Message: message})
			s.failError(c, err)
			return
		}
		stored, err := s.Control.Repo.CompleteKBDocument(c.Request.Context(), document.DocumentID, result)
		if err != nil {
			s.failError(c, err)
			return
		}
		c.JSON(201, gin.H{"success": result.Status == "ready", "source_name": stored.SourceName, "chunk_count": stored.ChunkCount, "message": result.Message})
	})
	router.DELETE("/api/v2/documents/:source_name", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		sourceName, err := url.PathUnescape(c.Param("source_name"))
		if err != nil || sourceName == "" {
			s.failError(c, domain.ErrInvalid)
			return
		}
		var owner *string
		document, err := s.Control.Repo.GetKBDocument(c.Request.Context(), "public", sourceName, nil)
		if err != nil {
			s.failError(c, err)
			return
		}
		if document == nil {
			value := user.ID
			owner = &value
			document, err = s.Control.Repo.GetKBDocument(c.Request.Context(), "private", sourceName, owner)
		}
		if err != nil {
			s.failError(c, err)
			return
		}
		if document == nil {
			s.fail(c, 404, "DOCUMENT_NOT_FOUND", "document not found")
			return
		}
		if document.OwnerID == nil && !user.Admin {
			s.fail(c, 403, "ADMIN_REQUIRED", "仅管理员可删除公共知识库材料")
			return
		}
		command := domain.DocumentProcess{SchemaVersion: "1.0", Operation: "delete", DocumentID: document.DocumentID, VersionID: document.VersionID, ActorID: user.ID, OwnerID: document.OwnerID, SourceName: document.SourceName, SourceType: document.SourceType, SourceURL: document.SourceURL, Category: document.Category, MIMEType: document.MIMEType, SHA256: document.SHA256, ContentBase64: nil}
		result, err := s.Control.Runtime.ProcessDocument(c.Request.Context(), user.ID, command)
		if err != nil {
			s.failError(c, err)
			return
		}
		if result.Status != "deleted" {
			s.fail(c, 502, "DOCUMENT_DELETE_FAILED", "runtime did not confirm deletion")
			return
		}
		deleted, err := s.Control.Repo.DeleteKBDocument(c.Request.Context(), user.ID, document.DocumentID)
		if err != nil {
			s.failError(c, err)
			return
		}
		c.JSON(200, gin.H{"ok": deleted, "source_name": document.SourceName, "deleted_count": result.ChunkCount})
	})
	router.GET("/api/v2/documents/:source_name", func(c *gin.Context) {
		user, ok := s.requireOwner(c)
		if !ok {
			return
		}
		sourceName, err := url.PathUnescape(c.Param("source_name"))
		if err != nil || sourceName == "" {
			s.failError(c, domain.ErrInvalid)
			return
		}
		scope, owner, ok := resolveScope(c, user)
		if !ok {
			return
		}
		document, err := s.Control.Repo.GetKBDocument(c.Request.Context(), scope, sourceName, owner)
		if err != nil {
			s.failError(c, err)
			return
		}
		if document == nil || document.Status != "ready" {
			s.fail(c, 404, "DOCUMENT_NOT_FOUND", "document not found")
			return
		}
		c.JSON(200, document)
	})
}

func newKBDocument(actor string, owner *string, sourceName, sourceType string, sourceURL *string, category, mimeType string, content []byte) (domain.KBDocument, domain.DocumentProcess, error) {
	documentID, err := app.RandomID()
	if err != nil {
		return domain.KBDocument{}, domain.DocumentProcess{}, err
	}
	versionID, err := app.RandomID()
	if err != nil {
		return domain.KBDocument{}, domain.DocumentProcess{}, err
	}
	documentID, versionID = "kb_"+documentID, "kbv_"+versionID
	digestInput := content
	if sourceType == "web" && sourceURL != nil {
		digestInput = []byte(*sourceURL)
	}
	digest := sha256.Sum256(digestInput)
	sha := hex.EncodeToString(digest[:])
	now := float64(time.Now().UnixNano()) / 1e9
	title := sourceName
	document := domain.KBDocument{DocumentID: documentID, VersionID: versionID, OwnerID: owner, SourceName: sourceName, SourceType: sourceType, Title: title, SourceURL: sourceURL, Category: category, MIMEType: mimeType, SHA256: sha, Status: "processing", CreatedAt: now, UpdatedAt: now}
	var encoded *string
	if sourceType == "file" {
		value := base64.StdEncoding.EncodeToString(content)
		encoded = &value
	}
	command := domain.DocumentProcess{SchemaVersion: "1.0", Operation: "ingest", DocumentID: documentID, VersionID: versionID, ActorID: actor, OwnerID: owner, SourceName: sourceName, SourceType: sourceType, SourceURL: sourceURL, Category: category, MIMEType: mimeType, SHA256: sha, ContentBase64: encoded}
	return document, command, nil
}

func readUpload(header *multipart.FileHeader, limit int64) ([]byte, string, error) {
	name := strings.ToLower(filepath.Ext(header.Filename))
	supported := map[string]string{".txt": "text/plain", ".md": "text/markdown", ".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".csv": "text/csv"}
	mimeType, ok := supported[name]
	if !ok {
		return nil, "", domain.ErrUnsupportedDocumentType
	}
	file, err := header.Open()
	if err != nil {
		return nil, "", err
	}
	defer file.Close()
	content, err := io.ReadAll(io.LimitReader(file, limit+1))
	if err != nil {
		return nil, "", err
	}
	if len(content) == 0 {
		return nil, "", domain.ErrInvalidDocumentContent
	}
	if int64(len(content)) > limit {
		return nil, "", domain.ErrUploadTooLarge
	}
	if name == ".pdf" && !strings.HasPrefix(string(content), "%PDF-") {
		return nil, "", domain.ErrInvalidDocumentContent
	}
	return content, mimeType, nil
}

func oneOf(value string, choices ...string) bool {
	for _, choice := range choices {
		if value == choice {
			return true
		}
	}
	return false
}

func DefaultLogger() *slog.Logger { return slog.New(slog.NewJSONHandler(os.Stdout, nil)) }
