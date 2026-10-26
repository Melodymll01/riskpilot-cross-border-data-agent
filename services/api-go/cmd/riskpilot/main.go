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
	"riskpilot/api-go/internal/api"
	"riskpilot/api-go/internal/config"
	"riskpilot/api-go/internal/infra"
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
	cfg, err := config.Load()
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
	handler, err := api.New(cfg, store, log)
	if err != nil {
		log.Error("cannot create gateway")
		os.Exit(1)
	}
	server := &http.Server{Addr: cfg.Listen, Handler: handler.Router(), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 60 * time.Second, WriteTimeout: cfg.UpstreamTimeout + 5*time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 1 << 20}
	signals, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	failures := make(chan error, 1)
	go func() { failures <- server.ListenAndServe() }()
	log.Info("Go API listening", "address", cfg.Listen, "python_service", cfg.PythonURL)
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
