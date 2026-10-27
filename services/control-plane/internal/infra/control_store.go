package infra

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"
	"unicode/utf8"

	"riskpilot/control-plane/internal/app"
	"riskpilot/control-plane/internal/domain"
)

func newID(prefix string) (string, error) {
	raw := make([]byte, 16)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	return prefix + hex.EncodeToString(raw), nil
}

func keyDigest(value string) string {
	digest := sha256.Sum256([]byte(value))
	return hex.EncodeToString(digest[:])
}

func jsonText(value any) (string, error) {
	data, err := json.Marshal(value)
	return string(data), err
}

func titleFromQuestion(question string) string {
	runes := []rune(strings.TrimSpace(question))
	if len(runes) > 32 {
		runes = runes[:32]
	}
	if len(runes) == 0 {
		return "新对话"
	}
	return string(runes)
}

func (s *Store) BeginRun(ctx context.Context, owner string, taskID *string, question string, attachmentIDs []string, idempotencyKey, requestHash string, policy app.RunPolicy) (domain.BeginRun, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return domain.BeginRun{}, err
	}
	defer tx.Rollback()
	keyHash := keyDigest(idempotencyKey)
	var existingRunID, existingHash string
	err = tx.QueryRowContext(ctx, "SELECT run_id,request_hash FROM control_idempotency WHERE owner_id=? AND key_hash=?", owner, keyHash).Scan(&existingRunID, &existingHash)
	if err == nil {
		if existingHash != requestHash {
			return domain.BeginRun{}, domain.ErrIdempotency
		}
		run, loadErr := getRunTx(ctx, tx, owner, existingRunID)
		if loadErr != nil {
			return domain.BeginRun{}, loadErr
		}
		task, loadErr := getTaskTx(ctx, tx, owner, run.TaskID)
		if loadErr != nil {
			return domain.BeginRun{}, loadErr
		}
		events, loadErr := listRunEventsTx(ctx, tx, run.RunID, run.AttemptID)
		if loadErr != nil {
			return domain.BeginRun{}, loadErr
		}
		return domain.BeginRun{Task: task, Run: run, Existing: true, Events: events}, nil
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return domain.BeginRun{}, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	taskCreated := false
	var task domain.Task
	if taskID == nil || *taskID == "" {
		id, idErr := newID("task_")
		if idErr != nil {
			return domain.BeginRun{}, idErr
		}
		task = domain.Task{TaskID: id, OwnerID: owner, Title: titleFromQuestion(question), State: "planning", UserGoal: "", CollectedFacts: map[string]any{}, CreatedAt: now, UpdatedAt: now}
		facts, _ := jsonText(task.CollectedFacts)
		_, err = tx.ExecContext(ctx, `INSERT INTO tasks(task_id,owner_id,title,state,user_goal,collected_facts,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)`, task.TaskID, task.OwnerID, task.Title, task.State, task.UserGoal, facts, task.CreatedAt, task.UpdatedAt)
		if err != nil {
			return domain.BeginRun{}, err
		}
		taskCreated = true
	} else {
		task, err = getTaskTx(ctx, tx, owner, *taskID)
		if err != nil {
			return domain.BeginRun{}, err
		}
		var activeRuns int
		if err = tx.QueryRowContext(ctx, `SELECT COUNT(*) FROM conversation_runs WHERE task_id=? AND owner_id=? AND status IN ('running','interrupted','cancel_requested')`, task.TaskID, owner).Scan(&activeRuns); err != nil {
			return domain.BeginRun{}, err
		}
		if activeRuns > 0 {
			return domain.BeginRun{}, fmt.Errorf("%w: task already has an active run", domain.ErrConflict)
		}
	}
	messageID, err := newID("msg_")
	if err != nil {
		return domain.BeginRun{}, err
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO messages(msg_id,task_id,role,content,tool_call_id,citations,created_at) VALUES(?,?, 'user', ?,NULL,'[]',?)`, messageID, task.TaskID, question, now)
	if err != nil {
		return domain.BeginRun{}, err
	}
	if _, err = tx.ExecContext(ctx, "UPDATE tasks SET updated_at=? WHERE task_id=?", now, task.TaskID); err != nil {
		return domain.BeginRun{}, err
	}
	if _, err = tx.ExecContext(ctx, "DELETE FROM task_attachments WHERE task_id=?", task.TaskID); err != nil {
		return domain.BeginRun{}, err
	}
	attachmentVersions := make([]domain.AttachmentVersion, 0, len(attachmentIDs))
	for position, documentID := range attachmentIDs {
		var versionID, sha string
		err = tx.QueryRowContext(ctx, `SELECT d.current_version_id,v.sha256 FROM chat_documents d JOIN chat_document_versions v ON v.version_id=d.current_version_id WHERE d.document_id=? AND d.owner_id=? AND d.status!='deleted'`, documentID, owner).Scan(&versionID, &sha)
		if errors.Is(err, sql.ErrNoRows) {
			return domain.BeginRun{}, fmt.Errorf("%w: attachment %s", domain.ErrNotFound, documentID)
		}
		if err != nil {
			return domain.BeginRun{}, err
		}
		attachmentVersions = append(attachmentVersions, domain.AttachmentVersion{DocumentID: documentID, VersionID: versionID, SHA256: sha})
		if _, err = tx.ExecContext(ctx, "INSERT INTO task_attachments(task_id,document_id,selected_at,selection_order) VALUES(?,?,?,?)", task.TaskID, documentID, now, position); err != nil {
			return domain.BeginRun{}, err
		}
	}
	runID, err := newID("run_")
	if err != nil {
		return domain.BeginRun{}, err
	}
	threadID, err := newID("thread_")
	if err != nil {
		return domain.BeginRun{}, err
	}
	deadline := now + policy.MaxDurationSeconds
	attachmentsJSON, _ := jsonText(attachmentIDs)
	run := domain.Run{
		RunID: runID, AttemptID: 1, ThreadID: threadID, OwnerID: owner, TaskID: task.TaskID, Question: question,
		InputMessageID: messageID, AttachmentDocumentIDs: append([]string(nil), attachmentIDs...), Status: "running", Stage: "agent",
		Evidence: []domain.Citation{}, VerifiedCitationIDs: []string{}, VerifiedContextMessageIDs: []string{}, ClarificationFields: []string{},
		MaxDurationSeconds: policy.MaxDurationSeconds, RemainingSeconds: policy.MaxDurationSeconds, DeadlineAt: &deadline,
		MaxModelCalls: policy.MaxModelCalls, MaxToolCalls: policy.MaxToolCalls, NextEventSeq: 1, CreatedAt: now, UpdatedAt: now,
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO conversation_runs(
		run_id,thread_id,owner_id,task_id,question,input_message_id,attachment_doc_ids,status,stage,evidence_json,
		verified_citation_ids,verified_context_message_ids,clarification_fields,max_duration_seconds,remaining_seconds,deadline_at,
		max_model_calls,max_tool_calls,model_call_count,tool_call_count,model_retry_count,tool_retry_count,input_tokens,output_tokens,
		usage_reported_model_calls,answer_message_id,error_type,end_reason,cancel_requested_at,finished_at,attempt_id,next_event_seq,
		request_hash,idempotency_key_hash,created_at,updated_at)
		VALUES(?,?,?,?,?,?,?,?,?,'[]','[]','[]','[]',?,?,?,?,?,0,0,0,0,NULL,NULL,0,NULL,NULL,NULL,NULL,NULL,1,1,?,?,?,?)`,
		run.RunID, run.ThreadID, run.OwnerID, run.TaskID, run.Question, run.InputMessageID, attachmentsJSON, run.Status, run.Stage,
		run.MaxDurationSeconds, run.RemainingSeconds, deadline, run.MaxModelCalls, run.MaxToolCalls, requestHash, keyHash, now, now)
	if err != nil {
		return domain.BeginRun{}, err
	}
	for _, attachment := range attachmentVersions {
		if _, err = tx.ExecContext(ctx, `INSERT INTO control_run_attachment_snapshots(run_id,attempt_id,document_id,version_id,sha256) VALUES(?,?,?,?,?)`, run.RunID, run.AttemptID, attachment.DocumentID, attachment.VersionID, attachment.SHA256); err != nil {
			return domain.BeginRun{}, err
		}
	}
	if err = snapshotMemories(ctx, tx, run.RunID, run.AttemptID, owner, now); err != nil {
		return domain.BeginRun{}, err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO control_idempotency(owner_id,key_hash,request_hash,run_id,created_at) VALUES(?,?,?,?,?)`, owner, keyHash, requestHash, run.RunID, now); err != nil {
		return domain.BeginRun{}, err
	}
	if err = tx.Commit(); err != nil {
		return domain.BeginRun{}, err
	}
	task.UpdatedAt = now
	return domain.BeginRun{Task: task, Run: run, TaskCreated: taskCreated}, nil
}

func snapshotMemories(ctx context.Context, tx *sql.Tx, runID string, attemptID int, owner string, now float64) error {
	enabled := 1
	err := tx.QueryRowContext(ctx, "SELECT use_saved_memory FROM memory_settings WHERE owner_id=?", owner).Scan(&enabled)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return err
	}
	if enabled == 0 {
		return nil
	}
	rows, err := tx.QueryContext(ctx, "SELECT payload FROM account_memories WHERE owner_id=? ORDER BY memory_id", owner)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var payload string
		if err = rows.Scan(&payload); err != nil {
			return err
		}
		var memory domain.Memory
		if err = json.Unmarshal([]byte(payload), &memory); err != nil {
			return err
		}
		if memory.ExpiresAt != nil && *memory.ExpiresAt <= now {
			continue
		}
		if memory.SourceMessageID == "" {
			memory.SourceMessageID = "manual:" + memory.MemoryID
			payload, _ = jsonText(memory)
		}
		if _, err = tx.ExecContext(ctx, `INSERT INTO control_run_memory_snapshots(run_id,attempt_id,memory_id,revision,payload_json) VALUES(?,?,?,?,?)`, runID, attemptID, memory.MemoryID, memory.Revision, payload); err != nil {
			return err
		}
	}
	return rows.Err()
}

func (s *Store) RunExecution(ctx context.Context, run domain.Run) (domain.RunExecute, error) {
	var frozen string
	err := s.DB.QueryRowContext(ctx, `SELECT payload_json FROM control_run_execution_snapshots WHERE run_id=? AND attempt_id=?`, run.RunID, run.AttemptID).Scan(&frozen)
	if err == nil {
		var execute domain.RunExecute
		if err = json.Unmarshal([]byte(frozen), &execute); err != nil {
			return domain.RunExecute{}, err
		}
		return execute, nil
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return domain.RunExecute{}, err
	}
	attachments := []domain.AttachmentVersion{}
	rows, err := s.DB.QueryContext(ctx, `SELECT document_id,version_id,sha256 FROM control_run_attachment_snapshots WHERE run_id=? AND attempt_id=? ORDER BY document_id`, run.RunID, run.AttemptID)
	if err != nil {
		return domain.RunExecute{}, err
	}
	for rows.Next() {
		var item domain.AttachmentVersion
		if err = rows.Scan(&item.DocumentID, &item.VersionID, &item.SHA256); err != nil {
			rows.Close()
			return domain.RunExecute{}, err
		}
		attachments = append(attachments, item)
	}
	if err = rows.Close(); err != nil {
		return domain.RunExecute{}, err
	}
	memories := []domain.MemoryReference{}
	rows, err = s.DB.QueryContext(ctx, `SELECT payload_json FROM control_run_memory_snapshots WHERE run_id=? AND attempt_id=? ORDER BY memory_id`, run.RunID, run.AttemptID)
	if err != nil {
		return domain.RunExecute{}, err
	}
	for rows.Next() {
		var payload string
		var memory domain.Memory
		if err = rows.Scan(&payload); err != nil {
			rows.Close()
			return domain.RunExecute{}, err
		}
		if err = json.Unmarshal([]byte(payload), &memory); err != nil {
			rows.Close()
			return domain.RunExecute{}, err
		}
		memories = append(memories, domain.MemoryReference{MemoryID: memory.MemoryID, Revision: memory.Revision, Content: memory.Content, SourceMessageID: memory.SourceMessageID, SourceQuote: memory.SourceQuote})
	}
	if err = rows.Close(); err != nil {
		return domain.RunExecute{}, err
	}
	recent := []domain.ContextMessage{}
	rows, err = s.DB.QueryContext(ctx, `SELECT msg_id,role,content FROM messages WHERE task_id=? AND msg_id!=? AND role IN ('user','assistant') ORDER BY created_at DESC,rowid DESC LIMIT 50`, run.TaskID, run.InputMessageID)
	if err != nil {
		return domain.RunExecute{}, err
	}
	for rows.Next() {
		var message domain.ContextMessage
		if err = rows.Scan(&message.MessageID, &message.Role, &message.Content); err != nil {
			rows.Close()
			return domain.RunExecute{}, err
		}
		recent = append(recent, message)
	}
	if err = rows.Close(); err != nil {
		return domain.RunExecute{}, err
	}
	for left, right := 0, len(recent)-1; left < right; left, right = left+1, right-1 {
		recent[left], recent[right] = recent[right], recent[left]
	}
	deadline := float64(time.Now().Add(time.Duration(run.MaxDurationSeconds*float64(time.Second))).UnixNano()) / 1e9
	if run.DeadlineAt != nil {
		deadline = *run.DeadlineAt
	}
	execute := domain.RunExecute{
		SchemaVersion: "1.0", RunID: run.RunID, AttemptID: run.AttemptID, ThreadID: run.ThreadID,
		OwnerID: run.OwnerID, TaskID: run.TaskID, InputMessageID: run.InputMessageID, Question: run.Question,
		AttachmentVersions: attachments, MemorySnapshot: memories, RecentMessages: recent, ConversationSummary: "",
		Budget: domain.RunBudget{DeadlineAt: deadline, MaxModelCalls: run.MaxModelCalls, MaxToolCalls: run.MaxToolCalls},
	}
	payload, err := jsonText(execute)
	if err != nil {
		return domain.RunExecute{}, err
	}
	result, err := s.DB.ExecContext(ctx, `INSERT OR IGNORE INTO control_run_execution_snapshots(run_id,attempt_id,payload_json,created_at) VALUES(?,?,?,?)`, run.RunID, run.AttemptID, payload, time.Now().UnixNano()/1e9)
	if err != nil {
		return domain.RunExecute{}, err
	}
	if rows, _ := result.RowsAffected(); rows == 0 {
		var existing string
		if err = s.DB.QueryRowContext(ctx, `SELECT payload_json FROM control_run_execution_snapshots WHERE run_id=? AND attempt_id=?`, run.RunID, run.AttemptID).Scan(&existing); err != nil {
			return domain.RunExecute{}, err
		}
		if existing != payload {
			return domain.RunExecute{}, fmt.Errorf("%w: execution snapshot changed", domain.ErrConflict)
		}
	}
	return execute, nil
}

func (s *Store) ApplyRunEvent(ctx context.Context, event domain.RunEvent) (domain.Run, *domain.Message, bool, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return domain.Run{}, nil, false, err
	}
	defer tx.Rollback()
	run, err := getRunByIDTx(ctx, tx, event.RunID)
	if err != nil {
		return domain.Run{}, nil, false, err
	}
	if err = event.Validate(run); err != nil {
		return domain.Run{}, nil, false, err
	}
	var existingType, existingPayload string
	err = tx.QueryRowContext(ctx, `SELECT event_type,payload_json FROM run_attempt_events WHERE run_id=? AND attempt_id=? AND event_seq=?`, event.RunID, event.AttemptID, event.EventSeq).Scan(&existingType, &existingPayload)
	if err == nil {
		if existingType == event.EventType && jsonEqual([]byte(existingPayload), event.Payload) {
			return run, nil, false, nil
		}
		return domain.Run{}, nil, false, domain.ErrEventSequence
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return domain.Run{}, nil, false, err
	}
	if run.Terminal() {
		return run, nil, false, domain.ErrTerminalRun
	}
	if run.Status != "running" && run.Status != "cancel_requested" {
		return run, nil, false, domain.ErrConflict
	}
	if event.EventSeq != run.NextEventSeq {
		return run, nil, false, domain.ErrEventSequence
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO run_attempt_events(run_id,attempt_id,event_seq,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)`, event.RunID, event.AttemptID, event.EventSeq, event.EventType, string(event.Payload), time.Now().UnixNano()/1e9); err != nil {
		return domain.Run{}, nil, false, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	run.NextEventSeq++
	run.UpdatedAt = now
	var answer *domain.Message
	switch event.EventType {
	case "stage":
		var payload struct {
			Stage string `json:"stage"`
		}
		if err = strictPayload(event.Payload, &payload); err != nil || !oneOf(payload.Stage, "agent", "tool", "generation", "verification") {
			return domain.Run{}, nil, false, domain.ErrInvalid
		}
		run.Stage = payload.Stage
	case "usage":
		var payload domain.UsagePayload
		if err = strictPayload(event.Payload, &payload); err != nil || payload.ModelCalls < 0 || payload.ToolCalls < 0 {
			return domain.Run{}, nil, false, domain.ErrInvalid
		}
		run.ModelCallCount = payload.ModelCalls
		run.ToolCallCount = payload.ToolCalls
		run.InputTokens = payload.InputTokens
		run.OutputTokens = payload.OutputTokens
		run.UsageReportedModelCalls = payload.ModelCalls
	case "interrupted":
		run.Status = "interrupted"
		reason := "runtime_interrupted"
		var payload map[string]any
		if json.Unmarshal(event.Payload, &payload) == nil {
			if value, ok := payload["reason"].(string); ok && value != "" {
				reason = value
			}
		}
		run.EndReason = &reason
		run.DeadlineAt = nil
	case "result_proposal":
		var proposal domain.ResultProposal
		if err = strictPayload(event.Payload, &proposal); err != nil || !oneOf(proposal.Status, "completed", "failed") || strings.TrimSpace(proposal.Answer) == "" || proposal.EndReason == "" {
			return domain.Run{}, nil, false, domain.ErrInvalid
		}
		citations := make([]domain.Citation, 0, len(proposal.Citations))
		verifiedCitations := make(map[string]struct{}, len(proposal.VerifiedCitationIDs))
		for _, citationID := range proposal.VerifiedCitationIDs {
			if citationID == "" {
				return domain.Run{}, nil, false, domain.ErrInvalid
			}
			if _, duplicate := verifiedCitations[citationID]; duplicate {
				return domain.Run{}, nil, false, domain.ErrInvalid
			}
			verifiedCitations[citationID] = struct{}{}
		}
		for _, rawCitation := range proposal.Citations {
			encoded, encodeErr := json.Marshal(rawCitation)
			if encodeErr != nil {
				return domain.Run{}, nil, false, domain.ErrInvalid
			}
			var citation domain.Citation
			if decodeErr := json.Unmarshal(encoded, &citation); decodeErr != nil {
				return domain.Run{}, nil, false, domain.ErrInvalid
			}
			if citation.CitationID == "" {
				return domain.Run{}, nil, false, domain.ErrInvalid
			}
			if _, verified := verifiedCitations[citation.CitationID]; !verified {
				return domain.Run{}, nil, false, domain.ErrInvalid
			}
			citations = append(citations, citation)
		}
		var frozenExecution string
		if err = tx.QueryRowContext(ctx, `SELECT payload_json FROM control_run_execution_snapshots WHERE run_id=? AND attempt_id=?`, run.RunID, run.AttemptID).Scan(&frozenExecution); err != nil {
			return domain.Run{}, nil, false, err
		}
		var execution domain.RunExecute
		if err = json.Unmarshal([]byte(frozenExecution), &execution); err != nil {
			return domain.Run{}, nil, false, err
		}
		allowedContext := map[string]struct{}{}
		for _, message := range execution.RecentMessages {
			allowedContext[message.MessageID] = struct{}{}
		}
		for _, memory := range execution.MemorySnapshot {
			allowedContext[memory.SourceMessageID] = struct{}{}
		}
		seenContext := map[string]struct{}{}
		for _, messageID := range proposal.VerifiedContextMessageIDs {
			if _, allowed := allowedContext[messageID]; !allowed {
				return domain.Run{}, nil, false, domain.ErrInvalid
			}
			if _, duplicate := seenContext[messageID]; duplicate {
				return domain.Run{}, nil, false, domain.ErrInvalid
			}
			seenContext[messageID] = struct{}{}
		}
		messageID, idErr := newID("msg_")
		if idErr != nil {
			return domain.Run{}, nil, false, idErr
		}
		message := domain.Message{MessageID: messageID, TaskID: run.TaskID, Role: "assistant", Content: proposal.Answer, Citations: citations, CreatedAt: now}
		citationsJSON, _ := jsonText(message.Citations)
		if _, err = tx.ExecContext(ctx, `INSERT INTO messages(msg_id,task_id,role,content,tool_call_id,citations,created_at) VALUES(?,?, 'assistant', ?,NULL,?,?)`, message.MessageID, message.TaskID, message.Content, citationsJSON, message.CreatedAt); err != nil {
			return domain.Run{}, nil, false, err
		}
		for index, citation := range message.Citations {
			payload, _ := jsonText(citation)
			if _, err = tx.ExecContext(ctx, `INSERT INTO message_citations(message_id,citation_id,ordinal,payload_json) VALUES(?,?,?,?)`, message.MessageID, citation.CitationID, index, payload); err != nil {
				return domain.Run{}, nil, false, err
			}
		}
		run.Status = proposal.Status
		run.Stage = proposal.Status
		run.Evidence = citations
		run.VerifiedCitationIDs = proposal.VerifiedCitationIDs
		run.VerifiedContextMessageIDs = proposal.VerifiedContextMessageIDs
		run.ClarificationFields = proposal.ClarificationFields
		run.AnswerMessageID = &messageID
		run.ErrorType = proposal.ErrorType
		run.EndReason = &proposal.EndReason
		run.FinishedAt = &now
		run.DeadlineAt = nil
		if _, err = tx.ExecContext(ctx, `UPDATE tasks SET state='done',updated_at=? WHERE task_id=?`, now, run.TaskID); err != nil {
			return domain.Run{}, nil, false, err
		}
		answer = &message
	}
	if err = updateRunTx(ctx, tx, run); err != nil {
		return domain.Run{}, nil, false, err
	}
	if err = tx.Commit(); err != nil {
		return domain.Run{}, nil, false, err
	}
	return run, answer, true, nil
}

func jsonEqual(left, right []byte) bool {
	var a, b any
	if json.Unmarshal(left, &a) != nil || json.Unmarshal(right, &b) != nil {
		return false
	}
	canonicalA, errA := json.Marshal(a)
	canonicalB, errB := json.Marshal(b)
	return errA == nil && errB == nil && bytes.Equal(canonicalA, canonicalB)
}

func strictPayload(data []byte, target any) error {
	decoder := json.NewDecoder(strings.NewReader(string(data)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return errors.New("payload contains multiple JSON values")
	}
	return nil
}

func oneOf(value string, allowed ...string) bool {
	for _, candidate := range allowed {
		if value == candidate {
			return true
		}
	}
	return false
}

func (s *Store) InterruptRun(ctx context.Context, owner, runID string, attemptID int, reason string) (domain.Run, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return domain.Run{}, err
	}
	defer tx.Rollback()
	run, err := getRunTx(ctx, tx, owner, runID)
	if err != nil {
		return domain.Run{}, err
	}
	if run.AttemptID != attemptID {
		return domain.Run{}, domain.ErrConflict
	}
	if run.Terminal() || run.Status == "interrupted" {
		return run, nil
	}
	now := float64(time.Now().UnixNano()) / 1e9
	run.Status = "interrupted"
	run.EndReason = &reason
	run.DeadlineAt = nil
	run.UpdatedAt = now
	if err = updateRunTx(ctx, tx, run); err != nil {
		return domain.Run{}, err
	}
	return run, tx.Commit()
}

func (s *Store) FailRun(ctx context.Context, owner, runID string, attemptID int, errorType, text string) (domain.Run, domain.Message, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return domain.Run{}, domain.Message{}, err
	}
	defer tx.Rollback()
	run, err := getRunTx(ctx, tx, owner, runID)
	if err != nil {
		return domain.Run{}, domain.Message{}, err
	}
	if run.AttemptID != attemptID || run.Terminal() {
		return run, domain.Message{}, domain.ErrConflict
	}
	now := float64(time.Now().UnixNano()) / 1e9
	messageID, err := newID("msg_")
	if err != nil {
		return domain.Run{}, domain.Message{}, err
	}
	message := domain.Message{MessageID: messageID, TaskID: run.TaskID, Role: "assistant", Content: text, Citations: []domain.Citation{}, CreatedAt: now}
	if _, err = tx.ExecContext(ctx, `INSERT INTO messages(msg_id,task_id,role,content,tool_call_id,citations,created_at) VALUES(?,?, 'assistant', ?,NULL,'[]',?)`, messageID, run.TaskID, text, now); err != nil {
		return domain.Run{}, domain.Message{}, err
	}
	run.Status, run.Stage = "failed", "failed"
	run.AnswerMessageID = &messageID
	run.ErrorType, run.EndReason = &errorType, &errorType
	run.FinishedAt, run.DeadlineAt, run.UpdatedAt = &now, nil, now
	if err = updateRunTx(ctx, tx, run); err != nil {
		return domain.Run{}, domain.Message{}, err
	}
	if err = tx.Commit(); err != nil {
		return domain.Run{}, domain.Message{}, err
	}
	return run, message, nil
}

func (s *Store) GetRun(ctx context.Context, owner, runID string) (*domain.Run, error) {
	run, err := getRunDB(ctx, s.DB, owner, runID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &run, err
}

func (s *Store) LatestRun(ctx context.Context, owner, taskID string) (*domain.Run, error) {
	run, err := scanRun(s.DB.QueryRowContext(ctx, runSelect+` WHERE owner_id=? AND task_id=? ORDER BY updated_at DESC LIMIT 1`, owner, taskID))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &run, err
}

func (s *Store) CancelRun(ctx context.Context, owner, runID, reason string) (domain.Run, bool, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return domain.Run{}, false, err
	}
	defer tx.Rollback()
	run, err := getRunTx(ctx, tx, owner, runID)
	if err != nil {
		return domain.Run{}, false, err
	}
	if run.Terminal() {
		return run, false, nil
	}
	now := float64(time.Now().UnixNano()) / 1e9
	run.Status, run.Stage = "cancelled", "cancelled"
	run.EndReason, run.CancelRequestedAt, run.FinishedAt = &reason, &now, &now
	run.DeadlineAt, run.UpdatedAt = nil, now
	if err = updateRunTx(ctx, tx, run); err != nil {
		return domain.Run{}, false, err
	}
	if err = tx.Commit(); err != nil {
		return domain.Run{}, false, err
	}
	return run, true, nil
}

func (s *Store) PrepareResume(ctx context.Context, owner, runID string) (domain.Run, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return domain.Run{}, err
	}
	defer tx.Rollback()
	run, err := getRunTx(ctx, tx, owner, runID)
	if err != nil {
		return domain.Run{}, err
	}
	if run.Status != "interrupted" {
		return domain.Run{}, domain.ErrConflict
	}
	var executionPayload string
	if err = tx.QueryRowContext(ctx, `SELECT payload_json FROM control_run_execution_snapshots WHERE run_id=? AND attempt_id=?`, run.RunID, run.AttemptID).Scan(&executionPayload); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return domain.Run{}, fmt.Errorf("%w: frozen execution snapshot is unavailable", domain.ErrConflict)
		}
		return domain.Run{}, err
	}
	var resumedExecution domain.RunExecute
	if err = json.Unmarshal([]byte(executionPayload), &resumedExecution); err != nil {
		return domain.Run{}, err
	}
	rows, err := tx.QueryContext(ctx, `SELECT memory_id,revision,payload_json FROM control_run_memory_snapshots WHERE run_id=? AND attempt_id=?`, run.RunID, run.AttemptID)
	if err != nil {
		return domain.Run{}, err
	}
	type snapshot struct {
		id       string
		revision int
		payload  string
	}
	memories := []snapshot{}
	for rows.Next() {
		var item snapshot
		if err = rows.Scan(&item.id, &item.revision, &item.payload); err != nil {
			rows.Close()
			return domain.Run{}, err
		}
		var currentRevision int
		if err = tx.QueryRowContext(ctx, `SELECT revision FROM account_memories WHERE owner_id=? AND memory_id=?`, owner, item.id).Scan(&currentRevision); err != nil || currentRevision != item.revision {
			rows.Close()
			return domain.Run{}, fmt.Errorf("%w: referenced memory changed", domain.ErrConflict)
		}
		memories = append(memories, item)
	}
	rows.Close()
	previousAttempt := run.AttemptID
	run.AttemptID++
	now := float64(time.Now().UnixNano()) / 1e9
	deadline := now + run.MaxDurationSeconds
	run.Status, run.Stage, run.NextEventSeq = "running", "agent", 1
	run.EndReason, run.ErrorType, run.FinishedAt, run.CancelRequestedAt = nil, nil, nil, nil
	run.DeadlineAt, run.RemainingSeconds, run.UpdatedAt = &deadline, run.MaxDurationSeconds, now
	resumedExecution.AttemptID = run.AttemptID
	resumedExecution.Resume = true
	resumedExecution.Budget.DeadlineAt = deadline
	for _, item := range memories {
		if _, err = tx.ExecContext(ctx, `INSERT INTO control_run_memory_snapshots(run_id,attempt_id,memory_id,revision,payload_json) VALUES(?,?,?,?,?)`, run.RunID, run.AttemptID, item.id, item.revision, item.payload); err != nil {
			return domain.Run{}, err
		}
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO control_run_attachment_snapshots(run_id,attempt_id,document_id,version_id,sha256) SELECT run_id,?,document_id,version_id,sha256 FROM control_run_attachment_snapshots WHERE run_id=? AND attempt_id=?`, run.AttemptID, run.RunID, previousAttempt); err != nil {
		return domain.Run{}, err
	}
	resumedPayload, err := jsonText(resumedExecution)
	if err != nil {
		return domain.Run{}, err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO control_run_execution_snapshots(run_id,attempt_id,payload_json,created_at) VALUES(?,?,?,?)`, run.RunID, run.AttemptID, resumedPayload, now); err != nil {
		return domain.Run{}, err
	}
	if err = updateRunTx(ctx, tx, run); err != nil {
		return domain.Run{}, err
	}
	return run, tx.Commit()
}

func (s *Store) ListRunEvents(ctx context.Context, owner, runID string, attemptID int) ([]domain.RunEvent, error) {
	if run, err := s.GetRun(ctx, owner, runID); err != nil || run == nil {
		if err == nil {
			err = domain.ErrNotFound
		}
		return nil, err
	}
	return listRunEventsDB(ctx, s.DB, runID, attemptID)
}

func listRunEventsTx(ctx context.Context, tx *sql.Tx, runID string, attemptID int) ([]domain.RunEvent, error) {
	return listRunEventsQuery(ctx, tx, runID, attemptID)
}

func listRunEventsDB(ctx context.Context, db *sql.DB, runID string, attemptID int) ([]domain.RunEvent, error) {
	return listRunEventsQuery(ctx, db, runID, attemptID)
}

type queryer interface {
	QueryContext(context.Context, string, ...any) (*sql.Rows, error)
}

func listRunEventsQuery(ctx context.Context, q queryer, runID string, attemptID int) ([]domain.RunEvent, error) {
	rows, err := q.QueryContext(ctx, `SELECT event_seq,event_type,payload_json FROM run_attempt_events WHERE run_id=? AND attempt_id=? ORDER BY event_seq`, runID, attemptID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	events := []domain.RunEvent{}
	for rows.Next() {
		var event domain.RunEvent
		var payload string
		if err = rows.Scan(&event.EventSeq, &event.EventType, &payload); err != nil {
			return nil, err
		}
		event.SchemaVersion, event.RunID, event.AttemptID, event.Payload = "1.0", runID, attemptID, json.RawMessage(payload)
		events = append(events, event)
	}
	return events, rows.Err()
}

func (s *Store) ListTasks(ctx context.Context, owner string, limit int) ([]domain.Task, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT task_id,owner_id,title,state,user_goal,collected_facts,created_at,updated_at FROM tasks WHERE owner_id=? ORDER BY updated_at DESC LIMIT ?`, owner, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	tasks := []domain.Task{}
	for rows.Next() {
		task, scanErr := scanTask(rows)
		if scanErr != nil {
			return nil, scanErr
		}
		tasks = append(tasks, task)
	}
	return tasks, rows.Err()
}

func (s *Store) TaskDetail(ctx context.Context, owner, taskID string) (*domain.TaskDetail, error) {
	task, err := getTaskDB(ctx, s.DB, owner, taskID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	rows, err := s.DB.QueryContext(ctx, `SELECT msg_id,task_id,role,content,citations,created_at FROM messages WHERE task_id=? ORDER BY created_at,rowid`, taskID)
	if err != nil {
		return nil, err
	}
	messages := []domain.Message{}
	for rows.Next() {
		message, scanErr := scanMessage(rows)
		if scanErr != nil {
			rows.Close()
			return nil, scanErr
		}
		messages = append(messages, message)
	}
	if err = rows.Close(); err != nil {
		return nil, err
	}
	attachments := []string{}
	rows, err = s.DB.QueryContext(ctx, `SELECT document_id FROM task_attachments WHERE task_id=? ORDER BY selection_order`, taskID)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var id string
		if err = rows.Scan(&id); err != nil {
			rows.Close()
			return nil, err
		}
		attachments = append(attachments, id)
	}
	rows.Close()
	latest, err := s.LatestRun(ctx, owner, taskID)
	if err != nil {
		return nil, err
	}
	var latestView *domain.RunView
	if latest != nil {
		view := domain.ViewRun(*latest, time.Now())
		latestView = &view
	}
	return &domain.TaskDetail{Task: task, Messages: messages, AttachmentDocumentIDs: attachments, LatestRun: latestView}, nil
}

func (s *Store) UpdateTask(ctx context.Context, owner, taskID string, title *string, facts map[string]any) (*domain.Task, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	task, err := getTaskTx(ctx, tx, owner, taskID)
	if err != nil {
		if errors.Is(err, domain.ErrNotFound) {
			return nil, nil
		}
		return nil, err
	}
	if title != nil {
		task.Title = *title
	}
	if task.CollectedFacts == nil {
		task.CollectedFacts = map[string]any{}
	}
	for key, value := range facts {
		task.CollectedFacts[key] = value
	}
	task.UpdatedAt = float64(time.Now().UnixNano()) / 1e9
	encoded, _ := jsonText(task.CollectedFacts)
	if _, err = tx.ExecContext(ctx, `UPDATE tasks SET title=?,collected_facts=?,updated_at=? WHERE task_id=? AND owner_id=?`, task.Title, encoded, task.UpdatedAt, taskID, owner); err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return &task, nil
}

func (s *Store) DeleteTask(ctx context.Context, owner, taskID string) (bool, error) {
	result, err := s.DB.ExecContext(ctx, `DELETE FROM tasks WHERE task_id=? AND owner_id=?`, taskID, owner)
	if err != nil {
		return false, err
	}
	count, err := result.RowsAffected()
	return count == 1, err
}

func (s *Store) ListMemories(ctx context.Context, owner string) ([]domain.Memory, bool, error) {
	enabled := 1
	err := s.DB.QueryRowContext(ctx, `SELECT use_saved_memory FROM memory_settings WHERE owner_id=?`, owner).Scan(&enabled)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return nil, false, err
	}
	rows, err := s.DB.QueryContext(ctx, `SELECT payload FROM account_memories WHERE owner_id=? ORDER BY json_extract(payload,'$.updated_at') DESC`, owner)
	if err != nil {
		return nil, false, err
	}
	defer rows.Close()
	memories := []domain.Memory{}
	now := float64(time.Now().UnixNano()) / 1e9
	for rows.Next() {
		var payload string
		var memory domain.Memory
		if err = rows.Scan(&payload); err != nil {
			return nil, false, err
		}
		if err = json.Unmarshal([]byte(payload), &memory); err != nil {
			return nil, false, err
		}
		if memory.ExpiresAt != nil && *memory.ExpiresAt <= now {
			continue
		}
		memory.NeedsReview = memory.ReviewAt != nil && *memory.ReviewAt <= now
		memories = append(memories, memory)
	}
	return memories, enabled != 0, rows.Err()
}

func (s *Store) SetMemoryEnabled(ctx context.Context, owner string, enabled bool) error {
	value := 0
	if enabled {
		value = 1
	}
	_, err := s.DB.ExecContext(ctx, `INSERT INTO memory_settings(owner_id,use_saved_memory,updated_at) VALUES(?,?,?) ON CONFLICT(owner_id) DO UPDATE SET use_saved_memory=excluded.use_saved_memory,updated_at=excluded.updated_at`, owner, value, time.Now().UnixNano()/1e9)
	return err
}

func (s *Store) CreateMemory(ctx context.Context, memory domain.Memory) (domain.Memory, bool, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return domain.Memory{}, false, err
	}
	defer tx.Rollback()
	rows, err := tx.QueryContext(ctx, `SELECT payload FROM account_memories WHERE owner_id=?`, memory.OwnerID)
	if err != nil {
		return domain.Memory{}, false, err
	}
	count := 0
	for rows.Next() {
		count++
		var payload string
		var existing domain.Memory
		if err = rows.Scan(&payload); err != nil {
			rows.Close()
			return domain.Memory{}, false, err
		}
		if json.Unmarshal([]byte(payload), &existing) == nil && existing.Kind == memory.Kind && existing.Content == memory.Content {
			rows.Close()
			return existing, true, nil
		}
	}
	rows.Close()
	if count >= 100 {
		return domain.Memory{}, false, fmt.Errorf("%w: memory quota exceeded", domain.ErrConflict)
	}
	payload, _ := jsonText(memory)
	if _, err = tx.ExecContext(ctx, `INSERT INTO account_memories(memory_id,owner_id,revision,payload) VALUES(?,?,?,?)`, memory.MemoryID, memory.OwnerID, memory.Revision, payload); err != nil {
		return domain.Memory{}, false, err
	}
	if err = tx.Commit(); err != nil {
		return domain.Memory{}, false, err
	}
	return memory, false, nil
}

func (s *Store) UpdateMemory(ctx context.Context, owner, memoryID string, expected int, content string, expiresAt *float64, expiryProvided bool) (domain.Memory, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return domain.Memory{}, err
	}
	defer tx.Rollback()
	memory, err := getMemoryTx(ctx, tx, owner, memoryID)
	if err != nil {
		return domain.Memory{}, err
	}
	if memory.Revision != expected {
		return domain.Memory{}, domain.ErrRevision
	}
	now := float64(time.Now().UnixNano()) / 1e9
	stable := memory.Kind == "preference" || (memory.Kind == "profile" && memory.ProfileField != nil && *memory.ProfileField == "name")
	if expiryProvided {
		if expiresAt == nil && !stable {
			return domain.Memory{}, domain.ErrInvalid
		}
		if expiresAt != nil && *expiresAt <= now {
			return domain.Memory{}, domain.ErrInvalid
		}
		memory.ExpiresAt = expiresAt
		if expiresAt == nil {
			memory.RetentionDays = nil
		} else {
			value := (*expiresAt - now) / 86400
			memory.RetentionDays = &value
		}
	}
	memory.Content, memory.SourceQuote, memory.Revision, memory.UpdatedAt = content, content, expected+1, now
	if memory.ProfileField != nil {
		value := content
		memory.Value = &value
	}
	payload, _ := jsonText(memory)
	result, err := tx.ExecContext(ctx, `UPDATE account_memories SET revision=?,payload=? WHERE memory_id=? AND owner_id=? AND revision=?`, memory.Revision, payload, memoryID, owner, expected)
	if err != nil {
		return domain.Memory{}, err
	}
	if count, _ := result.RowsAffected(); count != 1 {
		return domain.Memory{}, domain.ErrRevision
	}
	return memory, tx.Commit()
}

func (s *Store) ConfirmMemory(ctx context.Context, owner, memoryID string, expected int, now float64) (domain.Memory, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return domain.Memory{}, err
	}
	defer tx.Rollback()
	memory, err := getMemoryTx(ctx, tx, owner, memoryID)
	if err != nil {
		return domain.Memory{}, err
	}
	if memory.Revision != expected || memory.RetentionDays == nil {
		return domain.Memory{}, domain.ErrRevision
	}
	expiry := now + *memory.RetentionDays*86400
	memory.ExpiresAt, memory.UpdatedAt, memory.Revision = &expiry, now, expected+1
	if memory.Kind == "profile" {
		review := now + 180*86400
		memory.ReviewAt = &review
	}
	payload, _ := jsonText(memory)
	result, err := tx.ExecContext(ctx, `UPDATE account_memories SET revision=?,payload=? WHERE memory_id=? AND owner_id=? AND revision=?`, memory.Revision, payload, memoryID, owner, expected)
	if err != nil {
		return domain.Memory{}, err
	}
	if count, _ := result.RowsAffected(); count != 1 {
		return domain.Memory{}, domain.ErrRevision
	}
	return memory, tx.Commit()
}

func (s *Store) DeleteMemory(ctx context.Context, owner, memoryID string) (bool, error) {
	result, err := s.DB.ExecContext(ctx, `DELETE FROM account_memories WHERE memory_id=? AND owner_id=?`, memoryID, owner)
	if err != nil {
		return false, err
	}
	count, err := result.RowsAffected()
	return count == 1, err
}

func (s *Store) ClearMemories(ctx context.Context, owner string) (int64, error) {
	result, err := s.DB.ExecContext(ctx, `DELETE FROM account_memories WHERE owner_id=?`, owner)
	if err != nil {
		return 0, err
	}
	return result.RowsAffected()
}

func (s *Store) SubmitFeedback(ctx context.Context, feedback domain.Feedback) (*string, error) {
	var role string
	err := s.DB.QueryRowContext(ctx, `SELECT m.role FROM messages m JOIN tasks t ON t.task_id=m.task_id WHERE m.msg_id=? AND m.task_id=? AND t.owner_id=?`, feedback.MessageID, feedback.TaskID, feedback.OwnerID).Scan(&role)
	if errors.Is(err, sql.ErrNoRows) || role != "assistant" {
		return nil, domain.ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	if feedback.Rating == nil {
		_, err = s.DB.ExecContext(ctx, `DELETE FROM message_feedback WHERE msg_id=? AND task_id=? AND owner_id=?`, feedback.MessageID, feedback.TaskID, feedback.OwnerID)
		return nil, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	_, err = s.DB.ExecContext(ctx, `INSERT INTO message_feedback(msg_id,task_id,owner_id,rating,created_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(msg_id) DO UPDATE SET rating=excluded.rating,updated_at=excluded.updated_at WHERE task_id=excluded.task_id AND owner_id=excluded.owner_id`, feedback.MessageID, feedback.TaskID, feedback.OwnerID, *feedback.Rating, now, now)
	return feedback.Rating, err
}

func (s *Store) FeedbackForTask(ctx context.Context, owner, taskID string) (map[string]string, error) {
	var exists int
	if err := s.DB.QueryRowContext(ctx, `SELECT 1 FROM tasks WHERE task_id=? AND owner_id=?`, taskID, owner).Scan(&exists); errors.Is(err, sql.ErrNoRows) {
		return nil, domain.ErrNotFound
	} else if err != nil {
		return nil, err
	}
	rows, err := s.DB.QueryContext(ctx, `SELECT msg_id,rating FROM message_feedback WHERE task_id=? AND owner_id=?`, taskID, owner)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := map[string]string{}
	for rows.Next() {
		var id, rating string
		if err = rows.Scan(&id, &rating); err != nil {
			return nil, err
		}
		result[id] = rating
	}
	return result, rows.Err()
}

func (s *Store) CreateFile(ctx context.Context, record domain.FileRecord) error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	_, err = tx.ExecContext(ctx, `INSERT INTO chat_documents(document_id,owner_id,logical_name,document_type,status,created_by,current_version_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)`, record.Document.DocumentID, record.Document.OwnerID, record.Document.LogicalName, record.Document.DocumentType, record.Document.Status, record.Document.CreatedBy, record.Document.CurrentVersionID, record.Document.CreatedAt, record.Document.UpdatedAt)
	if err != nil {
		return err
	}
	objectKey := "sqlite://" + record.Version.VersionID
	_, err = tx.ExecContext(ctx, `INSERT INTO chat_document_versions(version_id,document_id,version_number,object_key,sha256,mime_type,size_bytes,parser_version,page_count,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)`, record.Version.VersionID, record.Version.DocumentID, record.Version.VersionNumber, objectKey, record.Version.SHA256, record.Version.MIMEType, record.Version.SizeBytes, record.Version.ParserVersion, record.Version.PageCount, record.Version.CreatedAt)
	if err != nil {
		return err
	}
	job := record.Job
	if job == nil {
		return domain.ErrInvalid
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO chat_processing_jobs(job_id,document_version_id,status,current_stage,progress,error_code,error_message,retry_count,revision,created_at,updated_at,started_at,completed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)`, job.JobID, job.DocumentVersionID, job.Status, job.CurrentStage, job.Progress, job.ErrorCode, job.ErrorMessage, job.RetryCount, job.Revision, job.CreatedAt, job.UpdatedAt, job.StartedAt, job.CompletedAt)
	if err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO file_objects(version_id,content) VALUES(?,?)`, record.Version.VersionID, record.Content); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *Store) ListFiles(ctx context.Context, owner string) ([]domain.FileRecord, error) {
	rows, err := s.DB.QueryContext(ctx, fileSelect+` WHERE d.owner_id=? AND d.status!='deleted' ORDER BY d.updated_at DESC`, owner)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	files := []domain.FileRecord{}
	for rows.Next() {
		record, scanErr := scanFile(rows, false)
		if scanErr != nil {
			return nil, scanErr
		}
		files = append(files, record)
	}
	return files, rows.Err()
}

func (s *Store) GetFile(ctx context.Context, owner, documentID string) (*domain.FileRecord, error) {
	record, err := scanFile(s.DB.QueryRowContext(ctx, fileSelect+` WHERE d.owner_id=? AND d.document_id=? AND d.status!='deleted'`, owner, documentID), true)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &record, err
}

func (s *Store) GetFileJob(ctx context.Context, owner, jobID string) (*domain.ProcessingJob, error) {
	job, err := scanJob(s.DB.QueryRowContext(ctx, jobSelect+` WHERE j.job_id=? AND d.owner_id=?`, jobID, owner))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &job, err
}

func (s *Store) RetryFileJob(ctx context.Context, owner, jobID string) (*domain.ProcessingJob, error) {
	return s.transitionJob(ctx, owner, jobID, "retry")
}

func (s *Store) CancelFileJob(ctx context.Context, owner, jobID string) (*domain.ProcessingJob, error) {
	return s.transitionJob(ctx, owner, jobID, "cancel")
}

func (s *Store) transitionJob(ctx context.Context, owner, jobID, action string) (*domain.ProcessingJob, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	job, err := scanJob(tx.QueryRowContext(ctx, jobSelect+` WHERE j.job_id=? AND d.owner_id=?`, jobID, owner))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if action == "retry" {
		if job.Status != "failed" || job.RetryCount >= 3 {
			return nil, domain.ErrConflict
		}
		job.Status, job.CurrentStage, job.Progress = "queued", "extract_structure", 0
		job.RetryCount++
		job.ErrorCode, job.ErrorMessage, job.StartedAt, job.CompletedAt = nil, nil, nil, nil
	} else {
		if job.Status == "completed" || job.Status == "cancelled" {
			return &job, nil
		}
		job.Status, job.CompletedAt = "cancelled", &now
	}
	job.Revision++
	job.UpdatedAt = now
	_, err = tx.ExecContext(ctx, `UPDATE chat_processing_jobs SET status=?,current_stage=?,progress=?,error_code=?,error_message=?,retry_count=?,revision=?,updated_at=?,started_at=?,completed_at=? WHERE job_id=?`, job.Status, job.CurrentStage, job.Progress, job.ErrorCode, job.ErrorMessage, job.RetryCount, job.Revision, job.UpdatedAt, job.StartedAt, job.CompletedAt, job.JobID)
	if err != nil {
		return nil, err
	}
	if action == "retry" {
		_, err = tx.ExecContext(ctx, `UPDATE chat_documents SET status='queued',updated_at=? WHERE current_version_id=?`, now, job.DocumentVersionID)
	}
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return &job, nil
}

func (s *Store) SearchFiles(ctx context.Context, owner, query string, limit int) ([]map[string]any, error) {
	if limit < 1 || limit > 50 {
		limit = 20
	}
	rows, err := s.DB.QueryContext(ctx, `SELECT d.document_id,d.logical_name,v.version_id,v.sha256,CAST(o.content AS TEXT) FROM chat_documents d JOIN chat_document_versions v ON v.version_id=d.current_version_id JOIN file_objects o ON o.version_id=v.version_id WHERE d.owner_id=? AND d.status!='deleted' AND CAST(o.content AS TEXT) LIKE ? ORDER BY d.updated_at DESC LIMIT ?`, owner, "%"+query+"%", limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	hits := []map[string]any{}
	for rows.Next() {
		var documentID, name, versionID, sha, text string
		if err = rows.Scan(&documentID, &name, &versionID, &sha, &text); err != nil {
			return nil, err
		}
		if !utf8.ValidString(text) {
			continue
		}
		hits = append(hits, map[string]any{"score": 1.0, "chunk": map[string]any{"chunk_id": "file:" + versionID, "document_id": documentID, "document_version_id": versionID, "text": text, "source_type": "file", "source_name": name, "title": name, "page_number": 1, "source_sha256": sha}})
	}
	return hits, rows.Err()
}

func (s *Store) CreateKBDocument(ctx context.Context, document domain.KBDocument, content []byte) error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	_, err = tx.ExecContext(ctx, `INSERT INTO kb_documents(document_id,version_id,owner_id,source_name,source_type,title,source_url,category,mime_type,sha256,chunk_count,status,error_code,error_message,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,0,?,NULL,NULL,?,?)`, document.DocumentID, document.VersionID, document.OwnerID, document.SourceName, document.SourceType, document.Title, document.SourceURL, document.Category, document.MIMEType, document.SHA256, document.Status, document.CreatedAt, document.UpdatedAt)
	if err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO kb_document_objects(version_id,content) VALUES(?,?)`, document.VersionID, content); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *Store) CompleteKBDocument(ctx context.Context, documentID string, result domain.DocumentResult) (*domain.KBDocument, error) {
	now := float64(time.Now().UnixNano()) / 1e9
	update, err := s.DB.ExecContext(ctx, `UPDATE kb_documents SET status=?,title=?,chunk_count=?,error_code=?,error_message=?,updated_at=? WHERE document_id=?`, result.Status, result.Title, result.ChunkCount, result.ErrorCode, result.Message, now, documentID)
	if err != nil {
		return nil, err
	}
	if count, _ := update.RowsAffected(); count != 1 {
		return nil, domain.ErrNotFound
	}
	document, err := scanKBDocument(s.DB.QueryRowContext(ctx, kbDocumentSelect+` WHERE document_id=?`, documentID))
	return &document, err
}

func (s *Store) ListKBDocuments(ctx context.Context, scope string, owner *string) ([]domain.KBDocument, error) {
	query := kbDocumentSelect
	args := []any{}
	switch scope {
	case "public":
		query += ` WHERE owner_id IS NULL`
	case "private":
		if owner == nil {
			return nil, domain.ErrInvalid
		}
		query += ` WHERE owner_id=?`
		args = append(args, *owner)
	case "all":
		if owner == nil {
			query += ` WHERE owner_id IS NULL`
		} else {
			query += ` WHERE owner_id IS NULL OR owner_id=?`
			args = append(args, *owner)
		}
	default:
		return nil, domain.ErrInvalid
	}
	query += ` ORDER BY updated_at DESC`
	rows, err := s.DB.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	documents := []domain.KBDocument{}
	for rows.Next() {
		document, scanErr := scanKBDocument(rows)
		if scanErr != nil {
			return nil, scanErr
		}
		documents = append(documents, document)
	}
	return documents, rows.Err()
}

func (s *Store) GetKBDocument(ctx context.Context, scope, sourceName string, owner *string) (*domain.KBDocument, error) {
	query := kbDocumentSelect + ` WHERE source_name=?`
	args := []any{sourceName}
	switch scope {
	case "public":
		query += ` AND owner_id IS NULL`
	case "private":
		if owner == nil {
			return nil, domain.ErrInvalid
		}
		query += ` AND owner_id=?`
		args = append(args, *owner)
	case "all":
		if owner == nil {
			query += ` AND owner_id IS NULL`
		} else {
			query += ` AND (owner_id IS NULL OR owner_id=?)`
			args = append(args, *owner)
		}
	default:
		return nil, domain.ErrInvalid
	}
	document, err := scanKBDocument(s.DB.QueryRowContext(ctx, query, args...))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &document, err
}

func (s *Store) DeleteKBDocument(ctx context.Context, _ string, documentID string) (bool, error) {
	result, err := s.DB.ExecContext(ctx, `DELETE FROM kb_documents WHERE document_id=?`, documentID)
	if err != nil {
		return false, err
	}
	count, err := result.RowsAffected()
	return count == 1, err
}

const runSelect = `SELECT run_id,thread_id,owner_id,task_id,question,input_message_id,attachment_doc_ids,status,stage,evidence_json,verified_citation_ids,verified_context_message_ids,clarification_fields,max_duration_seconds,remaining_seconds,deadline_at,max_model_calls,max_tool_calls,model_call_count,tool_call_count,model_retry_count,tool_retry_count,input_tokens,output_tokens,usage_reported_model_calls,answer_message_id,error_type,end_reason,cancel_requested_at,finished_at,attempt_id,next_event_seq,created_at,updated_at FROM conversation_runs`

const kbDocumentSelect = `SELECT document_id,version_id,owner_id,source_name,source_type,title,source_url,category,mime_type,sha256,chunk_count,status,error_code,error_message,created_at,updated_at FROM kb_documents`

func scanKBDocument(row rowScanner) (domain.KBDocument, error) {
	var document domain.KBDocument
	err := row.Scan(&document.DocumentID, &document.VersionID, &document.OwnerID, &document.SourceName, &document.SourceType, &document.Title, &document.SourceURL, &document.Category, &document.MIMEType, &document.SHA256, &document.ChunkCount, &document.Status, &document.ErrorCode, &document.ErrorMessage, &document.CreatedAt, &document.UpdatedAt)
	return document, err
}

func scanRun(row rowScanner) (domain.Run, error) {
	var run domain.Run
	var attachments, evidence, verifiedCitations, verifiedMessages, clarification string
	err := row.Scan(&run.RunID, &run.ThreadID, &run.OwnerID, &run.TaskID, &run.Question, &run.InputMessageID, &attachments, &run.Status, &run.Stage, &evidence, &verifiedCitations, &verifiedMessages, &clarification, &run.MaxDurationSeconds, &run.RemainingSeconds, &run.DeadlineAt, &run.MaxModelCalls, &run.MaxToolCalls, &run.ModelCallCount, &run.ToolCallCount, &run.ModelRetryCount, &run.ToolRetryCount, &run.InputTokens, &run.OutputTokens, &run.UsageReportedModelCalls, &run.AnswerMessageID, &run.ErrorType, &run.EndReason, &run.CancelRequestedAt, &run.FinishedAt, &run.AttemptID, &run.NextEventSeq, &run.CreatedAt, &run.UpdatedAt)
	if err != nil {
		return domain.Run{}, err
	}
	for data, target := range map[string]any{attachments: &run.AttachmentDocumentIDs, evidence: &run.Evidence, verifiedCitations: &run.VerifiedCitationIDs, verifiedMessages: &run.VerifiedContextMessageIDs, clarification: &run.ClarificationFields} {
		if err = json.Unmarshal([]byte(data), target); err != nil {
			return domain.Run{}, err
		}
	}
	return run, nil
}

func getRunDB(ctx context.Context, db *sql.DB, owner, runID string) (domain.Run, error) {
	return scanRun(db.QueryRowContext(ctx, runSelect+` WHERE owner_id=? AND run_id=?`, owner, runID))
}

func getRunTx(ctx context.Context, tx *sql.Tx, owner, runID string) (domain.Run, error) {
	run, err := scanRun(tx.QueryRowContext(ctx, runSelect+` WHERE owner_id=? AND run_id=?`, owner, runID))
	if errors.Is(err, sql.ErrNoRows) {
		return domain.Run{}, domain.ErrNotFound
	}
	return run, err
}

func getRunByIDTx(ctx context.Context, tx *sql.Tx, runID string) (domain.Run, error) {
	run, err := scanRun(tx.QueryRowContext(ctx, runSelect+` WHERE run_id=?`, runID))
	if errors.Is(err, sql.ErrNoRows) {
		return domain.Run{}, domain.ErrNotFound
	}
	return run, err
}

func updateRunTx(ctx context.Context, tx *sql.Tx, run domain.Run) error {
	evidence, _ := jsonText(run.Evidence)
	verifiedCitations, _ := jsonText(run.VerifiedCitationIDs)
	verifiedMessages, _ := jsonText(run.VerifiedContextMessageIDs)
	clarification, _ := jsonText(run.ClarificationFields)
	_, err := tx.ExecContext(ctx, `UPDATE conversation_runs SET status=?,stage=?,evidence_json=?,verified_citation_ids=?,verified_context_message_ids=?,clarification_fields=?,remaining_seconds=?,deadline_at=?,model_call_count=?,tool_call_count=?,model_retry_count=?,tool_retry_count=?,input_tokens=?,output_tokens=?,usage_reported_model_calls=?,answer_message_id=?,error_type=?,end_reason=?,cancel_requested_at=?,finished_at=?,attempt_id=?,next_event_seq=?,updated_at=? WHERE run_id=? AND owner_id=?`, run.Status, run.Stage, evidence, verifiedCitations, verifiedMessages, clarification, run.RemainingSeconds, run.DeadlineAt, run.ModelCallCount, run.ToolCallCount, run.ModelRetryCount, run.ToolRetryCount, run.InputTokens, run.OutputTokens, run.UsageReportedModelCalls, run.AnswerMessageID, run.ErrorType, run.EndReason, run.CancelRequestedAt, run.FinishedAt, run.AttemptID, run.NextEventSeq, run.UpdatedAt, run.RunID, run.OwnerID)
	return err
}

func scanTask(row rowScanner) (domain.Task, error) {
	var task domain.Task
	var facts string
	err := row.Scan(&task.TaskID, &task.OwnerID, &task.Title, &task.State, &task.UserGoal, &facts, &task.CreatedAt, &task.UpdatedAt)
	if err != nil {
		return domain.Task{}, err
	}
	if err = json.Unmarshal([]byte(facts), &task.CollectedFacts); err != nil {
		return domain.Task{}, err
	}
	return task, nil
}

func getTaskDB(ctx context.Context, db *sql.DB, owner, taskID string) (domain.Task, error) {
	return scanTask(db.QueryRowContext(ctx, `SELECT task_id,owner_id,title,state,user_goal,collected_facts,created_at,updated_at FROM tasks WHERE owner_id=? AND task_id=?`, owner, taskID))
}

func getTaskTx(ctx context.Context, tx *sql.Tx, owner, taskID string) (domain.Task, error) {
	task, err := scanTask(tx.QueryRowContext(ctx, `SELECT task_id,owner_id,title,state,user_goal,collected_facts,created_at,updated_at FROM tasks WHERE owner_id=? AND task_id=?`, owner, taskID))
	if errors.Is(err, sql.ErrNoRows) {
		return domain.Task{}, domain.ErrNotFound
	}
	return task, err
}

func scanMessage(row rowScanner) (domain.Message, error) {
	var message domain.Message
	var citations string
	err := row.Scan(&message.MessageID, &message.TaskID, &message.Role, &message.Content, &citations, &message.CreatedAt)
	if err != nil {
		return domain.Message{}, err
	}
	if err = json.Unmarshal([]byte(citations), &message.Citations); err != nil {
		return domain.Message{}, err
	}
	return message, nil
}

func getMemoryTx(ctx context.Context, tx *sql.Tx, owner, memoryID string) (domain.Memory, error) {
	var payload string
	err := tx.QueryRowContext(ctx, `SELECT payload FROM account_memories WHERE owner_id=? AND memory_id=?`, owner, memoryID).Scan(&payload)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.Memory{}, domain.ErrNotFound
	}
	if err != nil {
		return domain.Memory{}, err
	}
	var memory domain.Memory
	if err = json.Unmarshal([]byte(payload), &memory); err != nil {
		return domain.Memory{}, err
	}
	return memory, nil
}

const fileSelect = `SELECT d.document_id,d.owner_id,d.logical_name,d.document_type,d.status,d.created_by,d.current_version_id,d.created_at,d.updated_at,v.version_id,v.document_id,v.version_number,v.sha256,v.mime_type,v.size_bytes,v.parser_version,v.page_count,v.created_at,j.job_id,j.document_version_id,j.status,j.current_stage,j.progress,j.error_code,j.error_message,j.retry_count,j.revision,j.created_at,j.updated_at,j.started_at,j.completed_at,o.content FROM chat_documents d JOIN chat_document_versions v ON v.version_id=d.current_version_id LEFT JOIN chat_processing_jobs j ON j.job_id=(SELECT job_id FROM chat_processing_jobs WHERE document_version_id=v.version_id ORDER BY created_at DESC,job_id DESC LIMIT 1) LEFT JOIN file_objects o ON o.version_id=v.version_id`

func scanFile(row rowScanner, includeContent bool) (domain.FileRecord, error) {
	var record domain.FileRecord
	var job domain.ProcessingJob
	var content []byte
	err := row.Scan(&record.Document.DocumentID, &record.Document.OwnerID, &record.Document.LogicalName, &record.Document.DocumentType, &record.Document.Status, &record.Document.CreatedBy, &record.Document.CurrentVersionID, &record.Document.CreatedAt, &record.Document.UpdatedAt, &record.Version.VersionID, &record.Version.DocumentID, &record.Version.VersionNumber, &record.Version.SHA256, &record.Version.MIMEType, &record.Version.SizeBytes, &record.Version.ParserVersion, &record.Version.PageCount, &record.Version.CreatedAt, &job.JobID, &job.DocumentVersionID, &job.Status, &job.CurrentStage, &job.Progress, &job.ErrorCode, &job.ErrorMessage, &job.RetryCount, &job.Revision, &job.CreatedAt, &job.UpdatedAt, &job.StartedAt, &job.CompletedAt, &content)
	if err != nil {
		return domain.FileRecord{}, err
	}
	record.Job = &job
	if includeContent {
		record.Content = content
	}
	return record, nil
}

const jobSelect = `SELECT j.job_id,j.document_version_id,j.status,j.current_stage,j.progress,j.error_code,j.error_message,j.retry_count,j.revision,j.created_at,j.updated_at,j.started_at,j.completed_at FROM chat_processing_jobs j JOIN chat_document_versions v ON v.version_id=j.document_version_id JOIN chat_documents d ON d.document_id=v.document_id`

func scanJob(row rowScanner) (domain.ProcessingJob, error) {
	var job domain.ProcessingJob
	err := row.Scan(&job.JobID, &job.DocumentVersionID, &job.Status, &job.CurrentStage, &job.Progress, &job.ErrorCode, &job.ErrorMessage, &job.RetryCount, &job.Revision, &job.CreatedAt, &job.UpdatedAt, &job.StartedAt, &job.CompletedAt)
	return job, err
}
