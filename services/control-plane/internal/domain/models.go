package domain

import (
	"encoding/json"
	"errors"
	"fmt"
	"time"
)

var (
	ErrNotFound                = errors.New("not found")
	ErrConflict                = errors.New("conflict")
	ErrForbidden               = errors.New("forbidden")
	ErrInvalid                 = errors.New("invalid input")
	ErrRuntime                 = errors.New("agent runtime unavailable")
	ErrTerminalRun             = errors.New("run is already terminal")
	ErrEventSequence           = errors.New("invalid event sequence")
	ErrIdempotency             = errors.New("idempotency key reused with a different request")
	ErrRevision                = errors.New("revision conflict")
	ErrUnsupportedDocumentType = errors.New("unsupported document type")
	ErrInvalidDocumentContent  = errors.New("invalid document content")
	ErrUploadTooLarge          = errors.New("upload too large")
)

type Task struct {
	TaskID         string         `json:"task_id"`
	OwnerID        string         `json:"owner_id"`
	Title          string         `json:"title"`
	State          string         `json:"state"`
	UserGoal       string         `json:"user_goal"`
	CollectedFacts map[string]any `json:"collected_facts"`
	CreatedAt      float64        `json:"created_at"`
	UpdatedAt      float64        `json:"updated_at"`
}

type Citation struct {
	CitationID      string  `json:"citation_id"`
	SourceID        string  `json:"source_id"`
	SourceType      string  `json:"source_type"`
	SourceName      string  `json:"source_name"`
	Title           string  `json:"title"`
	SourceURL       *string `json:"source_url"`
	SourceVersion   *string `json:"source_version"`
	TextSnippet     string  `json:"text_snippet"`
	ChunkID         *string `json:"chunk_id"`
	DocumentID      *string `json:"document_id"`
	DocumentVersion *string `json:"document_version_id"`
	PageNumber      *int    `json:"page_number"`
	SourceSHA256    *string `json:"source_sha256"`
	ClauseID        *string `json:"clause_id"`
}

type Message struct {
	MessageID string     `json:"msg_id"`
	TaskID    string     `json:"-"`
	Role      string     `json:"role"`
	Content   string     `json:"content"`
	Citations []Citation `json:"citations"`
	CreatedAt float64    `json:"created_at"`
}

type Run struct {
	RunID                     string     `json:"run_id"`
	AttemptID                 int        `json:"attempt_id,omitempty"`
	ThreadID                  string     `json:"-"`
	OwnerID                   string     `json:"-"`
	TaskID                    string     `json:"task_id"`
	Question                  string     `json:"-"`
	InputMessageID            string     `json:"-"`
	AttachmentDocumentIDs     []string   `json:"attachment_doc_ids"`
	Status                    string     `json:"status"`
	Stage                     string     `json:"stage"`
	Evidence                  []Citation `json:"-"`
	VerifiedCitationIDs       []string   `json:"verified_citation_ids"`
	VerifiedContextMessageIDs []string   `json:"verified_context_message_ids"`
	ClarificationFields       []string   `json:"clarification_fields"`
	MaxDurationSeconds        float64    `json:"max_duration_seconds"`
	RemainingSeconds          float64    `json:"remaining_seconds"`
	DeadlineAt                *float64   `json:"-"`
	MaxModelCalls             int        `json:"max_model_calls"`
	MaxToolCalls              int        `json:"max_tool_calls"`
	ModelCallCount            int        `json:"model_call_count"`
	ToolCallCount             int        `json:"tool_call_count"`
	ModelRetryCount           int        `json:"model_retry_count"`
	ToolRetryCount            int        `json:"tool_retry_count"`
	InputTokens               *int       `json:"input_tokens"`
	OutputTokens              *int       `json:"output_tokens"`
	UsageReportedModelCalls   int        `json:"-"`
	AnswerMessageID           *string    `json:"answer_message_id"`
	ErrorType                 *string    `json:"error_type"`
	EndReason                 *string    `json:"end_reason"`
	CancelRequestedAt         *float64   `json:"cancel_requested_at"`
	FinishedAt                *float64   `json:"finished_at"`
	NextEventSeq              int        `json:"-"`
	CreatedAt                 float64    `json:"created_at"`
	UpdatedAt                 float64    `json:"updated_at"`
}

func (r Run) Terminal() bool {
	switch r.Status {
	case "cancelled", "timed_out", "budget_exhausted", "completed", "failed":
		return true
	default:
		return false
	}
}

func (r Run) UsageStatus() string {
	if r.UsageReportedModelCalls == 0 {
		return "unknown"
	}
	if r.UsageReportedModelCalls == r.ModelCallCount {
		return "complete"
	}
	return "partial"
}

type RunView struct {
	Run
	EvidenceCount int      `json:"evidence_count"`
	EvidenceIDs   []string `json:"evidence_ids"`
	UsageStatus   string   `json:"usage_status"`
}

func ViewRun(run Run, now time.Time) RunView {
	if run.DeadlineAt != nil && run.Status == "running" {
		remaining := *run.DeadlineAt - float64(now.UnixNano())/1e9
		if remaining < 0 {
			remaining = 0
		}
		if remaining < run.RemainingSeconds {
			run.RemainingSeconds = remaining
		}
	}
	ids := make([]string, 0, len(run.Evidence))
	for _, citation := range run.Evidence {
		ids = append(ids, citation.CitationID)
	}
	return RunView{Run: run, EvidenceCount: len(run.Evidence), EvidenceIDs: ids, UsageStatus: run.UsageStatus()}
}

type Feedback struct {
	MessageID string  `json:"msg_id"`
	TaskID    string  `json:"task_id"`
	OwnerID   string  `json:"-"`
	Rating    *string `json:"rating"`
	CreatedAt float64 `json:"-"`
	UpdatedAt float64 `json:"-"`
}

type Memory struct {
	MemoryID        string   `json:"memory_id"`
	OwnerID         string   `json:"owner_id"`
	Kind            string   `json:"kind"`
	Content         string   `json:"content"`
	ProfileField    *string  `json:"profile_field"`
	Value           *string  `json:"value"`
	SourceType      string   `json:"source_type"`
	SourceTaskID    *string  `json:"source_task_id"`
	SourceMessageID string   `json:"source_message_id"`
	SourceQuote     string   `json:"source_quote"`
	Revision        int      `json:"revision"`
	CreatedAt       float64  `json:"created_at"`
	UpdatedAt       float64  `json:"updated_at"`
	ExpiresAt       *float64 `json:"expires_at"`
	ReviewAt        *float64 `json:"review_at"`
	RetentionDays   *float64 `json:"retention_days"`
	SchemaVersion   int      `json:"schema_version"`
	ParserVersion   int      `json:"profile_parser_version"`
	NeedsReview     bool     `json:"needs_review"`
}

type Document struct {
	DocumentID       string  `json:"document_id"`
	OwnerID          string  `json:"owner_id"`
	LogicalName      string  `json:"logical_name"`
	DocumentType     string  `json:"document_type"`
	Status           string  `json:"status"`
	CreatedBy        string  `json:"created_by"`
	CurrentVersionID string  `json:"current_version_id"`
	CreatedAt        float64 `json:"created_at"`
	UpdatedAt        float64 `json:"updated_at"`
}

type DocumentVersion struct {
	VersionID     string  `json:"version_id"`
	DocumentID    string  `json:"document_id"`
	VersionNumber int     `json:"version_number"`
	SHA256        string  `json:"sha256"`
	MIMEType      string  `json:"mime_type"`
	SizeBytes     int64   `json:"size_bytes"`
	ParserVersion string  `json:"parser_version"`
	PageCount     *int    `json:"page_count"`
	CreatedAt     float64 `json:"created_at"`
}

type ProcessingJob struct {
	JobID             string   `json:"job_id"`
	DocumentVersionID string   `json:"document_version_id"`
	Status            string   `json:"status"`
	CurrentStage      string   `json:"current_stage"`
	Progress          float64  `json:"progress"`
	ErrorCode         *string  `json:"error_code"`
	ErrorMessage      *string  `json:"error_message"`
	RetryCount        int      `json:"retry_count"`
	Revision          int      `json:"revision"`
	CreatedAt         float64  `json:"created_at"`
	UpdatedAt         float64  `json:"updated_at"`
	StartedAt         *float64 `json:"started_at"`
	CompletedAt       *float64 `json:"completed_at"`
}

type FileRecord struct {
	Document Document        `json:"document"`
	Version  DocumentVersion `json:"version,omitempty"`
	Job      *ProcessingJob  `json:"job"`
	Content  []byte          `json:"-"`
}

type KBDocument struct {
	DocumentID   string  `json:"-"`
	VersionID    string  `json:"-"`
	OwnerID      *string `json:"owner_id"`
	SourceName   string  `json:"source_name"`
	SourceType   string  `json:"source_type"`
	Title        string  `json:"title"`
	SourceURL    *string `json:"source_url"`
	Category     string  `json:"category"`
	MIMEType     string  `json:"-"`
	SHA256       string  `json:"-"`
	ChunkCount   int     `json:"chunk_count"`
	Status       string  `json:"-"`
	ErrorCode    *string `json:"-"`
	ErrorMessage *string `json:"-"`
	CreatedAt    float64 `json:"-"`
	UpdatedAt    float64 `json:"-"`
}

type DocumentProcess struct {
	SchemaVersion string  `json:"schema_version"`
	Operation     string  `json:"operation"`
	DocumentID    string  `json:"document_id"`
	VersionID     string  `json:"version_id"`
	ActorID       string  `json:"actor_id"`
	OwnerID       *string `json:"owner_id"`
	SourceName    string  `json:"source_name"`
	SourceType    string  `json:"source_type"`
	SourceURL     *string `json:"source_url"`
	Category      string  `json:"category"`
	MIMEType      string  `json:"mime_type"`
	SHA256        string  `json:"sha256"`
	ContentBase64 *string `json:"content_base64"`
}

type DocumentResult struct {
	SchemaVersion string  `json:"schema_version"`
	DocumentID    string  `json:"document_id"`
	Status        string  `json:"status"`
	Title         string  `json:"title"`
	ChunkCount    int     `json:"chunk_count"`
	ErrorCode     *string `json:"error_code"`
	Message       string  `json:"message"`
}

type AttachmentVersion struct {
	DocumentID string `json:"document_id"`
	VersionID  string `json:"version_id"`
	SHA256     string `json:"sha256"`
}

type MemoryReference struct {
	MemoryID        string `json:"memory_id"`
	Revision        int    `json:"revision"`
	Content         string `json:"content"`
	SourceMessageID string `json:"source_message_id"`
	SourceQuote     string `json:"source_quote"`
}

type ContextMessage struct {
	MessageID string `json:"message_id"`
	Role      string `json:"role"`
	Content   string `json:"content"`
}

type RunBudget struct {
	DeadlineAt    float64 `json:"deadline_at"`
	MaxModelCalls int     `json:"max_model_calls"`
	MaxToolCalls  int     `json:"max_tool_calls"`
}

// RunExecute is exactly contracts/internal/v1/run-execute.schema.json.
type RunExecute struct {
	SchemaVersion       string              `json:"schema_version"`
	RunID               string              `json:"run_id"`
	AttemptID           int                 `json:"attempt_id"`
	ThreadID            string              `json:"thread_id"`
	OwnerID             string              `json:"owner_id"`
	TaskID              string              `json:"task_id"`
	InputMessageID      string              `json:"input_message_id"`
	Question            string              `json:"question"`
	AttachmentVersions  []AttachmentVersion `json:"attachment_versions"`
	MemorySnapshot      []MemoryReference   `json:"memory_snapshot"`
	RecentMessages      []ContextMessage    `json:"recent_messages,omitempty"`
	ConversationSummary string              `json:"conversation_summary,omitempty"`
	Resume              bool                `json:"resume,omitempty"`
	Budget              RunBudget           `json:"budget"`
}

// RunControl is exactly contracts/internal/v1/run-control.schema.json.
type RunControl struct {
	SchemaVersion string `json:"schema_version"`
	RunID         string `json:"run_id"`
	AttemptID     int    `json:"attempt_id"`
	Reason        string `json:"reason"`
}

type UsagePayload struct {
	ModelCalls   int  `json:"model_calls"`
	ToolCalls    int  `json:"tool_calls"`
	InputTokens  *int `json:"input_tokens"`
	OutputTokens *int `json:"output_tokens"`
}

type ResultProposal struct {
	Status                    string           `json:"status"`
	Answer                    string           `json:"answer"`
	Citations                 []map[string]any `json:"citations"`
	VerifiedCitationIDs       []string         `json:"verified_citation_ids"`
	VerifiedContextMessageIDs []string         `json:"verified_context_message_ids"`
	ClarificationFields       []string         `json:"clarification_fields"`
	ErrorType                 *string          `json:"error_type"`
	EndReason                 string           `json:"end_reason"`
}

// RunEvent is exactly contracts/internal/v1/run-event.schema.json at the envelope level.
type RunEvent struct {
	SchemaVersion string          `json:"schema_version"`
	RunID         string          `json:"run_id"`
	AttemptID     int             `json:"attempt_id"`
	EventSeq      int             `json:"event_seq"`
	EventType     string          `json:"event_type"`
	Payload       json.RawMessage `json:"payload"`
}

func (e RunEvent) Validate(run Run) error {
	if e.SchemaVersion != "1.0" || e.RunID != run.RunID || e.AttemptID != run.AttemptID || e.EventSeq < 1 {
		return fmt.Errorf("%w: event envelope does not match active run attempt", ErrConflict)
	}
	switch e.EventType {
	case "stage", "tool_call", "tool_result", "tool_error", "draft_delta", "draft_discarded", "usage", "interrupted", "result_proposal":
		return nil
	default:
		return fmt.Errorf("%w: unsupported event_type %q", ErrInvalid, e.EventType)
	}
}

type ExternalEvent struct {
	EventType string         `json:"event_type"`
	Payload   map[string]any `json:"payload"`
}

type BeginRun struct {
	Task        Task
	Run         Run
	TaskCreated bool
	Existing    bool
	Events      []RunEvent
}

type TaskDetail struct {
	Task                  Task      `json:"task"`
	Messages              []Message `json:"messages"`
	AttachmentDocumentIDs []string  `json:"attachment_doc_ids"`
	LatestRun             *RunView  `json:"latest_run"`
}
