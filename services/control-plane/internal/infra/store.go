package infra

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"riskpilot/control-plane/internal/domain"
)

type Store struct{ DB *sql.DB }

func OpenStore(path string) (*Store, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return nil, err
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return nil, err
	}
	dsn := (&url.URL{Scheme: "file", Path: absolute}).String() + "?_busy_timeout=5000&_journal_mode=WAL&_foreign_keys=on&_txlock=immediate"
	db, err := sql.Open("sqlite3", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(4)
	_, err = db.Exec(`CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY, provider TEXT NOT NULL, provider_id TEXT NOT NULL,
        email TEXT, display_name TEXT NOT NULL, avatar_url TEXT,
        created_at REAL NOT NULL, last_active_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_users_provider ON users(provider,provider_id);
        CREATE TABLE IF NOT EXISTS gateway_oauth_states (
            state_hash TEXT PRIMARY KEY, expires_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS gateway_auth_events (
            event_id INTEGER PRIMARY KEY, action TEXT NOT NULL, actor_id TEXT,
	            request_id TEXT NOT NULL, created_at INTEGER NOT NULL);

		CREATE TABLE IF NOT EXISTS tasks (
			task_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
			state TEXT NOT NULL DEFAULT 'planning', user_goal TEXT NOT NULL DEFAULT '',
			collected_facts TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, updated_at REAL NOT NULL);
		CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner_id, updated_at DESC);
		CREATE TABLE IF NOT EXISTS messages (
			msg_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
			tool_call_id TEXT, citations TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL,
			FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE);
		CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id, created_at);
		CREATE TABLE IF NOT EXISTS conversation_runs (
			run_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL UNIQUE, owner_id TEXT NOT NULL,
			task_id TEXT NOT NULL, question TEXT NOT NULL, input_message_id TEXT NOT NULL,
			attachment_doc_ids TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL, stage TEXT NOT NULL,
			evidence_json TEXT NOT NULL DEFAULT '[]', verified_citation_ids TEXT NOT NULL DEFAULT '[]',
			verified_context_message_ids TEXT NOT NULL DEFAULT '[]', clarification_fields TEXT NOT NULL DEFAULT '[]',
			max_duration_seconds REAL NOT NULL DEFAULT 120, remaining_seconds REAL NOT NULL DEFAULT 120,
			deadline_at REAL, max_model_calls INTEGER NOT NULL DEFAULT 12, max_tool_calls INTEGER NOT NULL DEFAULT 6,
			model_call_count INTEGER NOT NULL DEFAULT 0, tool_call_count INTEGER NOT NULL DEFAULT 0,
			model_retry_count INTEGER NOT NULL DEFAULT 0, tool_retry_count INTEGER NOT NULL DEFAULT 0,
			input_tokens INTEGER, output_tokens INTEGER, usage_reported_model_calls INTEGER NOT NULL DEFAULT 0,
			answer_message_id TEXT, error_type TEXT, end_reason TEXT, cancel_requested_at REAL, finished_at REAL,
			attempt_id INTEGER NOT NULL DEFAULT 1, next_event_seq INTEGER NOT NULL DEFAULT 1,
			request_hash TEXT NOT NULL DEFAULT '', idempotency_key_hash TEXT NOT NULL DEFAULT '',
			created_at REAL NOT NULL, updated_at REAL NOT NULL,
			FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
			FOREIGN KEY(input_message_id) REFERENCES messages(msg_id) ON DELETE CASCADE,
			FOREIGN KEY(answer_message_id) REFERENCES messages(msg_id) ON DELETE SET NULL);
		CREATE INDEX IF NOT EXISTS idx_conversation_runs_task ON conversation_runs(task_id, updated_at DESC);
		CREATE TABLE IF NOT EXISTS message_citations (
			message_id TEXT NOT NULL, citation_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
			payload_json TEXT NOT NULL, PRIMARY KEY(message_id, citation_id),
			FOREIGN KEY(message_id) REFERENCES messages(msg_id) ON DELETE CASCADE);
		CREATE TABLE IF NOT EXISTS run_attempt_events (
			run_id TEXT NOT NULL, attempt_id INTEGER NOT NULL, event_seq INTEGER NOT NULL,
			event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at REAL NOT NULL,
			PRIMARY KEY(run_id, attempt_id, event_seq),
			FOREIGN KEY(run_id) REFERENCES conversation_runs(run_id) ON DELETE CASCADE);
		CREATE TABLE IF NOT EXISTS control_idempotency (
			owner_id TEXT NOT NULL, key_hash TEXT NOT NULL, request_hash TEXT NOT NULL, run_id TEXT NOT NULL,
			created_at REAL NOT NULL, PRIMARY KEY(owner_id, key_hash),
			FOREIGN KEY(run_id) REFERENCES conversation_runs(run_id) ON DELETE CASCADE);
		CREATE TABLE IF NOT EXISTS control_run_memory_snapshots (
			run_id TEXT NOT NULL, attempt_id INTEGER NOT NULL, memory_id TEXT NOT NULL,
			revision INTEGER NOT NULL, payload_json TEXT NOT NULL,
			PRIMARY KEY(run_id, attempt_id, memory_id),
			FOREIGN KEY(run_id) REFERENCES conversation_runs(run_id) ON DELETE CASCADE);
		CREATE TABLE IF NOT EXISTS control_run_attachment_snapshots (
			run_id TEXT NOT NULL, attempt_id INTEGER NOT NULL, document_id TEXT NOT NULL,
			version_id TEXT NOT NULL, sha256 TEXT NOT NULL,
			PRIMARY KEY(run_id, attempt_id, document_id),
			FOREIGN KEY(run_id) REFERENCES conversation_runs(run_id) ON DELETE CASCADE);
		CREATE TABLE IF NOT EXISTS control_run_execution_snapshots (
			run_id TEXT NOT NULL, attempt_id INTEGER NOT NULL, payload_json TEXT NOT NULL,
			created_at REAL NOT NULL, PRIMARY KEY(run_id, attempt_id),
			FOREIGN KEY(run_id) REFERENCES conversation_runs(run_id) ON DELETE CASCADE);

		CREATE TABLE IF NOT EXISTS account_memories (
			memory_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL);
		CREATE INDEX IF NOT EXISTS idx_account_memories_owner ON account_memories(owner_id);
		CREATE TABLE IF NOT EXISTS memory_settings (
			owner_id TEXT PRIMARY KEY, use_saved_memory INTEGER NOT NULL DEFAULT 1, updated_at REAL NOT NULL);
		CREATE TABLE IF NOT EXISTS message_feedback (
			msg_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, owner_id TEXT NOT NULL,
			rating TEXT NOT NULL CHECK(rating IN ('up','down')), created_at REAL NOT NULL, updated_at REAL NOT NULL);
		CREATE INDEX IF NOT EXISTS idx_feedback_owner ON message_feedback(owner_id);

		CREATE TABLE IF NOT EXISTS chat_documents (
			document_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, logical_name TEXT NOT NULL,
			document_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'uploaded', created_by TEXT NOT NULL,
			current_version_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
		CREATE INDEX IF NOT EXISTS idx_chat_documents_owner ON chat_documents(owner_id, updated_at DESC);
		CREATE TABLE IF NOT EXISTS chat_document_versions (
			version_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, version_number INTEGER NOT NULL,
			object_key TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL, mime_type TEXT NOT NULL,
			size_bytes INTEGER NOT NULL, parser_version TEXT NOT NULL DEFAULT '', page_count INTEGER,
			created_at REAL NOT NULL, UNIQUE(document_id, version_number),
			FOREIGN KEY(document_id) REFERENCES chat_documents(document_id) ON DELETE CASCADE);
		CREATE TABLE IF NOT EXISTS chat_processing_jobs (
			job_id TEXT PRIMARY KEY, document_version_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
			current_stage TEXT NOT NULL DEFAULT 'extract_structure', progress REAL NOT NULL DEFAULT 0,
			error_code TEXT, error_message TEXT, retry_count INTEGER NOT NULL DEFAULT 0,
			revision INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL,
			started_at REAL, completed_at REAL,
			FOREIGN KEY(document_version_id) REFERENCES chat_document_versions(version_id) ON DELETE CASCADE);
		CREATE TABLE IF NOT EXISTS file_objects (
			version_id TEXT PRIMARY KEY, content BLOB NOT NULL,
			FOREIGN KEY(version_id) REFERENCES chat_document_versions(version_id) ON DELETE CASCADE);
		CREATE TABLE IF NOT EXISTS kb_documents (
			document_id TEXT PRIMARY KEY, version_id TEXT NOT NULL UNIQUE, owner_id TEXT,
			source_name TEXT NOT NULL, source_type TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
			source_url TEXT, category TEXT NOT NULL DEFAULT '', mime_type TEXT NOT NULL,
			sha256 TEXT NOT NULL, chunk_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
			error_code TEXT, error_message TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
			UNIQUE(owner_id, source_name));
		CREATE INDEX IF NOT EXISTS idx_kb_documents_scope ON kb_documents(owner_id, updated_at DESC);
		CREATE UNIQUE INDEX IF NOT EXISTS idx_kb_documents_unique_source ON kb_documents(COALESCE(owner_id,''), source_name);
		CREATE TABLE IF NOT EXISTS kb_document_objects (
			version_id TEXT PRIMARY KEY, content BLOB,
			FOREIGN KEY(version_id) REFERENCES kb_documents(version_id) ON DELETE CASCADE);
		CREATE TABLE IF NOT EXISTS task_attachments (
			task_id TEXT NOT NULL, document_id TEXT NOT NULL, selected_at REAL NOT NULL, selection_order INTEGER NOT NULL,
			PRIMARY KEY(task_id, document_id),
			FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
			FOREIGN KEY(document_id) REFERENCES chat_documents(document_id) ON DELETE CASCADE);`)
	if err != nil {
		db.Close()
		return nil, err
	}
	for name, definition := range map[string]string{
		"attempt_id":           "INTEGER NOT NULL DEFAULT 1",
		"next_event_seq":       "INTEGER NOT NULL DEFAULT 1",
		"request_hash":         "TEXT NOT NULL DEFAULT ''",
		"idempotency_key_hash": "TEXT NOT NULL DEFAULT ''",
	} {
		if err = ensureColumn(db, "conversation_runs", name, definition); err != nil {
			db.Close()
			return nil, err
		}
	}
	if _, err = db.Exec("CREATE INDEX IF NOT EXISTS idx_conversation_runs_owner_idempotency ON conversation_runs(owner_id, idempotency_key_hash)"); err != nil {
		db.Close()
		return nil, err
	}
	return &Store{DB: db}, nil
}

func ensureColumn(db *sql.DB, table, column, definition string) error {
	rows, err := db.Query("PRAGMA table_info(" + table + ")")
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var cid int
		var name, kind string
		var notNull, primary int
		var defaultValue any
		if err = rows.Scan(&cid, &name, &kind, &notNull, &defaultValue, &primary); err != nil {
			return err
		}
		if name == column {
			return nil
		}
	}
	if err = rows.Err(); err != nil {
		return err
	}
	_, err = db.Exec("ALTER TABLE " + table + " ADD COLUMN " + column + " " + definition)
	return err
}

type rowScanner interface{ Scan(...any) error }

func scanUser(row rowScanner) (*domain.User, error) {
	user := domain.User{}
	err := row.Scan(&user.ID, &user.Provider, &user.ProviderID, &user.Email, &user.Name, &user.Avatar, &user.Created, &user.Active)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &user, nil
}

const userColumns = "user_id,provider,provider_id,email,display_name,avatar_url,created_at,last_active_at"

func (s *Store) User(ctx context.Context, id string) (*domain.User, error) {
	return scanUser(s.DB.QueryRowContext(ctx, "SELECT "+userColumns+" FROM users WHERE user_id=?", id))
}

func (s *Store) SaveUser(ctx context.Context, user domain.User) (*domain.User, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	previous, err := scanUser(tx.QueryRowContext(ctx, "SELECT "+userColumns+" FROM users WHERE provider=? AND provider_id=? ORDER BY created_at LIMIT 1", user.Provider, user.ProviderID))
	if err != nil {
		return nil, err
	}
	if previous != nil {
		user.ID = previous.ID
		user.Created = previous.Created
	}
	collision, err := scanUser(tx.QueryRowContext(ctx, "SELECT "+userColumns+" FROM users WHERE user_id=?", user.ID))
	if err != nil {
		return nil, err
	}
	if collision != nil && (collision.Provider != user.Provider || collision.ProviderID != user.ProviderID) {
		return nil, errors.New("provider identity collision")
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO users (`+userColumns+`) VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET email=excluded.email,display_name=excluded.display_name,
        avatar_url=excluded.avatar_url,last_active_at=excluded.last_active_at`, user.ID, user.Provider, user.ProviderID, user.Email, user.Name, user.Avatar, user.Created, user.Active)
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return &user, nil
}

func hashState(value string) string {
	hash := sha256.Sum256([]byte(value))
	return hex.EncodeToString(hash[:])
}
func (s *Store) SaveState(ctx context.Context, state string) error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, "DELETE FROM gateway_oauth_states WHERE expires_at<=?", time.Now().Unix()); err != nil {
		return err
	}
	var count int
	if err = tx.QueryRowContext(ctx, "SELECT COUNT(*) FROM gateway_oauth_states").Scan(&count); err != nil {
		return err
	}
	if count >= 10000 {
		return errors.New("OAuth state capacity exceeded")
	}
	_, err = tx.ExecContext(ctx, "INSERT INTO gateway_oauth_states VALUES (?,?)", hashState(state), time.Now().Add(10*time.Minute).Unix())
	if err != nil {
		return err
	}
	return tx.Commit()
}
func (s *Store) ConsumeState(ctx context.Context, state string) (bool, error) {
	result, err := s.DB.ExecContext(ctx, "DELETE FROM gateway_oauth_states WHERE state_hash=? AND expires_at>?", hashState(state), time.Now().Unix())
	if err != nil {
		return false, err
	}
	n, err := result.RowsAffected()
	return n == 1, err
}
func (s *Store) Audit(ctx context.Context, action, actor, requestID string) error {
	_, err := s.DB.ExecContext(ctx, "INSERT INTO gateway_auth_events(action,actor_id,request_id,created_at) VALUES (?,?,?,?)", action, actor, requestID, time.Now().Unix())
	return err
}
