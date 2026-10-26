package app

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"riskpilot/api-go/internal/config"
	"riskpilot/api-go/internal/domain"
	"riskpilot/api-go/internal/infra"
)

type Auth struct {
	Config config.Config
	Store  *infra.Store
	Client *http.Client
}

func RandomID() (string, error) {
	value := make([]byte, 24)
	if _, err := rand.Read(value); err != nil {
		return "", err
	}
	return hex.EncodeToString(value), nil
}
func (a *Auth) Token(id string) (string, error) {
	now := time.Now()
	return jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.RegisteredClaims{Subject: id, IssuedAt: jwt.NewNumericDate(now), ExpiresAt: jwt.NewNumericDate(now.Add(a.Config.SessionTTL))}).SignedString([]byte(a.Config.JWTSecret))
}
func (a *Auth) Identify(ctx context.Context, token string) (*domain.User, error) {
	if token == "" {
		return nil, nil
	}
	claims := jwt.RegisteredClaims{}
	parsed, err := jwt.ParseWithClaims(token, &claims, func(token *jwt.Token) (any, error) { return []byte(a.Config.JWTSecret), nil }, jwt.WithValidMethods([]string{"HS256"}), jwt.WithExpirationRequired(), jwt.WithIssuedAt())
	if err != nil || !parsed.Valid || claims.Subject == "" {
		return nil, nil
	}
	user, err := a.Store.User(ctx, claims.Subject)
	if user != nil {
		for _, id := range a.Config.AdminIDs {
			if user.ID == id {
				user.Admin = true
				break
			}
		}
	}
	return user, err
}
func (a *Auth) Anonymous(ctx context.Context) (*domain.User, error) {
	id, err := RandomID()
	if err != nil {
		return nil, err
	}
	now := float64(time.Now().UnixMilli()) / 1000
	return a.Store.SaveUser(ctx, domain.User{ID: "anon:" + id[:16], Provider: "anonymous", ProviderID: id[:16], Name: "匿名用户-" + id[:6], Created: now, Active: now})
}
func (a *Auth) Begin(ctx context.Context) (string, string, error) {
	for _, value := range []string{a.Config.ClientID, a.Config.ClientSecret} {
		if value == "" || strings.HasPrefix(strings.ToLower(value), "dev-placeholder-") {
			return "", "", errors.New("GitHub 登录尚未配置，暂时可以使用访客身份")
		}
	}
	state, err := RandomID()
	if err != nil {
		return "", "", err
	}
	if err = a.Store.SaveState(ctx, state); err != nil {
		return "", "", err
	}
	target, err := url.Parse(a.Config.AuthorizeURL)
	if err != nil {
		return "", "", err
	}
	query := target.Query()
	query.Set("client_id", a.Config.ClientID)
	query.Set("redirect_uri", a.Config.RedirectURI)
	query.Set("scope", "read:user user:email")
	query.Set("state", state)
	target.RawQuery = query.Encode()
	return target.String(), state, nil
}
func (a *Auth) Exchange(ctx context.Context, code, state string) (*domain.User, error) {
	valid, err := a.Store.ConsumeState(ctx, state)
	if err != nil {
		return nil, err
	}
	if !valid || code == "" {
		return nil, errors.New("invalid or expired OAuth state")
	}
	form := url.Values{"client_id": {a.Config.ClientID}, "client_secret": {a.Config.ClientSecret}, "code": {code}, "redirect_uri": {a.Config.RedirectURI}}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, a.Config.TokenURL, strings.NewReader(form.Encode()))
	if err != nil {
		return nil, err
	}
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	request.Header.Set("Accept", "application/json")
	response, err := a.Client.Do(request)
	if err != nil {
		return nil, errors.New("GitHub token service unavailable")
	}
	defer response.Body.Close()
	tokenData := struct {
		Token string `json:"access_token"`
		Error string `json:"error"`
	}{}
	if response.StatusCode != 200 || json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&tokenData) != nil || tokenData.Token == "" || tokenData.Error != "" {
		return nil, errors.New("GitHub token exchange failed")
	}
	request, err = http.NewRequestWithContext(ctx, http.MethodGet, a.Config.UserURL, nil)
	if err != nil {
		return nil, err
	}
	request.Header.Set("Authorization", "Bearer "+tokenData.Token)
	request.Header.Set("Accept", "application/vnd.github+json")
	request.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	response, err = a.Client.Do(request)
	if err != nil {
		return nil, errors.New("GitHub user service unavailable")
	}
	defer response.Body.Close()
	profile := struct {
		ID     int64   `json:"id"`
		Login  string  `json:"login"`
		Name   *string `json:"name"`
		Email  *string `json:"email"`
		Avatar *string `json:"avatar_url"`
	}{}
	if response.StatusCode != 200 || json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&profile) != nil || profile.ID <= 0 || profile.Login == "" {
		return nil, errors.New("GitHub user response invalid")
	}
	name := profile.Login
	if profile.Name != nil && *profile.Name != "" {
		name = *profile.Name
	}
	now := float64(time.Now().UnixMilli()) / 1000
	return a.Store.SaveUser(ctx, domain.User{ID: "github:id:" + strconv.FormatInt(profile.ID, 10), Provider: "github", ProviderID: strconv.FormatInt(profile.ID, 10), Name: name, Email: profile.Email, Avatar: profile.Avatar, Created: now, Active: now})
}
