package main

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func put(t *testing.T, p string, b []byte) {
	t.Helper()
	if e := os.MkdirAll(filepath.Dir(p), 0755); e != nil {
		t.Fatal(e)
	}
	if e := os.WriteFile(p, b, 0644); e != nil {
		t.Fatal(e)
	}
}
func pack(t *testing.T, root, id string) Manifest {
	m := Manifest{FormatVersion: 2, ContentFormatVersion: 2, MinClientVersion: 2, ProjectID: projectID, PackID: id, Batch: id, FileHashes: map[string]string{}}
	m.Files.Index = "data/index.json"
	for f, b := range map[string]string{"data/index.json": `{"entries":[]}`, "data/entries/Cerberus/BroodLordR.json": `{"id":"Unit:BroodLordR"}`} {
		m.FileHashes[f] = digest([]byte(b))
		put(t, filepath.Join(root, "packs", id, filepath.FromSlash(f)), []byte(b))
	}
	b, _ := json.Marshal(m)
	put(t, filepath.Join(root, "packs", id, "manifest.json"), b)
	return m
}
func fixture(t *testing.T) string {
	root := t.TempDir()
	pack(t, root, "test-1")
	a := AppManifest{FormatVersion: 2, ContentFormatVersion: 2, ClientVersion: 2, ProjectID: projectID, AppVersion: "4.0.0", FileHashes: map[string]string{"index.html": digest([]byte("百科"))}}
	b, _ := json.Marshal(a)
	put(t, filepath.Join(root, "app-manifest.json"), b)
	put(t, filepath.Join(root, "index.html"), []byte("百科"))
	put(t, filepath.Join(root, "active.json"), []byte(`{"version":1,"current":"test-1","previous":null}`))
	put(t, filepath.Join(root, "config", "factions.json"), []byte(`{"version":1,"factions":[{"id":"Golden","prefixes":["Golden","Golden F"],"name":"远征军","defaultVisible":true,"order":30,"branches":{"Golden F":"F"}}]}`))
	return root
}
func handler(t *testing.T) (*Service, *httptest.Server) {
	s, e := load(fixture(t))
	if e != nil {
		t.Fatal(e)
	}
	srv := httptest.NewServer(s)
	s.origin = srv.URL
	t.Cleanup(srv.Close)
	return s, srv
}
func post(t *testing.T, s *Service, srv *httptest.Server, url string, body string) *http.Response {
	r, _ := http.NewRequest("POST", srv.URL+url, strings.NewReader(body))
	r.Header.Set("Origin", s.origin)
	resp, e := http.DefaultClient.Do(r)
	if e != nil {
		t.Fatal(e)
	}
	return resp
}
func zipPack(t *testing.T, root, id string, extra map[string][]byte) []byte {
	m := pack(t, root, id)
	var b bytes.Buffer
	w := zip.NewWriter(&b)
	files := []string{"manifest.json"}
	for f := range m.FileHashes {
		files = append(files, f)
	}
	for _, f := range files {
		blob, _ := os.ReadFile(filepath.Join(root, "packs", id, filepath.FromSlash(f)))
		x, _ := w.Create(f)
		x.Write(blob)
	}
	for f, b := range extra {
		x, _ := w.Create(f)
		x.Write(b)
	}
	w.Close()
	return b.Bytes()
}
func stage(t *testing.T, s *Service, blob []byte) (map[string]any, error) {
	r := httptest.NewRequest("POST", "/__app/update/check", bytes.NewReader(blob))
	return s.stageZip(httptest.NewRecorder(), r)
}
func TestLoadAndFileProtection(t *testing.T) {
	s, srv := handler(t)
	for _, p := range []string{"/", "/manifest.json", "/__content/test-1/data/index.json"} {
		r, e := http.Get(srv.URL + p)
		if e != nil {
			t.Fatal(e)
		}
		r.Body.Close()
		if r.StatusCode != 200 {
			t.Fatal(p, r.StatusCode)
		}
	}
	put(t, filepath.Join(s.root, "packs", "test-1", "data", "index.json"), []byte("bad"))
	if _, e := load(s.root); e == nil {
		t.Fatal("tampering accepted")
	}
	if _, e := load(t.TempDir()); e == nil {
		t.Fatal("missing accepted")
	}
}
func TestConfigFallbackAndValidation(t *testing.T) {
	s, _ := handler(t)
	old := s.configRevision
	put(t, filepath.Join(s.root, "config", "factions.json"), []byte(`{"version":1,"factions":[{"id":"Golden","prefixes":["Golden","- golden"],"name":"远征军","defaultVisible":true,"order":30}]}`))
	if s.reloadConfig(false) == nil || s.configRevision != old {
		t.Fatal("bad config discarded last valid")
	}
	s2, e := load(s.root)
	if e != nil || s2.configRevision != old || s2.configWarning == "" {
		t.Fatal("fallback failed")
	}
}
func TestUpdateInstallRollbackPinnedAndRecovery(t *testing.T) {
	s, srv := handler(t)
	blob := zipPack(t, t.TempDir(), "test-2", nil)
	v, e := stage(t, s, blob)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = s.install(v["staging_id"].(string)); e != nil {
		t.Fatal(e)
	}
	m, _ := s.current()
	if m.PackID != "test-2" {
		t.Fatal("not installed")
	}
	for _, id := range []string{"test-1", "test-2"} {
		r, _ := http.Get(srv.URL + "/__content/" + id + "/data/index.json")
		r.Body.Close()
		if r.StatusCode != 200 {
			t.Fatal("pinned inaccessible")
		}
	}
	if _, e = s.rollback(); e != nil {
		t.Fatal(e)
	}
	b, _ := json.Marshal(Active{Version: 1, Current: "test-2"})
	put(t, filepath.Join(s.root, "update-journal.json"), b)
	s2, e := load(s.root)
	if e != nil || s2.active.Current != "test-2" {
		t.Fatal("recovery failed", e)
	}
}
func TestUnsafeUpdates(t *testing.T) {
	s, _ := handler(t)
	for _, extra := range []map[string][]byte{{"../evil": {}}, {"data/INDEX.json": {}}, {"config/factions.json": {}}, {"assets/client.js": {}}, {"data/extra.json": {}}} {
		if _, e := stage(t, s, zipPack(t, t.TempDir(), "bad", extra)); e == nil {
			t.Fatal("unsafe accepted", extra)
		}
	}
	var b bytes.Buffer
	w := zip.NewWriter(&b)
	h := &zip.FileHeader{Name: "data/link.json"}
	h.SetMode(os.ModeSymlink | 0777)
	f, _ := w.CreateHeader(h)
	f.Write([]byte("outside"))
	w.Close()
	if _, e := stage(t, s, b.Bytes()); e == nil {
		t.Fatal("symlink accepted")
	}
}
func TestHashAndFutureClientRejected(t *testing.T) {
	for _, future := range []bool{false, true} {
		s, _ := handler(t)
		root := t.TempDir()
		m := pack(t, root, "new")
		if future {
			m.MinClientVersion = 99
		} else {
			m.FileHashes["data/index.json"] = strings.Repeat("0", 64)
		}
		b, _ := json.Marshal(m)
		put(t, filepath.Join(root, "packs", "new", "manifest.json"), b)
		var buf bytes.Buffer
		w := zip.NewWriter(&buf)
		for _, name := range []string{"manifest.json", "data/index.json", "data/entries/Cerberus/BroodLordR.json"} {
			b, _ := os.ReadFile(filepath.Join(root, "packs", "new", filepath.FromSlash(name)))
			f, _ := w.Create(name)
			f.Write(b)
		}
		w.Close()
		if _, e := stage(t, s, buf.Bytes()); e == nil {
			t.Fatal("invalid accepted")
		}
	}
}
func TestDiskFailureKeepsActive(t *testing.T) {
	s, _ := handler(t)
	v, e := stage(t, s, zipPack(t, t.TempDir(), "new", nil))
	if e != nil {
		t.Fatal(e)
	}
	s.writeAtomic = func(string, []byte) error { return errors.New("disk full") }
	if _, e = s.install(v["staging_id"].(string)); e == nil {
		t.Fatal("disk failure ignored")
	}
	s2, e := load(s.root)
	if e != nil || s2.active.Current != "test-1" {
		t.Fatal("active changed on failure")
	}
}
func TestShutdownCancelsBeforeCommit(t *testing.T) {
	s, _ := handler(t)
	v, _ := stage(t, s, zipPack(t, t.TempDir(), "new", nil))
	s.beforeCommit = func() { s.mu.Lock(); s.closing = true; s.mu.Unlock() }
	if _, e := s.install(v["staging_id"].(string)); e == nil {
		t.Fatal("cancel ignored")
	}
	if s.active.Current != "test-1" {
		t.Fatal("cancel changed data")
	}
}
func TestSessionsAndIdleLifecycle(t *testing.T) {
	s, srv := handler(t)
	s.idleGrace = 150 * time.Millisecond
	s.startupTimeout = 2 * time.Second
	go s.lifecycle()
	ctx, cancel := context.WithCancel(context.Background())
	r, _ := http.NewRequestWithContext(ctx, "POST", srv.URL+"/__app/session", strings.NewReader(`{"token":"`+s.token+`","client_id":"one"}`))
	r.Header.Set("Origin", s.origin)
	resp, e := http.DefaultClient.Do(r)
	if e != nil {
		t.Fatal(e)
	}
	b := make([]byte, 10)
	resp.Body.Read(b)
	time.Sleep(200 * time.Millisecond)
	select {
	case <-s.stop:
		t.Fatal("active session exited")
	default:
	}
	cancel()
	resp.Body.Close()
	select {
	case <-s.stop:
	case <-time.After(time.Second):
		t.Fatal("idle did not exit")
	}
}
func TestStartupTimeoutAndSecurity(t *testing.T) {
	s, srv := handler(t)
	s.startupTimeout = 100 * time.Millisecond
	go s.lifecycle()
	r := post(t, s, srv, "/__app/quit", `{"token":"bad"}`)
	r.Body.Close()
	if r.StatusCode != 403 {
		t.Fatal("bad token")
	}
	req, _ := http.NewRequest("GET", srv.URL+"/__app/health", nil)
	req.Host = "foreign"
	resp, _ := http.DefaultClient.Do(req)
	resp.Body.Close()
	if resp.StatusCode != 403 {
		t.Fatal("host accepted")
	}
	select {
	case <-s.stop:
	case <-time.After(time.Second):
		t.Fatal("startup timeout failed")
	}
	for _, p := range []string{"../x", "C:/x", "a\\b", "a/CON.json", "a/x.", "data/../x"} {
		if safeRelative(p) {
			t.Fatal("unsafe path", p)
		}
	}
}
func TestLegacy(t *testing.T) {
	_, srv := handler(t)
	c := http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	r, _ := c.Get(srv.URL + "/units/Cerberus/BroodLordR.html")
	r.Body.Close()
	if r.StatusCode != 302 || !strings.Contains(r.Header.Get("Location"), "source=Cerberus") {
		t.Fatal("legacy identity")
	}
	r, _ = c.Get(srv.URL + "/units/Cerberus/Missile.html")
	b, _ := io.ReadAll(r.Body)
	r.Body.Close()
	if r.StatusCode != 404 || !strings.Contains(string(b), `href="/"`) {
		t.Fatal("missing fallback")
	}
}
func TestCloseDuringCommitCompletesSafely(t *testing.T) {
	s, _ := handler(t)
	v, e := stage(t, s, zipPack(t, t.TempDir(), "new", nil))
	if e != nil {
		t.Fatal(e)
	}
	entered := make(chan struct{})
	resume := make(chan struct{})
	s.writeAtomic = func(p string, b []byte) error {
		if strings.HasSuffix(p, "update-journal.json") {
			close(entered)
			<-resume
		}
		return atomicWrite(p, b)
	}
	done := make(chan error, 1)
	go func() { _, e := s.install(v["staging_id"].(string)); done <- e }()
	<-entered
	go s.shutdown()
	time.Sleep(50 * time.Millisecond)
	select {
	case <-s.stop:
		t.Fatal("shutdown interrupted commit")
	default:
	}
	close(resume)
	if e = <-done; e != nil {
		t.Fatal(e)
	}
	select {
	case <-s.stop:
	case <-time.After(time.Second):
		t.Fatal("shutdown stuck")
	}
	s2, e := load(s.root)
	if e != nil || s2.active.Current != "new" {
		t.Fatal("committed data lost", e)
	}
}
func TestConcurrentUpdateRejected(t *testing.T) {
	s, _ := handler(t)
	if e := s.beginOperation(); e != nil {
		t.Fatal(e)
	}
	if _, e := s.rollback(); e == nil {
		t.Fatal("concurrent update accepted")
	}
	s.endOperation()
}
func TestMultiSessionAndBroadcast(t *testing.T) {
	s, srv := handler(t)
	s.idleGrace = 100 * time.Millisecond
	s.startupTimeout = time.Second
	go s.lifecycle()
	open := func() (context.CancelFunc, *http.Response) {
		ctx, cancel := context.WithCancel(context.Background())
		r, _ := http.NewRequestWithContext(ctx, "POST", srv.URL+"/__app/session", strings.NewReader(`{"token":"`+s.token+`","client_id":"page"}`))
		r.Header.Set("Origin", s.origin)
		resp, e := http.DefaultClient.Do(r)
		if e != nil {
			t.Fatal(e)
		}
		var event map[string]any
		if e = json.NewDecoder(resp.Body).Decode(&event); e != nil {
			t.Fatal(e)
		}
		return cancel, resp
	}
	c1, r1 := open()
	c2, r2 := open()
	c1()
	r1.Body.Close()
	time.Sleep(200 * time.Millisecond)
	select {
	case <-s.stop:
		t.Fatal("remaining page ignored")
	default:
	}
	s.broadcast(Manifest{PackID: "next", Batch: "next"})
	var event map[string]any
	if e := json.NewDecoder(r2.Body).Decode(&event); e != nil || event["event"] != "content-changed" {
		t.Fatal("missing activation event", e)
	}
	c2()
	r2.Body.Close()
	select {
	case <-s.stop:
	case <-time.After(time.Second):
		t.Fatal("last page not stopped")
	}
}
func TestAssetAuditManifestContract(t *testing.T) {
	s, _ := handler(t)
	root := t.TempDir()
	m := pack(t, root, "asset-test")
	blob := []byte(`{"assets":[],"source_hashes":{}}`)
	m.FileHashes["assets/manifest.json"] = digest(blob)
	put(t, filepath.Join(root, "packs", "asset-test", "assets", "manifest.json"), blob)
	mb, _ := json.Marshal(m)
	put(t, filepath.Join(root, "packs", "asset-test", "manifest.json"), mb)
	if _, e := loadManifest(root, "asset-test"); e != nil {
		t.Fatal("real asset audit manifest rejected", e)
	}
	var out bytes.Buffer
	w := zip.NewWriter(&out)
	names := []string{"manifest.json"}
	for f := range m.FileHashes {
		names = append(names, f)
	}
	for _, name := range names {
		b, _ := os.ReadFile(filepath.Join(root, "packs", "asset-test", filepath.FromSlash(name)))
		f, _ := w.Create(name)
		f.Write(b)
	}
	w.Close()
	if _, e := stage(t, s, out.Bytes()); e != nil {
		t.Fatal("asset audit update rejected", e)
	}
	for _, name := range []string{"assets/other.json", "assets/images/active.svg", "assets/MANIFEST.json"} {
		if contentPath(name) {
			t.Fatal("unsafe asset permitted", name)
		}
	}
}
