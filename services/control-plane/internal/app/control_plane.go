package app

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"riskpilot/control-plane/internal/domain"
)

type Repository interface {
	BeginRun(context.Context, string, *string, string, []string, string, string, RunPolicy) (domain.BeginRun, error)
	RunExecution(context.Context, domain.Run) (domain.RunExecute, error)
	ApplyRunEvent(context.Context, domain.RunEvent) (domain.Run, *domain.Message, bool, error)
	InterruptRun(context.Context, string, string, int, string) (domain.Run, error)
	FailRun(context.Context, string, string, int, string, string) (domain.Run, domain.Message, error)
	GetRun(context.Context, string, string) (*domain.Run, error)
	LatestRun(context.Context, string, string) (*domain.Run, error)
	CancelRun(context.Context, string, string, string) (domain.Run, bool, error)
	PrepareResume(context.Context, string, string) (domain.Run, error)
	ListRunEvents(context.Context, string, string, int) ([]domain.RunEvent, error)

	ListTasks(context.Context, string, int) ([]domain.Task, error)
	TaskDetail(context.Context, string, string) (*domain.TaskDetail, error)
	UpdateTask(context.Context, string, string, *string, map[string]any) (*domain.Task, error)
	DeleteTask(context.Context, string, string) (bool, error)

	ListMemories(context.Context, string) ([]domain.Memory, bool, error)
	SetMemoryEnabled(context.Context, string, bool) error
	CreateMemory(context.Context, domain.Memory) (domain.Memory, bool, error)
	UpdateMemory(context.Context, string, string, int, string, *float64, bool) (domain.Memory, error)
	ConfirmMemory(context.Context, string, string, int, float64) (domain.Memory, error)
	DeleteMemory(context.Context, string, string) (bool, error)
	ClearMemories(context.Context, string) (int64, error)

	SubmitFeedback(context.Context, domain.Feedback) (*string, error)
	FeedbackForTask(context.Context, string, string) (map[string]string, error)

	CreateFile(context.Context, domain.FileRecord) error
	ListFiles(context.Context, string) ([]domain.FileRecord, error)
	GetFile(context.Context, string, string) (*domain.FileRecord, error)
	GetFileJob(context.Context, string, string) (*domain.ProcessingJob, error)
	RetryFileJob(context.Context, string, string) (*domain.ProcessingJob, error)
	CancelFileJob(context.Context, string, string) (*domain.ProcessingJob, error)
	SearchFiles(context.Context, string, string, int) ([]map[string]any, error)

	CreateKBDocument(context.Context, domain.KBDocument, []byte) error
	CompleteKBDocument(context.Context, string, domain.DocumentResult) (*domain.KBDocument, error)
	ListKBDocuments(context.Context, string, *string) ([]domain.KBDocument, error)
	GetKBDocument(context.Context, string, string, *string) (*domain.KBDocument, error)
	DeleteKBDocument(context.Context, string, string) (bool, error)
}

type Runtime interface {
	Execute(context.Context, string, domain.RunExecute, func(domain.RunEvent) error) error
	Control(context.Context, string, string, domain.RunControl, func(domain.RunEvent) error) error
	ProcessDocument(context.Context, string, domain.DocumentProcess) (domain.DocumentResult, error)
	Ready(context.Context) error
}

type RunPolicy struct {
	MaxDurationSeconds float64
	MaxModelCalls      int
	MaxToolCalls       int
}

type ChatInput struct {
	TaskID                *string  `json:"task_id"`
	Message               string   `json:"message"`
	AttachmentDocumentIDs []string `json:"attachment_doc_ids"`
}

type ControlPlane struct {
	Repo    Repository
	Runtime Runtime
	Policy  RunPolicy
	Clock   func() time.Time
}

func NewControlPlane(repo Repository, runtime Runtime, cfg Config) *ControlPlane {
	return &ControlPlane{
		Repo:    repo,
		Runtime: runtime,
		Policy:  RunPolicy{MaxDurationSeconds: cfg.RunTimeout.Seconds(), MaxModelCalls: cfg.MaxModelCalls, MaxToolCalls: cfg.MaxToolCalls},
		Clock:   time.Now,
	}
}

func hashChat(input ChatInput) (string, error) {
	data, err := json.Marshal(input)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:]), nil
}

func (c *ControlPlane) Start(ctx context.Context, owner, idempotencyKey string, input ChatInput, emit func(domain.ExternalEvent) error) (domain.Run, error) {
	input.Message = strings.TrimSpace(input.Message)
	if input.Message == "" || len([]rune(input.Message)) > 8000 || len(input.AttachmentDocumentIDs) > 20 {
		return domain.Run{}, domain.ErrInvalid
	}
	seen := map[string]struct{}{}
	for _, id := range input.AttachmentDocumentIDs {
		if id == "" {
			return domain.Run{}, domain.ErrInvalid
		}
		if _, exists := seen[id]; exists {
			return domain.Run{}, domain.ErrInvalid
		}
		seen[id] = struct{}{}
	}
	requestHash, err := hashChat(input)
	if err != nil {
		return domain.Run{}, err
	}
	if idempotencyKey == "" {
		idempotencyKey, err = RandomID()
		if err != nil {
			return domain.Run{}, err
		}
	}
	begin, err := c.Repo.BeginRun(ctx, owner, input.TaskID, input.Message, input.AttachmentDocumentIDs, idempotencyKey, requestHash, c.Policy)
	if err != nil {
		return domain.Run{}, err
	}
	if begin.TaskCreated {
		if err = emit(domain.ExternalEvent{EventType: "task_created", Payload: map[string]any{"task_id": begin.Task.TaskID, "title": begin.Task.Title}}); err != nil {
			return begin.Run, err
		}
	}
	startedType := "run_started"
	if begin.Existing {
		startedType = "run_resumed"
	}
	if err = emit(domain.ExternalEvent{EventType: startedType, Payload: map[string]any{"run_id": begin.Run.RunID, "task_id": begin.Run.TaskID, "attempt_id": begin.Run.AttemptID}}); err != nil {
		return begin.Run, err
	}
	if err = emit(runStatusEvent(begin.Run)); err != nil {
		return begin.Run, err
	}
	if begin.Existing {
		return c.replay(ctx, owner, begin.Run, emit)
	}
	execute, err := c.Repo.RunExecution(ctx, begin.Run)
	if err != nil {
		return begin.Run, err
	}
	err = c.Runtime.Execute(ctx, owner, execute, func(event domain.RunEvent) error {
		return c.accept(ctx, owner, event, emit)
	})
	return c.finishRuntimeCall(ctx, owner, begin.Run, err, emit)
}

func (c *ControlPlane) replay(ctx context.Context, owner string, run domain.Run, emit func(domain.ExternalEvent) error) (domain.Run, error) {
	events, err := c.Repo.ListRunEvents(ctx, owner, run.RunID, run.AttemptID)
	if err != nil {
		return run, err
	}
	for _, event := range events {
		if err = c.emitPersisted(ctx, owner, event, emit); err != nil {
			return run, err
		}
	}
	stored, err := c.Repo.GetRun(ctx, owner, run.RunID)
	if err != nil || stored == nil {
		return run, err
	}
	if !stored.Terminal() && stored.Status != "interrupted" {
		return *stored, domain.ErrConflict
	}
	return *stored, nil
}

func (c *ControlPlane) accept(ctx context.Context, owner string, event domain.RunEvent, emit func(domain.ExternalEvent) error) error {
	run, answer, accepted, err := c.Repo.ApplyRunEvent(ctx, event)
	if err != nil {
		return err
	}
	if !accepted {
		return nil
	}
	return c.emitApplied(event, run, answer, emit)
}

func (c *ControlPlane) emitPersisted(ctx context.Context, owner string, event domain.RunEvent, emit func(domain.ExternalEvent) error) error {
	run, err := c.Repo.GetRun(ctx, owner, event.RunID)
	if err != nil || run == nil {
		return err
	}
	var answer *domain.Message
	if event.EventType == "result_proposal" && run.AnswerMessageID != nil {
		detail, detailErr := c.Repo.TaskDetail(ctx, owner, run.TaskID)
		if detailErr != nil {
			return detailErr
		}
		for i := range detail.Messages {
			if detail.Messages[i].MessageID == *run.AnswerMessageID {
				message := detail.Messages[i]
				answer = &message
				break
			}
		}
	}
	return c.emitApplied(event, *run, answer, emit)
}

func (c *ControlPlane) emitApplied(event domain.RunEvent, run domain.Run, answer *domain.Message, emit func(domain.ExternalEvent) error) error {
	payload := map[string]any{}
	if len(event.Payload) > 0 {
		_ = json.Unmarshal(event.Payload, &payload)
	}
	switch event.EventType {
	case "stage":
		return emit(runStatusEvent(run))
	case "usage":
		return nil
	case "interrupted":
		payload["run_id"] = run.RunID
		payload["attempt_id"] = run.AttemptID
		return emit(domain.ExternalEvent{EventType: "run_interrupted", Payload: payload})
	case "result_proposal":
		if err := emit(runStatusEvent(run)); err != nil {
			return err
		}
		if answer == nil {
			return errors.New("terminal result has no answer message")
		}
		return emit(answerEvent(*answer))
	default:
		return emit(domain.ExternalEvent{EventType: event.EventType, Payload: payload})
	}
}

func (c *ControlPlane) finishRuntimeCall(ctx context.Context, owner string, initial domain.Run, runtimeErr error, emit func(domain.ExternalEvent) error) (domain.Run, error) {
	stored, err := c.Repo.GetRun(context.WithoutCancel(ctx), owner, initial.RunID)
	if err != nil || stored == nil {
		return initial, err
	}
	if stored.Terminal() || stored.Status == "interrupted" {
		return *stored, runtimeErr
	}
	if runtimeErr == nil {
		run, interruptErr := c.Repo.InterruptRun(context.WithoutCancel(ctx), owner, initial.RunID, stored.AttemptID, "runtime_stream_ended")
		if interruptErr != nil {
			return *stored, interruptErr
		}
		_ = emit(domain.ExternalEvent{EventType: "run_interrupted", Payload: map[string]any{"run_id": run.RunID, "attempt_id": run.AttemptID, "reason": "runtime_stream_ended"}})
		return run, nil
	}
	if errors.Is(runtimeErr, context.Canceled) || errors.Is(runtimeErr, context.DeadlineExceeded) {
		run, interruptErr := c.Repo.InterruptRun(context.WithoutCancel(ctx), owner, initial.RunID, stored.AttemptID, "client_disconnected")
		if interruptErr == nil {
			return run, runtimeErr
		}
		return *stored, interruptErr
	}
	run, interruptErr := c.Repo.InterruptRun(context.WithoutCancel(ctx), owner, initial.RunID, stored.AttemptID, "runtime_unavailable")
	if interruptErr != nil {
		return *stored, interruptErr
	}
	_ = emit(domain.ExternalEvent{EventType: "run_interrupted", Payload: map[string]any{"run_id": run.RunID, "attempt_id": run.AttemptID, "reason": "runtime_unavailable"}})
	return run, runtimeErr
}

func (c *ControlPlane) Resume(ctx context.Context, owner, runID string, emit func(domain.ExternalEvent) error) (domain.Run, error) {
	run, err := c.Repo.PrepareResume(ctx, owner, runID)
	if err != nil {
		return domain.Run{}, err
	}
	if err = emit(domain.ExternalEvent{EventType: "run_resumed", Payload: map[string]any{"run_id": run.RunID, "task_id": run.TaskID, "attempt_id": run.AttemptID}}); err != nil {
		return run, err
	}
	if err = emit(runStatusEvent(run)); err != nil {
		return run, err
	}
	execute, err := c.Repo.RunExecution(ctx, run)
	if err != nil {
		return run, err
	}
	if !execute.Resume {
		return run, errors.New("resume execution snapshot is not marked for checkpoint recovery")
	}
	err = c.Runtime.Execute(ctx, owner, execute, func(event domain.RunEvent) error { return c.accept(ctx, owner, event, emit) })
	return c.finishRuntimeCall(ctx, owner, run, err, emit)
}

func (c *ControlPlane) Cancel(ctx context.Context, owner, runID string) (domain.Run, error) {
	run, notify, err := c.Repo.CancelRun(ctx, owner, runID, "user_cancelled")
	if err != nil || !notify {
		return run, err
	}
	command := domain.RunControl{SchemaVersion: "1.0", RunID: run.RunID, AttemptID: run.AttemptID, Reason: "user_cancelled"}
	runtimeErr := c.Runtime.Control(ctx, owner, "cancel", command, func(event domain.RunEvent) error {
		_, _, _, applyErr := c.Repo.ApplyRunEvent(ctx, event)
		if errors.Is(applyErr, domain.ErrTerminalRun) {
			return nil
		}
		return applyErr
	})
	if runtimeErr != nil && !errors.Is(runtimeErr, context.Canceled) {
		// Cancellation is authoritative in Go; the runtime notification is best effort.
		return run, nil
	}
	stored, getErr := c.Repo.GetRun(ctx, owner, runID)
	if getErr == nil && stored != nil {
		return *stored, nil
	}
	return run, getErr
}

func runStatusEvent(run domain.Run) domain.ExternalEvent {
	return domain.ExternalEvent{EventType: "run_status", Payload: map[string]any{
		"run_id": run.RunID, "attempt_id": run.AttemptID, "status": run.Status, "stage": run.Stage,
		"end_reason": run.EndReason, "error_type": run.ErrorType,
	}}
}

func answerEvent(message domain.Message) domain.ExternalEvent {
	return domain.ExternalEvent{EventType: "answer", Payload: map[string]any{
		"text": message.Content, "citations": message.Citations, "msg_id": message.MessageID,
		"task_id": message.TaskID, "created_at": message.CreatedAt,
	}}
}

func (c *ControlPlane) GetRun(ctx context.Context, owner, runID string) (*domain.RunView, error) {
	run, err := c.Repo.GetRun(ctx, owner, runID)
	if err != nil || run == nil {
		return nil, err
	}
	view := domain.ViewRun(*run, c.Clock())
	return &view, nil
}

func (c *ControlPlane) ListTasks(ctx context.Context, owner string, limit int) ([]domain.Task, error) {
	if limit < 1 {
		limit = 1
	}
	if limit > 200 {
		limit = 200
	}
	return c.Repo.ListTasks(ctx, owner, limit)
}

func (c *ControlPlane) TaskDetail(ctx context.Context, owner, taskID string) (*domain.TaskDetail, error) {
	return c.Repo.TaskDetail(ctx, owner, taskID)
}

func (c *ControlPlane) CreateMemory(ctx context.Context, owner, content, kind string, profileField *string, expiresAt *float64, sourceTaskID, sourceMessageID *string) (domain.Memory, bool, error) {
	content = strings.TrimSpace(content)
	if content == "" || len([]rune(content)) > 800 {
		return domain.Memory{}, false, domain.ErrInvalid
	}
	if kind == "" || kind == "auto" {
		kind = "business_context"
		for _, marker := range []string{"偏好", "喜欢", "回答", "优先", "希望你"} {
			if strings.Contains(content, marker) {
				kind = "preference"
				break
			}
		}
	}
	if kind == "fact" {
		kind = "business_context"
	}
	if kind != "profile" && kind != "preference" && kind != "business_context" {
		return domain.Memory{}, false, domain.ErrInvalid
	}
	now := float64(c.Clock().UnixNano()) / 1e9
	memoryID, err := RandomID()
	if err != nil {
		return domain.Memory{}, false, err
	}
	memoryID = "mem_" + memoryID
	if sourceMessageID == nil || *sourceMessageID == "" {
		synthetic := "manual:" + memoryID
		sourceMessageID = &synthetic
	}
	if expiresAt != nil && *expiresAt <= now {
		return domain.Memory{}, false, domain.ErrInvalid
	}
	var retention *float64
	var review *float64
	if expiresAt == nil && kind == "business_context" {
		value := now + 90*86400
		expiresAt = &value
	}
	if expiresAt == nil && kind == "profile" && (profileField == nil || *profileField != "name") {
		value := now + 365*86400
		expiresAt = &value
		reviewValue := now + 180*86400
		review = &reviewValue
	}
	if expiresAt != nil {
		value := (*expiresAt - now) / 86400
		retention = &value
	}
	var profileValue *string
	if kind == "profile" {
		if profileField == nil || (*profileField != "name" && *profileField != "role" && *profileField != "organization") {
			return domain.Memory{}, false, domain.ErrInvalid
		}
		value := content
		profileValue = &value
	}
	sourceType := "manual"
	if sourceTaskID != nil {
		sourceType = "user_message"
	}
	memory := domain.Memory{
		MemoryID: memoryID, OwnerID: owner, Kind: kind, Content: content, ProfileField: profileField, Value: profileValue,
		SourceType: sourceType, SourceTaskID: sourceTaskID, SourceMessageID: *sourceMessageID, SourceQuote: content,
		Revision: 1, CreatedAt: now, UpdatedAt: now, ExpiresAt: expiresAt, ReviewAt: review, RetentionDays: retention,
		SchemaVersion: 2, ParserVersion: 1,
	}
	return c.Repo.CreateMemory(ctx, memory)
}

func (c *ControlPlane) MemoryPolicy() map[string]any {
	return map[string]any{"business_ttl_days": 90, "profile_ttl_days": 365, "profile_review_days": 180}
}

func (c *ControlPlane) EnsureAccount(user *domain.User) error {
	if user == nil {
		return domain.ErrNotFound
	}
	if user.Provider == "anonymous" {
		return domain.ErrForbidden
	}
	return nil
}

func WrapInvalid(message string) error { return fmt.Errorf("%w: %s", domain.ErrInvalid, message) }
