package domain

type User struct {
	ID         string  `json:"user_id"`
	Provider   string  `json:"provider"`
	ProviderID string  `json:"-"`
	Email      *string `json:"email"`
	Name       string  `json:"display_name"`
	Avatar     *string `json:"avatar_url"`
	Admin      bool    `json:"is_admin"`
	Created    float64 `json:"-"`
	Active     float64 `json:"-"`
}
