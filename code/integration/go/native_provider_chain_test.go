//go:build unit

// This original test is overlaid into the pinned upstream service package.
// It exercises the real forwarder, not Sub2API auth, scheduling or billing.
package service

import (
    "bytes"
    "context"
    "encoding/json"
    "io"
    "net/http"
    "net/http/httptest"
    "os"
    "strings"
    "testing"
    "time"

    "github.com/Wei-Shaw/sub2api/internal/pkg/tlsfingerprint"
    "github.com/gin-gonic/gin"
    "github.com/stretchr/testify/require"
    "github.com/tidwall/gjson"
)

type nativeProviderHTTP struct {
    t *testing.T
    origin string
    calls int
}

func (u *nativeProviderHTTP) Do(req *http.Request, proxy string, id int64, concurrency int) (*http.Response, error) {
    u.t.Helper()
    require.Equal(u.t, "", proxy)
    require.Equal(u.t, u.origin+"/v1/chat/completions", req.URL.String())
    require.Empty(u.t, req.Header.Get("Idempotency-Key"))
    for name := range req.Header {
        require.False(u.t, strings.HasPrefix(strings.ToLower(name), "x-native-"), name)
    }
    require.Equal(u.t, "Bearer fixture-native-provider-token", req.Header.Get("Authorization"))
    body, err := io.ReadAll(req.Body)
    require.NoError(u.t, err)
    require.NoError(u.t, req.Body.Close())
    require.Equal(u.t, "synthetic-basic", gjson.GetBytes(body, "model").String())
    if gjson.GetBytes(body, "stream").Bool() {
        require.True(u.t, gjson.GetBytes(body, "stream_options.include_usage").Bool())
    }
    req.Body = io.NopCloser(bytes.NewReader(body))
    u.calls++
    client := &http.Client{Timeout: 30*time.Second, Transport: &http.Transport{Proxy:nil},
        CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
    defer client.CloseIdleConnections()
    return client.Do(req)
}

func (u *nativeProviderHTTP) DoWithTLS(req *http.Request, proxy string, id int64, concurrency int, _ *tlsfingerprint.Profile) (*http.Response,error) {
    return u.Do(req, proxy, id, concurrency)
}

func TestNativeProviderChain(t *testing.T) {
    origin := os.Getenv("NATIVE_PROVIDER_FIXTURE_ORIGIN")
    require.True(t, strings.HasPrefix(origin, "http://127.0.0.1:"), "run through run_native_provider_chain.py")
    gin.SetMode(gin.TestMode)
    upstream := &nativeProviderHTTP{t:t, origin:origin}
    svc := &OpenAIGatewayService{cfg:rawChatCompletionsTestConfig(), httpUpstream:upstream}
    account := rawChatCompletionsTestAccount()
    account.Credentials["base_url"] = origin+"/v1"
    account.Credentials["api_key"] = "fixture-native-provider-token"
    account.Credentials["model_mapping"] = map[string]any{"fixture-public":"synthetic-basic"}
    account.Extra = map[string]any{"openai_responses_mode":"force_chat_completions"}
    for _, stream := range []bool{false,true} {
        body, err := json.Marshal(map[string]any{"model":"fixture-public", "messages":[]map[string]string{{"role":"user","content":"same synthetic request"}}, "stream":stream})
        require.NoError(t,err)
        rec := httptest.NewRecorder()
        c,_ := gin.CreateTestContext(rec)
        c.Request = httptest.NewRequest(http.MethodPost,"/v1/chat/completions",bytes.NewReader(body))
        c.Request.Header.Set("Content-Type","application/json")
        // Same client key in both calls is deliberately stripped by real Sub2API.
        c.Request.Header.Set("Idempotency-Key","same-client-key-not-forwarded")
        c.Request.Header.Set("X-Native-Tenant","untrusted")
        c.Request.Header.Set("X-Native-Operation","must-not-authorize")
        result,err := svc.ForwardAsChatCompletions(context.Background(),c,account,body,"","")
        require.NoError(t,err)
        require.NotNil(t,result)
        require.Equal(t,http.StatusOK,rec.Code)
        require.NotContains(t,rec.Body.String(),`"usage"`)
        // This is the gateway's zero-value representation, not measured zero usage.
        require.Zero(t,result.Usage.InputTokens)
        require.Zero(t,result.Usage.OutputTokens)
        if stream {
            require.Contains(t,rec.Header().Get("Content-Type"),"text/event-stream")
            require.Contains(t,rec.Body.String(),"data: [DONE]")
            var answer string
            for _,line := range strings.Split(rec.Body.String(),"\n") {
                if !strings.HasPrefix(line,"data: ") || line=="data: [DONE]" { continue }
                data:=strings.TrimPrefix(line,"data: ")
                require.False(t,gjson.Get(data,"error").Exists(),data)
                require.Equal(t,"fixture-public",gjson.Get(data,"model").String())
                answer+=gjson.Get(data,"choices.0.delta.content").String()
            }
            require.Equal(t,"Synthetic FakeHarness result",answer)
        } else {
            require.Equal(t,"fixture-public",gjson.Get(rec.Body.String(),"model").String())
            require.Equal(t,"Synthetic FakeHarness result",gjson.Get(rec.Body.String(),"choices.0.message.content").String())
        }
    }
    require.Equal(t,2,upstream.calls)
}
