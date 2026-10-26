package infra

import (
	"context"
	"path/filepath"
	"riskpilot/api-go/internal/domain"
	"sync"
	"testing"
	"time"
)

func TestDurableOAuthStateIsConsumedOnce(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.sqlite3")
	first, err := OpenStore(path)
	if err != nil {
		t.Fatal(err)
	}
	if err = first.SaveState(context.Background(), "random-state"); err != nil {
		t.Fatal(err)
	}
	first.DB.Close()
	second, err := OpenStore(path)
	if err != nil {
		t.Fatal(err)
	}
	defer second.DB.Close()
	var wait sync.WaitGroup
	results := make(chan bool, 4)
	for i := 0; i < 4; i++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			valid, err := second.ConsumeState(context.Background(), "random-state")
			if err != nil {
				t.Error(err)
			}
			results <- valid
		}()
	}
	wait.Wait()
	close(results)
	count := 0
	for valid := range results {
		if valid {
			count++
		}
	}
	if count != 1 {
		t.Fatalf("state consumed %d times", count)
	}
}

func TestIdentityCollisionCannotOverwriteOtherProvider(t *testing.T) {
	store, err := OpenStore(filepath.Join(t.TempDir(), "users.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.DB.Close()
	now := float64(time.Now().Unix())
	user := domain.User{ID: "github:id:99", Provider: "anonymous", ProviderID: "other", Name: "existing", Created: now, Active: now}
	if _, err = store.SaveUser(context.Background(), user); err != nil {
		t.Fatal(err)
	}
	user.Provider = "github"
	user.ProviderID = "99"
	user.Name = "replacement"
	if _, err = store.SaveUser(context.Background(), user); err == nil {
		t.Fatal("identity collision accepted")
	}
	stored, err := store.User(context.Background(), user.ID)
	if err != nil || stored.Name != "existing" {
		t.Fatal("existing identity changed")
	}
}
