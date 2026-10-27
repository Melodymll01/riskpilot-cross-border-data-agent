package api

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"riskpilot/control-plane/internal/app"
	"riskpilot/control-plane/internal/domain"
	"riskpilot/control-plane/internal/infra"
)

type fakeRuntime struct {
	mu         sync.Mutex
	executions []domain.RunExecute
	controls   []domain.RunControl
	documents  []domain.DocumentProcess
	interrupt  bool
	readyError error
}

func (f *fakeRuntime) Execute(_ context.Context, _ string, execute domain.RunExecute, accept func(domain.RunEvent) error) error {
	f.mu.Lock()
	f.executions = append(f.executions, execute)
	interrupt := f.interrupt
	f.mu.Unlock()
	if interrupt {
		payload, _ := json.Marshal(map[string]any{"reason": "checkpoint_saved"})
		return accept(domain.RunEvent{SchemaVersion: "1.0", RunID: execute.RunID, AttemptID: execute.AttemptID, EventSeq: 1, EventType: "interrupted", Payload: payload})
	}
	return emitCompleted(execute.RunID, execute.AttemptID, accept)
}

func (f *fakeRuntime) Control(_ context.Context, _ string, action string, control domain.RunControl, accept func(domain.RunEvent) error) error {
	f.mu.Lock()
	f.controls = append(f.controls, control)
	f.mu.Unlock()
	_ = action
	_ = accept
	return nil
}

func (f *fakeRuntime) Ready(context.Context) error { return f.readyError }

func (f *fakeRuntime) ProcessDocument(_ context.Context, _ string, command domain.DocumentProcess) (domain.DocumentResult, error) {
	f.mu.Lock()
	f.documents = append(f.documents, command)
	f.mu.Unlock()
	status, chunks := "ready", 2
	if command.Operation == "delete" {
		status, chunks = "deleted", 2
	}
	return domain.DocumentResult{SchemaVersion: "1.0", DocumentID: command.DocumentID, Status: status, Title: command.SourceName, ChunkCount: chunks, Message: "ok"}, nil
}

func emitCompleted(runID string, attemptID int, accept func(domain.RunEvent) error) error {
	stage, _ := json.Marshal(map[string]any{"stage": "verification"})
	if err := accept(domain.RunEvent{SchemaVersion: "1.0", RunID: runID, AttemptID: attemptID, EventSeq: 1, EventType: "stage", Payload: stage}); err != nil {
		return err
	}
	usage, _ := json.Marshal(map[string]any{"model_calls": 1, "tool_calls": 0, "input_tokens": 10, "output_tokens": 5})
	if err := accept(domain.RunEvent{SchemaVersion: "1.0", RunID: runID, AttemptID: attemptID, EventSeq: 2, EventType: "usage", Payload: usage}); err != nil {
		return err
	}
	result, _ := json.Marshal(map[string]any{
		"status": "completed", "answer": "最终回答", "citations": []any{},
		"verified_citation_ids": []string{}, "verified_context_message_ids": []string{},
		"clarification_fields": []string{}, "error_type": nil, "end_reason": "completed",
	})
	return accept(domain.RunEvent{SchemaVersion: "1.0", RunID: runID, AttemptID: attemptID, EventSeq: 3, EventType: "result_proposal", Payload: result})
}

func newTestServer(t *testing.T, runtime *fakeRuntime) (*Server, *infra.Store) {
	t.Helper()
	store, err := infra.OpenStore(filepath.Join(t.TempDir(), "control.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.DB.Close() })
	cfg := app.Config{
		RuntimeURL: "http://127.0.0.1:9", Frontend: t.TempDir(), JWTSecret: strings.Repeat("session-", 8),
		ServiceSecret: strings.Repeat("service-", 8), CookieName: "copilot_session", CookieSameSite: "lax",
		SessionTTL: time.Hour, MaxActive: 4, MaxUploadBytes: 1 << 20, RuntimeTimeout: 5 * time.Second,
		RunTimeout: 30 * time.Second, MaxModelCalls: 12, MaxToolCalls: 6, RatePerMinute: 100,
		AdminIDs: []string{"github:alice"},
	}
	server, err := New(cfg, store, runtime, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatal(err)
	}
	return server, store
}

func session(t *testing.T, server *Server, owner string) *http.Cookie {
	t.Helper()
	now := float64(time.Now().UnixNano()) / 1e9
	provider := "github"
	if strings.HasPrefix(owner, "anon:") {
		provider = "anonymous"
	}
	_, err := server.Auth.Store.SaveUser(context.Background(), domain.User{ID: owner, Provider: provider, ProviderID: owner, Name: "test", Created: now, Active: now})
	if err != nil {
		t.Fatal(err)
	}
	token, err := server.Auth.Token(owner)
	if err != nil {
		t.Fatal(err)
	}
	return &http.Cookie{Name: server.Config.CookieName, Value: token}
}

func request(t *testing.T, handler http.Handler, method, path string, body any, cookie *http.Cookie, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	var reader io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		reader = bytes.NewReader(encoded)
	}
	req := httptest.NewRequest(method, path, reader)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if cookie != nil {
		req.AddCookie(cookie)
	}
	for key, value := range headers {
		req.Header.Set(key, value)
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, req)
	return response
}

func TestChatStreamOwnsTaskRunEventsAndAtomicAnswer(t *testing.T) {
	runtime := &fakeRuntime{}
	server, store := newTestServer(t, runtime)
	cookie := session(t, server, "github:alice")
	response := request(t, server.Router(), http.MethodPost, "/api/v2/copilot/chat/stream", map[string]any{"message": "分析 PIPL"}, cookie, map[string]string{"Idempotency-Key": "chat-1"})
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	body := response.Body.String()
	for _, event := range []string{"event: task_created", "event: run_started", "event: run_status", "event: answer"} {
		if !strings.Contains(body, event) {
			t.Fatalf("missing %s in %s", event, body)
		}
	}
	var runCount, userMessages, answers, eventCount int
	for query, target := range map[string]*int{
		"SELECT COUNT(*) FROM conversation_runs":               &runCount,
		"SELECT COUNT(*) FROM messages WHERE role='user'":      &userMessages,
		"SELECT COUNT(*) FROM messages WHERE role='assistant'": &answers,
		"SELECT COUNT(*) FROM run_attempt_events":              &eventCount,
	} {
		if err := store.DB.QueryRow(query).Scan(target); err != nil {
			t.Fatal(err)
		}
	}
	if runCount != 1 || userMessages != 1 || answers != 1 || eventCount != 3 {
		t.Fatalf("run=%d user=%d answer=%d events=%d", runCount, userMessages, answers, eventCount)
	}
	var status string
	var answerID *string
	if err := store.DB.QueryRow("SELECT status,answer_message_id FROM conversation_runs").Scan(&status, &answerID); err != nil {
		t.Fatal(err)
	}
	if status != "completed" || answerID == nil {
		t.Fatalf("status=%s answer=%v", status, answerID)
	}
}

func TestIdempotencyReplaysWithoutDuplicateExecution(t *testing.T) {
	runtime := &fakeRuntime{}
	server, store := newTestServer(t, runtime)
	cookie := session(t, server, "github:alice")
	handler := server.Router()
	headers := map[string]string{"Idempotency-Key": "same-key"}
	first := request(t, handler, http.MethodPost, "/api/v2/copilot/chat", map[string]any{"message": "same"}, cookie, headers)
	second := request(t, handler, http.MethodPost, "/api/v2/copilot/chat", map[string]any{"message": "same"}, cookie, headers)
	if first.Code != 200 || second.Code != 200 {
		t.Fatalf("first=%d second=%d second_body=%s", first.Code, second.Code, second.Body.String())
	}
	var runs, messages int
	_ = store.DB.QueryRow("SELECT COUNT(*) FROM conversation_runs").Scan(&runs)
	_ = store.DB.QueryRow("SELECT COUNT(*) FROM messages").Scan(&messages)
	if runs != 1 || messages != 2 {
		t.Fatalf("runs=%d messages=%d", runs, messages)
	}
	runtime.mu.Lock()
	executions := len(runtime.executions)
	runtime.mu.Unlock()
	if executions != 1 {
		t.Fatalf("runtime executions=%d", executions)
	}
	conflict := request(t, handler, http.MethodPost, "/api/v2/copilot/chat", map[string]any{"message": "different"}, cookie, headers)
	if conflict.Code != http.StatusConflict {
		t.Fatalf("status=%d body=%s", conflict.Code, conflict.Body.String())
	}
}

func TestInterruptedRunResumeIncrementsAttemptAndCompletes(t *testing.T) {
	runtime := &fakeRuntime{interrupt: true}
	server, _ := newTestServer(t, runtime)
	cookie := session(t, server, "github:alice")
	handler := server.Router()
	created := request(t, handler, http.MethodPost, "/api/v2/copilot/chat", map[string]any{"message": "pause"}, cookie, nil)
	if created.Code != 200 {
		t.Fatal(created.Body.String())
	}
	var body struct {
		RunID string `json:"run_id"`
	}
	if err := json.Unmarshal(created.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	runtime.mu.Lock()
	runtime.interrupt = false
	runtime.mu.Unlock()
	resumed := request(t, handler, http.MethodPost, "/api/v2/copilot/runs/"+body.RunID+"/resume", nil, cookie, nil)
	if resumed.Code != 200 || !strings.Contains(resumed.Body.String(), `"attempt_id":2`) || !strings.Contains(resumed.Body.String(), "event: answer") {
		t.Fatalf("status=%d body=%s", resumed.Code, resumed.Body.String())
	}
	status := request(t, handler, http.MethodGet, "/api/v2/copilot/runs/"+body.RunID, nil, cookie, nil)
	if status.Code != 200 || !strings.Contains(status.Body.String(), `"status":"completed"`) {
		t.Fatal(status.Body.String())
	}
}

func TestCancelIsOwnerScopedAndIdempotent(t *testing.T) {
	runtime := &fakeRuntime{interrupt: true}
	server, _ := newTestServer(t, runtime)
	alice := session(t, server, "github:alice")
	bob := session(t, server, "github:bob")
	handler := server.Router()
	created := request(t, handler, http.MethodPost, "/api/v2/copilot/chat", map[string]any{"message": "pause"}, alice, nil)
	var body struct {
		RunID string `json:"run_id"`
	}
	_ = json.Unmarshal(created.Body.Bytes(), &body)
	if outsider := request(t, handler, http.MethodPost, "/api/v2/copilot/runs/"+body.RunID+"/cancel", nil, bob, nil); outsider.Code != 404 {
		t.Fatalf("outsider status=%d body=%s", outsider.Code, outsider.Body.String())
	}
	for i := 0; i < 2; i++ {
		response := request(t, handler, http.MethodPost, "/api/v2/copilot/runs/"+body.RunID+"/cancel", nil, alice, nil)
		if response.Code != 200 || !strings.Contains(response.Body.String(), `"status":"cancelled"`) {
			t.Fatalf("cancel %d status=%d body=%s", i, response.Code, response.Body.String())
		}
	}
}

func TestUnknownBusinessRouteIsNotForwarded(t *testing.T) {
	server, _ := newTestServer(t, &fakeRuntime{})
	cookie := session(t, server, "github:alice")
	response := request(t, server.Router(), http.MethodPost, "/api/v2/unknown", map[string]any{}, cookie, nil)
	if response.Code != 404 || !strings.Contains(response.Body.String(), "NOT_FOUND") {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
}

func TestCompletedRunResumeReturnsJSONConflictBeforeSSE(t *testing.T) {
	server, _ := newTestServer(t, &fakeRuntime{})
	cookie := session(t, server, "github:alice")
	handler := server.Router()
	created := request(t, handler, http.MethodPost, "/api/v2/copilot/chat", map[string]any{"message": "done"}, cookie, nil)
	var body struct {
		RunID string `json:"run_id"`
	}
	_ = json.Unmarshal(created.Body.Bytes(), &body)
	response := request(t, handler, http.MethodPost, "/api/v2/copilot/runs/"+body.RunID+"/resume", nil, cookie, nil)
	if response.Code != http.StatusConflict || !strings.HasPrefix(response.Header().Get("Content-Type"), "application/json") {
		t.Fatalf("status=%d content-type=%s body=%s", response.Code, response.Header().Get("Content-Type"), response.Body.String())
	}
}

func TestDocumentsAreExplicitCatalogRoutesWithInternalProcessing(t *testing.T) {
	runtime := &fakeRuntime{}
	server, _ := newTestServer(t, runtime)
	cookie := session(t, server, "github:alice")
	handler := server.Router()
	var upload bytes.Buffer
	writer := multipart.NewWriter(&upload)
	part, err := writer.CreateFormFile("file", "policy.md")
	if err != nil {
		t.Fatal(err)
	}
	_, _ = part.Write([]byte("# policy"))
	_ = writer.Close()
	req := httptest.NewRequest(http.MethodPost, "/api/v2/documents/file?as_public=true&category=%E6%B3%95%E8%A7%84", &upload)
	req.Header.Set("Content-Type", writer.FormDataContentType())
	req.AddCookie(cookie)
	created := httptest.NewRecorder()
	handler.ServeHTTP(created, req)
	if created.Code != 201 || !strings.Contains(created.Body.String(), `"chunk_count":2`) {
		t.Fatalf("status=%d body=%s", created.Code, created.Body.String())
	}
	listed := request(t, handler, http.MethodGet, "/api/v2/documents?scope=public", nil, cookie, nil)
	stats := request(t, handler, http.MethodGet, "/api/v2/documents/stats?scope=public", nil, cookie, nil)
	detail := request(t, handler, http.MethodGet, "/api/v2/documents/policy.md?scope=public", nil, cookie, nil)
	if listed.Code != 200 || !strings.Contains(listed.Body.String(), "policy.md") || stats.Code != 200 || !strings.Contains(stats.Body.String(), `"chunk_count":2`) || detail.Code != 200 {
		t.Fatalf("list=%d %s stats=%d %s detail=%d %s", listed.Code, listed.Body.String(), stats.Code, stats.Body.String(), detail.Code, detail.Body.String())
	}
	deleted := request(t, handler, http.MethodDelete, "/api/v2/documents/policy.md", nil, cookie, nil)
	if deleted.Code != 200 || !strings.Contains(deleted.Body.String(), `"deleted_count":2`) {
		t.Fatalf("status=%d body=%s", deleted.Code, deleted.Body.String())
	}
	runtime.mu.Lock()
	commands := append([]domain.DocumentProcess(nil), runtime.documents...)
	runtime.mu.Unlock()
	if len(commands) != 2 || commands[0].Operation != "ingest" || commands[0].ContentBase64 == nil || commands[1].Operation != "delete" || commands[1].ContentBase64 != nil {
		t.Fatalf("commands=%+v", commands)
	}
}
