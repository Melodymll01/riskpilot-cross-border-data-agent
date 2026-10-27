package infra

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"riskpilot/control-plane/internal/domain"
)

func TestRuntimeClientUsesStrictInternalPathsAndPrincipalBinding(t *testing.T) {
	secret := strings.Repeat("internal-", 8)
	paths := []string{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.URL.Path)
		claims := jwt.MapClaims{}
		_, err := jwt.ParseWithClaims(r.Header.Get(IdentityHeader), claims, func(*jwt.Token) (any, error) { return []byte(secret), nil }, jwt.WithIssuer("riskpilot-go"), jwt.WithAudience("riskpilot-agent"), jwt.WithValidMethods([]string{"HS256"}), jwt.WithExpirationRequired())
		if err != nil {
			t.Errorf("principal: %v", err)
		}
		wantSubject := "github:alice"
		if r.Method == http.MethodGet {
			wantSubject = "system:readiness"
		}
		if claims["sub"] != wantSubject || claims["method"] != r.Method || claims["target"] != r.URL.RequestURI() {
			t.Errorf("claims=%v request=%s %s", claims, r.Method, r.URL.RequestURI())
		}
		switch r.URL.Path {
		case "/internal/v1/runs/execute":
			if r.Method == http.MethodGet {
				w.WriteHeader(http.StatusMethodNotAllowed)
				return
			}
			w.Header().Set("Content-Type", "application/x-ndjson")
			_, _ = w.Write([]byte(`{"schema_version":"1.0","run_id":"run-1","attempt_id":2,"event_seq":1,"event_type":"stage","payload":{"stage":"agent"}}` + "\n"))
		case "/internal/v1/runs/run-1/cancel":
			w.WriteHeader(http.StatusNoContent)
		case "/internal/v1/documents/process":
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(domain.DocumentResult{SchemaVersion: "1.0", DocumentID: "kb-1", Status: "ready", Title: "Policy", ChunkCount: 3, Message: "ok"})
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	client, err := NewRuntimeClient(server.URL, secret, 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	execute := domain.RunExecute{SchemaVersion: "1.0", RunID: "run-1", AttemptID: 2, ThreadID: "thread-1", OwnerID: "github:alice", TaskID: "task-1", InputMessageID: "msg-1", Question: "q", AttachmentVersions: []domain.AttachmentVersion{}, MemorySnapshot: []domain.MemoryReference{}, RecentMessages: []domain.ContextMessage{}, Resume: true, Budget: domain.RunBudget{DeadlineAt: float64(time.Now().Add(time.Minute).Unix()), MaxModelCalls: 2, MaxToolCalls: 2}}
	seen := 0
	if err = client.Execute(context.Background(), "github:alice", execute, func(event domain.RunEvent) error { seen++; return nil }); err != nil {
		t.Fatal(err)
	}
	if seen != 1 {
		t.Fatalf("events=%d", seen)
	}
	control := domain.RunControl{SchemaVersion: "1.0", RunID: "run-1", AttemptID: 2, Reason: "user_cancelled"}
	if err = client.Control(context.Background(), "github:alice", "cancel", control, func(domain.RunEvent) error { return nil }); err != nil {
		t.Fatal(err)
	}
	command := domain.DocumentProcess{SchemaVersion: "1.0", Operation: "ingest", DocumentID: "kb-1", VersionID: "kbv-1", ActorID: "github:alice", SourceName: "policy.md", SourceType: "file", Category: "law", MIMEType: "text/markdown", SHA256: strings.Repeat("a", 64)}
	result, err := client.ProcessDocument(context.Background(), "github:alice", command)
	if err != nil || result.ChunkCount != 3 {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	if err = client.Ready(context.Background()); err != nil {
		t.Fatal(err)
	}
	want := []string{"/internal/v1/runs/execute", "/internal/v1/runs/run-1/cancel", "/internal/v1/documents/process", "/internal/v1/runs/execute"}
	if strings.Join(paths, "|") != strings.Join(want, "|") {
		t.Fatalf("paths=%v", paths)
	}
}

func TestRuntimeClientRejectsUnexpectedRunEventFields(t *testing.T) {
	secret := strings.Repeat("internal-", 8)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"schema_version":"1.0","run_id":"run-1","attempt_id":1,"event_seq":1,"event_type":"stage","payload":{"stage":"agent"},"unexpected":true}` + "\n"))
	}))
	defer server.Close()
	client, err := NewRuntimeClient(server.URL, secret, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	execute := domain.RunExecute{SchemaVersion: "1.0", RunID: "run-1", AttemptID: 1, ThreadID: "thread-1", OwnerID: "github:alice", TaskID: "task-1", InputMessageID: "msg-1", Question: "q", AttachmentVersions: []domain.AttachmentVersion{}, MemorySnapshot: []domain.MemoryReference{}, Budget: domain.RunBudget{DeadlineAt: 1, MaxModelCalls: 1, MaxToolCalls: 1}}
	if err = client.Execute(context.Background(), "github:alice", execute, func(domain.RunEvent) error { return nil }); err == nil {
		t.Fatal("event with extra field was accepted")
	}
}
