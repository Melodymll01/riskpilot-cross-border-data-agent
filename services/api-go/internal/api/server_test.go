package api

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"riskpilot/api-go/internal/config"
	"riskpilot/api-go/internal/domain"
	"riskpilot/api-go/internal/infra"
)

func newTestServer(t *testing.T, upstream string) *Server {
	t.Helper()
	store, err := infra.OpenStore(filepath.Join(t.TempDir(), "api.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { store.DB.Close() })
	cfg := config.Config{PythonURL: upstream, Database: "", Frontend: t.TempDir(), JWTSecret: strings.Repeat("session-", 8), ServiceSecret: strings.Repeat("service-", 8), CookieName: "copilot_session", CookieSameSite: "lax", SessionTTL: time.Hour, MaxActive: 2, MaxUploadBytes: 1 << 20, UpstreamTimeout: 5 * time.Second, RatePerMinute: 60}
	server, err := New(cfg, store, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatal(err)
	}
	return server
}
func call(handler http.Handler, method, path string, cookies ...*http.Cookie) *httptest.ResponseRecorder {
	request := httptest.NewRequest(method, path, nil)
	for _, cookie := range cookies {
		request.AddCookie(cookie)
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	return response
}
func session(t *testing.T, s *Server, id string) *http.Cookie {
	t.Helper()
	now := float64(time.Now().Unix())
	_, err := s.Auth.Store.SaveUser(context.Background(), domain.User{ID: id, Provider: "github", ProviderID: id, Name: "test", Created: now, Active: now})
	if err != nil {
		t.Fatal(err)
	}
	token, err := s.Auth.Token(id)
	if err != nil {
		t.Fatal(err)
	}
	return &http.Cookie{Name: s.Config.CookieName, Value: token}
}

func TestAnonymousMeLogoutAndLegacySession(t *testing.T) {
	s := newTestServer(t, "http://127.0.0.1:9")
	router := s.Router()
	response := call(router, "POST", "/api/v2/auth/anonymous")
	if response.Code != 201 {
		t.Fatalf("%d %s", response.Code, response.Body)
	}
	cookies := response.Result().Cookies()
	if len(cookies) != 1 || !cookies[0].HttpOnly || cookies[0].SameSite != http.SameSiteLaxMode {
		t.Fatal("session cookie properties")
	}
	me := call(router, "GET", "/api/v2/auth/me", cookies[0])
	if me.Code != 200 || !strings.Contains(me.Body.String(), `"authenticated":true`) {
		t.Fatal(me.Body)
	}
	if call(router, "POST", "/api/v2/auth/logout", cookies[0]).Result().Cookies()[0].MaxAge != -1 {
		t.Fatal("logout did not clear cookie")
	}
	if strings.Contains(call(router, "GET", "/api/v2/auth/me").Body.String(), `"authenticated":true`) {
		t.Fatal("unexpected authentication")
	}
	// Python's original session has sub/iat/exp and no issuer. It remains accepted.
	legacy, err := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.MapClaims{"sub": jsonUserID(t, response), "iat": time.Now().Unix(), "exp": time.Now().Add(time.Hour).Unix()}).SignedString([]byte(s.Config.JWTSecret))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(call(router, "GET", "/api/v2/auth/me", &http.Cookie{Name: s.Config.CookieName, Value: legacy}).Body.String(), `"authenticated":true`) {
		t.Fatal("legacy session rejected")
	}
}
func jsonUserID(t *testing.T, response *httptest.ResponseRecorder) string {
	t.Helper()
	var data struct {
		User domain.User `json:"user"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &data); err != nil {
		t.Fatal(err)
	}
	return data.User.ID
}

func TestProxyIdentityBindingAndHeaderStripping(t *testing.T) {
	var s *Server
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		claims := jwt.MapClaims{}
		_, err := jwt.ParseWithClaims(r.Header.Get(IdentityHeader), claims, func(*jwt.Token) (any, error) { return []byte(s.Config.ServiceSecret), nil }, jwt.WithIssuer("riskpilot-go"), jwt.WithAudience("riskpilot-agent"), jwt.WithExpirationRequired(), jwt.WithValidMethods([]string{"HS256"}))
		if err != nil {
			t.Errorf("identity: %v", err)
		}
		if claims["sub"] != "github:alice" || claims["target"] != "/api/v2/memory?query=%E5%AD%A6%E7%94%9F" || claims["method"] != "GET" {
			t.Errorf("claims: %v", claims)
		}
		if r.Header.Get("Cookie") != "" || r.Header.Get("Authorization") != "" || r.Header.Get("X-Riskpilot-Owner") != "" {
			t.Error("browser credentials forwarded")
		}
		http.SetCookie(w, &http.Cookie{Name: "should-not-leak", Value: "secret"})
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"memories":[]}`)
	}))
	defer upstream.Close()
	s = newTestServer(t, upstream.URL)
	cookie := session(t, s, "github:alice")
	router := s.Router()
	request := httptest.NewRequest("GET", "/api/v2/memory?query=%E5%AD%A6%E7%94%9F", nil)
	request.AddCookie(cookie)
	request.Header.Set(IdentityHeader, "forged")
	request.Header.Set("X-Riskpilot-Owner", "github:bob")
	request.Header.Set("Authorization", "Bearer leak")
	response := httptest.NewRecorder()
	router.ServeHTTP(response, request)
	if response.Code != 200 || len(response.Result().Cookies()) != 0 {
		t.Fatalf("%d %s", response.Code, response.Body)
	}
	if call(router, "GET", "/api/v2/memory").Code != 401 {
		t.Fatal("unauthenticated business request allowed")
	}
}

func TestOAuthBrowserBindingRenameAndReplay(t *testing.T) {
	id := int64(123)
	login := "new-login"
	calls := 0
	github := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/token" {
			calls++
			io.WriteString(w, `{"access_token":"mock-github-token"}`)
			return
		}
		if r.Header.Get("Authorization") != "Bearer mock-github-token" {
			t.Error("missing access token")
		}
		json.NewEncoder(w).Encode(map[string]any{"id": id, "login": login, "name": "Renamed User"})
	}))
	defer github.Close()
	s := newTestServer(t, "http://127.0.0.1:9")
	s.Auth.Config.ClientID = "test-client"
	s.Auth.Config.ClientSecret = "test-secret"
	s.Auth.Config.AuthorizeURL = github.URL + "/authorize"
	s.Auth.Config.TokenURL = github.URL + "/token"
	s.Auth.Config.UserURL = github.URL + "/user"
	now := float64(time.Now().Unix())
	_, err := s.Auth.Store.SaveUser(context.Background(), domain.User{ID: "github:old-login", Provider: "github", ProviderID: "123", Name: "Old", Created: now, Active: now})
	if err != nil {
		t.Fatal(err)
	}
	router := s.Router()
	begin := call(router, "GET", "/api/v2/auth/github/login")
	var data map[string]string
	json.Unmarshal(begin.Body.Bytes(), &data)
	bound := begin.Result().Cookies()[0]
	callback := "/api/v2/auth/github/callback?code=valid&state=" + data["state"]
	if call(router, "GET", callback).Code != 400 || calls != 0 {
		t.Fatal("OAuth callback accepted without browser state")
	}
	response := call(router, "GET", callback, bound)
	if response.Code != 303 {
		t.Fatal(response.Body)
	}
	user, err := s.Auth.Store.User(context.Background(), "github:old-login")
	if err != nil || user.Name != "Renamed User" {
		t.Fatal("rename lost account identity")
	}
	if call(router, "GET", callback, bound).Code != 400 || calls != 1 {
		t.Fatal("OAuth replay accepted")
	}
}

func TestCrossOriginUnknownAuthAndUnavailableUpstream(t *testing.T) {
	s := newTestServer(t, "http://127.0.0.1:9")
	router := s.Router()
	request := httptest.NewRequest("POST", "/api/v2/auth/anonymous", nil)
	request.Header.Set("Origin", "https://attacker.test")
	response := httptest.NewRecorder()
	router.ServeHTTP(response, request)
	if response.Code != 403 {
		t.Fatal("cross-origin write accepted")
	}
	if call(router, "GET", "/api/v2/auth/unknown").Code != 404 {
		t.Fatal("unknown auth proxied")
	}
	if call(router, "GET", "/api/v2/memory", session(t, s, "github:alice")).Code != 502 {
		t.Fatal("upstream failure not reported")
	}
	if call(router, "GET", "/api/v2/health/ready").Code != 503 {
		t.Fatal("unavailable service declared ready")
	}
}

func TestSSEFlushAndClientCancelReachUpstream(t *testing.T) {
	cancelled := make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		io.WriteString(w, "event: run_started\ndata: {\"run_id\":\"run-test\"}\n\n")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
		close(cancelled)
	}))
	defer upstream.Close()
	s := newTestServer(t, upstream.URL)
	cookie := session(t, s, "github:alice")
	gateway := httptest.NewServer(s.Router())
	defer gateway.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	request, _ := http.NewRequestWithContext(ctx, "GET", gateway.URL+"/api/v2/copilot/runs/test/resume", nil)
	request.AddCookie(cookie)
	client := http.Client{Timeout: 2 * time.Second}
	response, err := client.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	data := make([]byte, 19)
	if _, err = io.ReadFull(response.Body, data); err != nil {
		t.Fatal(err)
	}
	if string(data) != "event: run_started\n" {
		t.Fatalf("first SSE bytes: %q", data)
	}
	cancel()
	response.Body.Close()
	select {
	case <-cancelled:
	case <-time.After(time.Second):
		t.Fatal("client cancellation did not reach upstream")
	}
}

func TestCapacityAndRateLimits(t *testing.T) {
	s := newTestServer(t, "http://127.0.0.1:9")
	cookie := session(t, s, "github:alice")
	router := s.Router()
	s.active <- struct{}{}
	s.active <- struct{}{}
	if call(router, "GET", "/api/v2/memory", cookie).Code != 503 {
		t.Fatal("capacity was unbounded")
	}
	<-s.active
	<-s.active
	s.Config.RateEnabled = true
	s.Config.RatePerMinute = 1
	if call(router, "GET", "/api/v2/auth/me").Code != 200 || call(router, "GET", "/api/v2/auth/me").Code != 429 {
		t.Fatal("auth rate limit missing")
	}
}
