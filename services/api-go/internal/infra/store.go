package infra

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	_ "github.com/mattn/go-sqlite3"
	"net/url"
	"os"
	"path/filepath"
	"riskpilot/api-go/internal/domain"
	"time"
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
            request_id TEXT NOT NULL, created_at INTEGER NOT NULL);`)
	if err != nil {
		db.Close()
		return nil, err
	}
	return &Store{DB: db}, nil
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
