package main

import (
	"context"
	"errors"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/joho/godotenv"
	"riskpilot/control-plane/internal/api"
	"riskpilot/control-plane/internal/app"
	"riskpilot/control-plane/internal/infra"
)

func main() {
	log := api.DefaultLogger()
	root := os.Getenv("RISKPILOT_ROOT")
	if root == "" {
		root = "."
	}
	if err := godotenv.Load(filepath.Join(root, ".env")); err != nil && !errors.Is(err, os.ErrNotExist) {
		log.Error("cannot parse .env")
		os.Exit(1)
	}
	cfg, err := app.LoadConfig()
	if err != nil {
		log.Error("invalid configuration", "error", err.Error())
		os.Exit(1)
	}
	store, err := infra.OpenStore(cfg.Database)
	if err != nil {
		log.Error("cannot open database")
		os.Exit(1)
	}
	defer store.DB.Close()
	runtime, err := infra.NewRuntimeClient(cfg.RuntimeURL, cfg.ServiceSecret, cfg.RuntimeTimeout)
	if err != nil {
		log.Error("cannot create agent runtime client", "error", err.Error())
		os.Exit(1)
	}
	handler, err := api.New(cfg, store, runtime, log)
	if err != nil {
		log.Error("cannot create control plane")
		os.Exit(1)
	}
	server := &http.Server{Addr: cfg.Listen, Handler: handler.Router(), ReadHeaderTimeout: 5 * time.Second, WriteTimeout: cfg.RuntimeTimeout + 5*time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 1 << 20}
	signals, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	failures := make(chan error, 1)
	go func() { failures <- server.ListenAndServe() }()
	log.Info("Go control plane listening", "address", cfg.Listen, "agent_runtime", cfg.RuntimeURL)
	select {
	case err := <-failures:
		if !errors.Is(err, http.ErrServerClosed) {
			log.Error("Go API failed", "error", err.Error())
			os.Exit(1)
		}
	case <-signals.Done():
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := server.Shutdown(ctx); err != nil {
			_ = server.Close()
		}
	}
}
