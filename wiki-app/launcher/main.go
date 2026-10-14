package main

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

const clientVersion = 2
const appName = "SCORE-wiki"
const projectID = "SCORE-fa82e2d014"

type Manifest struct {
	FormatVersion        int    `json:"format_version"`
	ContentFormatVersion int    `json:"content_format_version"`
	MinClientVersion     int    `json:"min_client_version"`
	ProjectID            string `json:"project_id"`
	PackID               string `json:"pack_id"`
	Batch                string `json:"batch"`
	Files                struct {
		Index string `json:"index"`
	} `json:"files"`
	FileHashes map[string]string `json:"file_hashes"`
}
type AppManifest struct {
	FormatVersion        int               `json:"format_version"`
	AppVersion           string            `json:"app_version"`
	ClientVersion        int               `json:"client_version"`
	ContentFormatVersion int               `json:"content_format_version"`
	ProjectID            string            `json:"project_id"`
	FileHashes           map[string]string `json:"file_hashes"`
}
type Active struct {
	Version  int     `json:"version"`
	Current  string  `json:"current"`
	Previous *string `json:"previous"`
}
type Service struct {
	root, token, identity, origin string
	manifest                      Manifest
	app                           AppManifest
	active                        Active
	server                        *http.Server
	mu                            sync.Mutex
	operation                     sync.Mutex
	stopping                      sync.Once
	stop                          chan struct{}
	sessions                      map[chan map[string]any]bool
	first                         bool
	lastGone                      time.Time
	started                       time.Time
	idleGrace, startupTimeout     time.Duration
	busy                          bool
	closing                       bool
	trace                         bool
	config                        map[string]any
	configRevision                string
	configWarning                 string
	staged                        map[string]*Staged
	cancelOperation               func()
	windowHandle                  uintptr
	activateWindow                func()
	closeWindow                   func()
	updateWindow                  func(string)
	// fault injection confined to tests
	beforeCommit func()
	writeAtomic  func(string, []byte) error
}

func digest(b []byte) string { v := sha256.Sum256(b); return hex.EncodeToString(v[:]) }
func safeRelative(f string) bool {
	if f == "" || strings.ContainsAny(f, "\\:\x00") || strings.HasPrefix(f, "/") || path.Clean(f) != f {
		return false
	}
	for _, p := range strings.Split(f, "/") {
		if p == ".." || p == "." || p == "" || strings.HasSuffix(p, ".") || strings.HasSuffix(p, " ") {
			return false
		}
		n := strings.ToUpper(strings.Split(p, ".")[0])
		if n == "CON" || n == "PRN" || n == "AUX" || n == "NUL" || strings.HasPrefix(n, "COM") && len(n) == 4 || strings.HasPrefix(n, "LPT") && len(n) == 4 {
			return false
		}
	}
	return true
}
func validPack(p string) bool {
	if len(p) == 0 || len(p) > 100 {
		return false
	}
	for _, c := range p {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_' || c == '-') {
			return false
		}
	}
	return true
}
func validateManifest(m Manifest) error {
	if m.FormatVersion != 2 || m.ContentFormatVersion != 2 || m.MinClientVersion > clientVersion || m.ProjectID != projectID || !validPack(m.PackID) || m.Batch == "" || m.Files.Index != "data/index.json" || len(m.FileHashes) == 0 {
		return errors.New("内容清单格式、项目或客户端版本不兼容")
	}
	seen := map[string]bool{}
	for f, h := range m.FileHashes {
		if !contentPath(f) || len(h) != 64 {
			return fmt.Errorf("内容路径或哈希无效：%s", f)
		}
		if _, e := hex.DecodeString(h); e != nil {
			return e
		}
		if seen[strings.ToLower(f)] {
			return errors.New("内容路径大小写冲突")
		}
		seen[strings.ToLower(f)] = true
	}
	if _, ok := m.FileHashes[m.Files.Index]; !ok {
		return errors.New("缺少索引")
	}
	return nil
}
func contentPath(f string) bool {
	if !safeRelative(f) {
		return false
	}
	ext := strings.ToLower(path.Ext(f))
	return f == "assets/manifest.json" || strings.HasPrefix(f, "data/") && ext == ".json" || strings.HasPrefix(f, "assets/") && (ext == ".png" || ext == ".jpg" || ext == ".jpeg" || ext == ".webp" || ext == ".gif" || ext == ".woff" || ext == ".woff2" || ext == ".ttf")
}
func checked(root, f, hash string) (string, error) {
	if !safeRelative(f) {
		return "", errors.New("路径无效")
	}
	p, e := filepath.EvalSymlinks(filepath.Join(root, filepath.FromSlash(f)))
	if e != nil {
		return "", e
	}
	rel, e := filepath.Rel(root, p)
	if e != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(os.PathSeparator)) {
		return "", errors.New("路径越界")
	}
	stream, e := os.Open(p)
	if e != nil {
		return "", e
	}
	defer stream.Close()
	i, e := stream.Stat()
	if e != nil || !i.Mode().IsRegular() {
		return "", errors.New("非普通文件")
	}
	h := sha256.New()
	if _, e = io.Copy(h, stream); e != nil {
		return "", e
	}
	if hex.EncodeToString(h.Sum(nil)) != hash {
		return "", fmt.Errorf("文件哈希不匹配：%s", f)
	}
	return p, nil
}
func loadManifest(root, id string) (Manifest, error) {
	var m Manifest
	if !validPack(id) {
		return m, errors.New("内容版本标识无效")
	}
	b, e := os.ReadFile(filepath.Join(root, "packs", id, "manifest.json"))
	if e != nil {
		return m, e
	}
	e = json.Unmarshal(b, &m)
	if e != nil {
		return m, e
	}
	if e = validateManifest(m); e != nil {
		return m, e
	}
	if m.PackID != id {
		return m, errors.New("内容目录版本不匹配")
	}
	_, e = checked(filepath.Join(root, "packs", id), m.Files.Index, m.FileHashes[m.Files.Index])
	return m, e
}
func load(root string) (*Service, error) {
	abs, e := filepath.Abs(root)
	if e != nil {
		return nil, e
	}
	abs, e = filepath.EvalSymlinks(abs)
	if e != nil {
		return nil, e
	}
	b, e := os.ReadFile(filepath.Join(abs, "app-manifest.json"))
	if e != nil {
		return nil, e
	}
	var app AppManifest
	if e = json.Unmarshal(b, &app); e != nil {
		return nil, e
	}
	if app.FormatVersion != 2 || app.ClientVersion != clientVersion || app.ContentFormatVersion != 2 || app.ProjectID != projectID || app.AppVersion == "" {
		return nil, errors.New("程序版本清单不兼容")
	}
	for f, h := range app.FileHashes {
		if !safeRelative(f) || len(h) != 64 {
			return nil, errors.New("程序清单路径无效")
		}
	}
	if _, e = checked(abs, "index.html", app.FileHashes["index.html"]); e != nil {
		return nil, e
	}
	if e = recoverActive(abs); e != nil {
		return nil, e
	}
	b, e = os.ReadFile(filepath.Join(abs, "active.json"))
	if e != nil {
		return nil, e
	}
	var active Active
	if e = json.Unmarshal(b, &active); e != nil {
		return nil, e
	}
	if active.Version != 1 {
		return nil, errors.New("活动版本格式无效")
	}
	m, e := loadManifest(abs, active.Current)
	if e != nil {
		return nil, e
	}
	token := make([]byte, 32)
	if _, e = rand.Read(token); e != nil {
		return nil, e
	}
	s := &Service{root: abs, token: hex.EncodeToString(token), identity: digest([]byte(abs + app.AppVersion)), manifest: m, app: app, active: active, stop: make(chan struct{}), sessions: map[chan map[string]any]bool{}, started: time.Now(), idleGrace: 30 * time.Second, startupTimeout: 300 * time.Second, staged: map[string]*Staged{}, writeAtomic: atomicWrite}
	s.reloadConfig(true)
	return s, nil
}
func reply(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	json.NewEncoder(w).Encode(v)
}
func fail(w http.ResponseWriter, e error, status int) {
	w.WriteHeader(status)
	reply(w, map[string]string{"error": e.Error()})
}
func (s *Service) current() (Manifest, Active) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.manifest, s.active
}
func (s *Service) authorize(w http.ResponseWriter, r *http.Request, body any) bool {
	if r.Method != "POST" || r.Header.Get("Origin") != s.origin {
		fail(w, errors.New("请求来源无效"), 403)
		return false
	}
	if r.URL.Path == "/__app/update/check" {
		if r.Header.Get("X-SCORE-Token") != s.token {
			fail(w, errors.New("令牌无效"), 403)
			return false
		}
		return true
	}
	b, e := io.ReadAll(http.MaxBytesReader(w, r.Body, 8192))
	var auth struct {
		Token string `json:"token"`
	}
	if e != nil || json.Unmarshal(b, &auth) != nil || auth.Token != s.token {
		fail(w, errors.New("令牌无效"), 403)
		return false
	}
	if body != nil && json.Unmarshal(b, body) != nil {
		fail(w, errors.New("请求格式无效"), 400)
		return false
	}
	return true
}
func (s *Service) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if s.trace {
		log.Printf("request %s %s", r.Method, r.URL.Path)
	}
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Cache-Control", "no-cache")
	if r.Host != strings.TrimPrefix(s.origin, "http://") {
		fail(w, errors.New("本机访问地址无效"), 403)
		return
	}
	m, a := s.current()
	switch r.URL.Path {
	case "/__app/health":
		reply(w, map[string]any{"app": appName, "version": "4", "app_version": s.app.AppVersion, "client_version": clientVersion, "batch": m.Batch, "pack_id": m.PackID, "root": s.root, "identity": s.identity, "token": s.token, "origin": s.origin, "state": "running"})
		return
	case "/manifest.json":
		blob, err := os.ReadFile(filepath.Join(s.root, "packs", m.PackID, "manifest.json"))
		if err != nil {
			fail(w, err, 500)
			return
		}
		var full map[string]any
		if err = json.Unmarshal(blob, &full); err != nil {
			fail(w, err, 500)
			return
		}
		full["app_version"] = s.app.AppVersion
		full["client_version"] = clientVersion
		reply(w, full)
		return
	case "/__app/config":
		s.configReply(w)
		return
	case "/__app/config/reload":
		if !s.authorize(w, r, nil) {
			return
		}
		if e := s.reloadConfig(false); e != nil {
			fail(w, e, 422)
			return
		}
		s.configReply(w)
		return
	case "/__app/session":
		var b struct {
			ClientID string `json:"client_id"`
		}
		if s.authorize(w, r, &b) {
			s.session(w, r)
		}
		return
	case "/__app/quit":
		if !s.authorize(w, r, nil) {
			return
		}
		reply(w, map[string]bool{"stopped": true})
		go func() { time.Sleep(100 * time.Millisecond); s.shutdown() }()
		return
	case "/__app/open":
		if !s.authorize(w, r, nil) {
			return
		}
		if s.activateWindow != nil {
			s.activateWindow()
		}
		go openBrowser(s.origin + "/")
		reply(w, map[string]bool{"opened": true})
		return
	case "/__app/update/status":
		s.mu.Lock()
		busy := s.busy
		var stage any
		for _, v := range s.staged {
			stage = map[string]any{"staging_id": v.ID, "pack_id": v.Manifest.PackID, "batch": v.Manifest.Batch}
			break
		}
		s.mu.Unlock()
		var previous any
		if a.Previous != nil {
			if pm, e := loadManifest(s.root, *a.Previous); e == nil {
				previous = map[string]string{"pack_id": pm.PackID, "batch": pm.Batch}
			}
		}
		reply(w, map[string]any{"current": map[string]string{"pack_id": m.PackID, "batch": m.Batch}, "previous": previous, "busy": busy, "staged": stage})
		return
	case "/__app/update/check":
		if s.authorize(w, r, nil) {
			v, e := s.stageZip(w, r)
			if e != nil {
				fail(w, e, 422)
			} else {
				reply(w, v)
			}
		}
		return
	case "/__app/update/install":
		var b struct {
			ID string `json:"staging_id"`
		}
		if s.authorize(w, r, &b) {
			v, e := s.install(b.ID)
			if e != nil {
				fail(w, e, 409)
			} else {
				reply(w, v)
			}
		}
		return
	case "/__app/update/rollback":
		if s.authorize(w, r, nil) {
			v, e := s.rollback()
			if e != nil {
				fail(w, e, 409)
			} else {
				reply(w, v)
			}
		}
		return
	case "/other.html":
		http.Redirect(w, r, "/#page=other", 302)
		return
	}
	if r.Method != "GET" && r.Method != "HEAD" {
		fail(w, errors.New("请求方法无效"), 405)
		return
	}
	if strings.HasPrefix(r.URL.Path, "/units/") && strings.HasSuffix(r.URL.Path, ".html") {
		parts := strings.Split(strings.Trim(r.URL.Path, "/"), "/")
		if len(parts) == 3 {
			origin, id := parts[1], strings.TrimSuffix(parts[2], ".html")
			if _, ok := m.FileHashes["data/entries/"+origin+"/"+id+".json"]; ok {
				http.Redirect(w, r, "/#faction="+origin+"&source="+origin+"&unit=Unit%3A"+id, 302)
				return
			}
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.WriteHeader(404)
		fmt.Fprint(w, `<h1>此对象未收录为玩家词条</h1><a href="/">返回阵营总览</a>`)
		return
	}
	file := strings.TrimPrefix(r.URL.Path, "/")
	root := s.root
	hash := ""
	if strings.HasPrefix(file, "__content/") {
		parts := strings.SplitN(strings.TrimPrefix(file, "__content/"), "/", 2)
		if len(parts) != 2 {
			fail(w, errors.New("内容路径无效"), 404)
			return
		}
		pm, e := loadManifest(s.root, parts[0])
		if e != nil {
			fail(w, e, 404)
			return
		}
		file = parts[1]
		root = filepath.Join(s.root, "packs", parts[0])
		hash = pm.FileHashes[file]
	} else {
		if file == "" {
			file = "index.html"
		}
		hash = s.app.FileHashes[file]
	}
	if hash == "" {
		fail(w, errors.New("文件未列入清单"), 404)
		return
	}
	p, e := checked(root, file, hash)
	if e != nil {
		fail(w, e, 404)
		return
	}
	http.ServeFile(w, r, p)
}
func (s *Service) shutdown() {
	s.stopping.Do(func() {
		s.mu.Lock()
		s.closing = true
		cancelUpload := s.cancelOperation
		s.mu.Unlock()
		if cancelUpload != nil {
			cancelUpload()
		}
		s.operation.Lock()
		defer s.operation.Unlock()
		close(s.stop)
		if s.closeWindow != nil {
			s.closeWindow()
		}
		if s.server != nil {
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			s.server.Shutdown(ctx)
		}
	})
}
func (s *Service) lifecycle() {
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-s.stop:
			return
		case now := <-ticker.C:
			s.mu.Lock()
			expire := !s.busy && ((!s.first && now.Sub(s.started) >= s.startupTimeout) || (s.first && len(s.sessions) == 0 && !s.lastGone.IsZero() && now.Sub(s.lastGone) >= s.idleGrace))
			s.mu.Unlock()
			if expire {
				go s.shutdown()
				return
			}
		}
	}
}
func (s *Service) session(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		fail(w, errors.New("不支持连接流"), 500)
		return
	}
	w.Header().Set("Content-Type", "application/x-ndjson")
	ch := make(chan map[string]any, 4)
	s.mu.Lock()
	if s.closing {
		s.mu.Unlock()
		return
	}
	s.sessions[ch] = true
	s.first = true
	s.mu.Unlock()
	defer func() {
		s.mu.Lock()
		delete(s.sessions, ch)
		if len(s.sessions) == 0 {
			s.lastGone = time.Now()
		}
		s.mu.Unlock()
	}()
	m, _ := s.current()
	json.NewEncoder(w).Encode(map[string]any{"event": "hello", "pack_id": m.PackID, "batch": m.Batch})
	flusher.Flush()
	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-s.stop:
			return
		case <-r.Context().Done():
			return
		case event := <-ch:
			json.NewEncoder(w).Encode(event)
			flusher.Flush()
		case <-ticker.C:
			m, _ = s.current()
			json.NewEncoder(w).Encode(map[string]any{"event": "heartbeat", "pack_id": m.PackID, "batch": m.Batch})
			flusher.Flush()
		}
	}
}
func (s *Service) broadcast(m Manifest) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for ch := range s.sessions {
		select {
		case ch <- map[string]any{"event": "content-changed", "pack_id": m.PackID, "batch": m.Batch}:
		default:
		}
	}
}
func matchingServer(port int, s *Service) bool {
	c := http.Client{Timeout: 400 * time.Millisecond}
	resp, e := c.Get("http://127.0.0.1:" + strconv.Itoa(port) + "/__app/health")
	if e != nil {
		return false
	}
	defer resp.Body.Close()
	var h struct{ App, Identity, Root, Token string }
	if resp.StatusCode != 200 || json.NewDecoder(io.LimitReader(resp.Body, 8192)).Decode(&h) != nil || h.App != appName || h.Identity != s.identity || !strings.EqualFold(h.Root, s.root) {
		return false
	}
	s.token = h.Token
	return true
}
func choosePort(start int, s *Service) (net.Listener, int, bool, error) {
	for p := start; p <= start+10 && p <= 65535; p++ {
		l, e := net.Listen("tcp4", "127.0.0.1:"+strconv.Itoa(p))
		if e == nil {
			return l, p, false, nil
		}
		if matchingServer(p, s) {
			return nil, p, true, nil
		}
	}
	return nil, 0, false, errors.New("本机端口均被占用")
}
func run() error {
	exe, e := os.Executable()
	if e != nil {
		return e
	}
	root := flag.String("root", filepath.Dir(exe), "资源目录")
	port := flag.Int("port", 18765, "本机端口")
	noBrowser := flag.Bool("no-browser", false, "不打开浏览器")
	headless := flag.Bool("headless", false, "不显示控制窗口")
	trace := flag.Bool("trace", false, "记录请求")
	idle := flag.Float64("idle-grace-seconds", 30, "最后页面退出宽限")
	startup := flag.Float64("startup-timeout-seconds", 300, "首次页面连接期限")
	flag.Parse()
	if *port < 1 || *port > 65525 || *idle <= 0 || *startup <= 0 {
		return errors.New("启动参数无效")
	}
	s, e := load(*root)
	if e != nil {
		return e
	}
	s.trace = *trace
	s.idleGrace = time.Duration(*idle * float64(time.Second))
	s.startupTimeout = time.Duration(*startup * float64(time.Second))
	l, p, reused, e := choosePort(*port, s)
	if e != nil {
		return e
	}
	s.origin = "http://127.0.0.1:" + strconv.Itoa(p)
	if reused {
		if !*noBrowser {
			body := strings.NewReader(`{"token":"` + s.token + `"}`)
			r, _ := http.NewRequest("POST", s.origin+"/__app/open", body)
			r.Header.Set("Origin", s.origin)
			resp, e := http.DefaultClient.Do(r)
			if e != nil {
				return e
			}
			resp.Body.Close()
		}
		return nil
	}
	s.server = &http.Server{Handler: s, ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 30 * time.Second}
	if !*headless {
		if e = startControlWindow(s); e != nil {
			l.Close()
			return e
		}
	}
	go s.lifecycle()
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt)
	defer signal.Stop(signals)
	go func() {
		select {
		case <-signals:
			s.shutdown()
		case <-s.stop:
		}
	}()
	if !*noBrowser {
		go func() {
			if e := openBrowser(s.origin + "/"); e != nil {
				log.Print(e)
			}
		}()
	}
	fmt.Println("SCORE百科", s.origin, "批次", s.manifest.Batch)
	e = s.server.Serve(l)
	if errors.Is(e, http.ErrServerClosed) {
		return nil
	}
	return e
}
func main() {
	dir, e := os.UserCacheDir()
	if e == nil {
		dir = filepath.Join(dir, "SCOREWiki")
		if os.MkdirAll(dir, 0755) == nil {
			if f, e := os.OpenFile(filepath.Join(dir, "launcher.log"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644); e == nil {
				defer f.Close()
				log.SetOutput(f)
			}
		}
	}
	if e = run(); e != nil {
		log.Print(e)
		fmt.Fprintln(os.Stderr, e)
		if flag.Lookup("headless") == nil || flag.Lookup("headless").Value.String() != "true" {
			showError(e.Error())
		}
		os.Exit(1)
	}
}
