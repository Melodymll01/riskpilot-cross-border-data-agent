package infra

import (
	"context"
	"encoding/json"
	"errors"
	"path/filepath"
	"testing"
	"time"

	"riskpilot/control-plane/internal/app"
	"riskpilot/control-plane/internal/domain"
)

func openControlStore(t *testing.T) *Store {
	t.Helper()
	store, err := OpenStore(filepath.Join(t.TempDir(), "control.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.DB.Close() })
	return store
}

func TestRunEventSequenceAndFinalAnswerTransaction(t *testing.T) {
	store := openControlStore(t)
	ctx := context.Background()
	begin, err := store.BeginRun(ctx, "github:alice", nil, "question", nil, "key", "hash", app.RunPolicy{MaxDurationSeconds: 30, MaxModelCalls: 3, MaxToolCalls: 2})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = store.RunExecution(ctx, begin.Run); err != nil {
		t.Fatal(err)
	}
	stage, _ := json.Marshal(map[string]any{"stage": "agent"})
	_, _, _, err = store.ApplyRunEvent(ctx, domain.RunEvent{SchemaVersion: "1.0", RunID: begin.Run.RunID, AttemptID: 1, EventSeq: 2, EventType: "stage", Payload: stage})
	if !errors.Is(err, domain.ErrEventSequence) {
		t.Fatalf("out-of-order err=%v", err)
	}
	invalid, _ := json.Marshal(map[string]any{"status": "completed", "answer": "", "citations": []any{}, "verified_citation_ids": []string{}, "verified_context_message_ids": []string{}, "clarification_fields": []string{}, "error_type": nil, "end_reason": "done"})
	_, _, _, err = store.ApplyRunEvent(ctx, domain.RunEvent{SchemaVersion: "1.0", RunID: begin.Run.RunID, AttemptID: 1, EventSeq: 1, EventType: "result_proposal", Payload: invalid})
	if !errors.Is(err, domain.ErrInvalid) {
		t.Fatalf("invalid result err=%v", err)
	}
	var eventCount, messageCount int
	_ = store.DB.QueryRow(`SELECT COUNT(*) FROM run_attempt_events`).Scan(&eventCount)
	_ = store.DB.QueryRow(`SELECT COUNT(*) FROM messages`).Scan(&messageCount)
	if eventCount != 0 || messageCount != 1 {
		t.Fatalf("invalid event leaked partial state events=%d messages=%d", eventCount, messageCount)
	}
	proposal := map[string]any{
		"status": "completed", "answer": "answer",
		"citations":             []any{map[string]any{"citation_id": "cite-1", "source_type": "law", "source_name": "PIPL", "extra_runtime_metadata": "allowed-by-frozen-schema"}},
		"verified_citation_ids": []string{"cite-1"}, "verified_context_message_ids": []string{},
		"clarification_fields": []string{}, "error_type": nil, "end_reason": "completed",
	}
	payload, _ := json.Marshal(proposal)
	run, answer, accepted, err := store.ApplyRunEvent(ctx, domain.RunEvent{SchemaVersion: "1.0", RunID: begin.Run.RunID, AttemptID: 1, EventSeq: 1, EventType: "result_proposal", Payload: payload})
	if err != nil || !accepted || answer == nil || run.Status != "completed" || run.AnswerMessageID == nil {
		t.Fatalf("run=%+v answer=%+v accepted=%v err=%v", run, answer, accepted, err)
	}
	var citations int
	_ = store.DB.QueryRow(`SELECT COUNT(*) FROM message_citations WHERE message_id=?`, answer.MessageID).Scan(&citations)
	if citations != 1 {
		t.Fatalf("citations=%d", citations)
	}
	_, _, accepted, err = store.ApplyRunEvent(ctx, domain.RunEvent{SchemaVersion: "1.0", RunID: begin.Run.RunID, AttemptID: 1, EventSeq: 1, EventType: "result_proposal", Payload: payload})
	if err != nil || accepted {
		t.Fatalf("exact replay accepted=%v err=%v", accepted, err)
	}
}

func TestResumeCopiesFrozenExecuteSnapshotIntoNewAttempt(t *testing.T) {
	store := openControlStore(t)
	ctx := context.Background()
	begin, err := store.BeginRun(ctx, "github:alice", nil, "question", nil, "resume-key", "resume-hash", app.RunPolicy{MaxDurationSeconds: 30, MaxModelCalls: 3, MaxToolCalls: 2})
	if err != nil {
		t.Fatal(err)
	}
	first, err := store.RunExecution(ctx, begin.Run)
	if err != nil {
		t.Fatal(err)
	}
	interrupted, _ := json.Marshal(map[string]any{"stage": "agent"})
	if _, _, _, err = store.ApplyRunEvent(ctx, domain.RunEvent{SchemaVersion: "1.0", RunID: begin.Run.RunID, AttemptID: 1, EventSeq: 1, EventType: "interrupted", Payload: interrupted}); err != nil {
		t.Fatal(err)
	}
	// A concurrent write after interruption must not alter the frozen context replayed to runtime.
	_, err = store.DB.Exec(`INSERT INTO messages(msg_id,task_id,role,content,citations,created_at) VALUES('late',?,'user','late message','[]',?)`, begin.Run.TaskID, time.Now().UnixNano()/1e9)
	if err != nil {
		t.Fatal(err)
	}
	resumedRun, err := store.PrepareResume(ctx, "github:alice", begin.Run.RunID)
	if err != nil {
		t.Fatal(err)
	}
	second, err := store.RunExecution(ctx, resumedRun)
	if err != nil {
		t.Fatal(err)
	}
	if second.AttemptID != 2 || !second.Resume || second.ThreadID != first.ThreadID || second.Question != first.Question || len(second.RecentMessages) != len(first.RecentMessages) {
		t.Fatalf("first=%+v second=%+v", first, second)
	}
	if second.Budget.DeadlineAt <= first.Budget.DeadlineAt {
		t.Fatalf("resume deadline was not renewed first=%f second=%f", first.Budget.DeadlineAt, second.Budget.DeadlineAt)
	}
}
