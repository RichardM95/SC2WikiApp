package main

import (
	"archive/zip"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const maxZip = 512 << 20
const maxExpanded = 2 << 30
const maxFiles = 50000

type Staged struct {
	ID, Dir  string
	Manifest Manifest
	Entries  int
}

func atomicWrite(p string, b []byte) error {
	f, e := os.CreateTemp(filepath.Dir(p), ".atomic-*")
	if e != nil {
		return e
	}
	name := f.Name()
	defer os.Remove(name)
	if _, e = f.Write(b); e != nil {
		f.Close()
		return e
	}
	if e = f.Sync(); e != nil {
		f.Close()
		return e
	}
	if e = f.Close(); e != nil {
		return e
	}
	return replaceFile(name, p)
}
func recoverActive(root string) error {
	journal := filepath.Join(root, "update-journal.json")
	b, e := os.ReadFile(journal)
	if os.IsNotExist(e) {
		return nil
	}
	if e != nil {
		return e
	}
	var a Active
	if e = json.Unmarshal(b, &a); e != nil {
		return e
	}
	if _, e = loadManifest(root, a.Current); e != nil {
		return fmt.Errorf("恢复更新失败：%w", e)
	}
	if e = atomicWrite(filepath.Join(root, "active.json"), b); e != nil {
		return e
	}
	return os.Remove(journal)
}
func (s *Service) beginOperation() error {
	if !s.operation.TryLock() {
		return errors.New("另一个更新操作正在执行")
	}
	s.mu.Lock()
	if s.closing {
		s.mu.Unlock()
		s.operation.Unlock()
		return errors.New("程序正在退出")
	}
	s.busy = true
	s.mu.Unlock()
	return nil
}
func (s *Service) endOperation() {
	s.mu.Lock()
	s.busy = false
	if len(s.sessions) == 0 {
		s.lastGone = time.Now()
	}
	s.mu.Unlock()
	s.operation.Unlock()
}
func (s *Service) cancelled() bool { s.mu.Lock(); defer s.mu.Unlock(); return s.closing }
func (s *Service) stageZip(w http.ResponseWriter, r *http.Request) (map[string]any, error) {
	if e := s.beginOperation(); e != nil {
		return nil, e
	}
	defer s.endOperation()
	s.mu.Lock()
	s.cancelOperation = func() { http.NewResponseController(w).SetReadDeadline(time.Now()); r.Body.Close() }
	s.mu.Unlock()
	defer func() { s.mu.Lock(); s.cancelOperation = nil; s.mu.Unlock() }()
	base := filepath.Join(s.root, "staging")
	if e := os.MkdirAll(base, 0755); e != nil {
		return nil, e
	}
	dir, e := os.MkdirTemp(base, "update-")
	if e != nil {
		return nil, e
	}
	ok := false
	defer func() {
		if !ok {
			os.RemoveAll(dir)
		}
	}()
	archive := filepath.Join(dir, "upload.zip")
	f, e := os.Create(archive)
	if e != nil {
		return nil, e
	}
	_, e = io.Copy(f, http.MaxBytesReader(w, r.Body, maxZip))
	closeErr := f.Close()
	if e != nil {
		return nil, e
	}
	if closeErr != nil {
		return nil, closeErr
	}
	zr, e := zip.OpenReader(archive)
	if e != nil {
		return nil, e
	}
	defer zr.Close()
	if len(zr.File) > maxFiles {
		return nil, errors.New("更新文件数量超过限制")
	}
	seen := map[string]bool{}
	files := map[string]*zip.File{}
	var total uint64
	for _, z := range zr.File {
		if !safeRelative(z.Name) || z.Mode()&os.ModeSymlink != 0 || !z.Mode().IsRegular() {
			return nil, errors.New("更新包包含不安全路径或非普通文件")
		}
		lower := strings.ToLower(z.Name)
		if seen[lower] {
			return nil, errors.New("更新包路径重复或大小写冲突")
		}
		seen[lower] = true
		if z.Name != "manifest.json" && !contentPath(z.Name) {
			return nil, errors.New("更新包不允许程序或配置文件")
		}
		total += z.UncompressedSize64
		if total > maxExpanded {
			return nil, errors.New("更新展开大小超过限制")
		}
		files[z.Name] = z
	}
	mz, exists := files["manifest.json"]
	if !exists || mz.UncompressedSize64 > 32<<20 {
		return nil, errors.New("更新包缺少有效清单")
	}
	reader, e := mz.Open()
	if e != nil {
		return nil, e
	}
	mb, e := io.ReadAll(io.LimitReader(reader, 32<<20))
	reader.Close()
	if e != nil {
		return nil, e
	}
	var m Manifest
	if e = json.Unmarshal(mb, &m); e != nil {
		return nil, e
	}
	if e = validateManifest(m); e != nil {
		return nil, e
	}
	if len(files) != len(m.FileHashes)+1 {
		return nil, errors.New("更新清单与文件数量不一致")
	}
	content := filepath.Join(dir, "content")
	if e = os.Mkdir(content, 0755); e != nil {
		return nil, e
	}
	for name, z := range files {
		if s.cancelled() {
			return nil, errors.New("更新已取消")
		}
		if name != "manifest.json" {
			if _, exists = m.FileHashes[name]; !exists {
				return nil, errors.New("存在清单以外文件")
			}
		}
		target := filepath.Join(content, filepath.FromSlash(name))
		if e = os.MkdirAll(filepath.Dir(target), 0755); e != nil {
			return nil, e
		}
		src, e := z.Open()
		if e != nil {
			return nil, e
		}
		dst, e := os.OpenFile(target, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
		if e != nil {
			src.Close()
			return nil, e
		}
		n, e := io.Copy(dst, io.LimitReader(src, int64(z.UncompressedSize64)+1))
		src.Close()
		cerr := dst.Close()
		if e != nil || cerr != nil || uint64(n) != z.UncompressedSize64 {
			return nil, errors.New("更新文件写入失败")
		}
		if name != "manifest.json" {
			if _, e = checked(content, name, m.FileHashes[name]); e != nil {
				return nil, e
			}
		}
	}
	var idx map[string]any
	if b, e := os.ReadFile(filepath.Join(content, "data", "index.json")); e == nil {
		json.Unmarshal(b, &idx)
	}
	count := 0
	if rows, ok := idx["entries"].([]any); ok {
		count = len(rows)
	}
	id := filepath.Base(dir)
	st := &Staged{ID: id, Dir: content, Manifest: m, Entries: count}
	s.mu.Lock()
	s.staged[id] = st
	s.mu.Unlock()
	ok = true
	return map[string]any{"staging_id": id, "pack_id": m.PackID, "batch": m.Batch, "summary": map[string]int{"entries": count, "files": len(files)}}, nil
}
func (s *Service) activate(m Manifest, a Active, state string) (map[string]any, error) {
	if s.beforeCommit != nil {
		s.beforeCommit()
	}
	if s.cancelled() {
		return nil, errors.New("更新已取消")
	}
	b, _ := json.Marshal(a)
	journal := filepath.Join(s.root, "update-journal.json")
	if e := s.writeAtomic(journal, b); e != nil {
		return nil, e
	}
	if e := s.writeAtomic(filepath.Join(s.root, "active.json"), b); e != nil {
		os.Remove(journal)
		return nil, e
	}
	os.Remove(journal)
	s.mu.Lock()
	s.manifest = m
	s.active = a
	s.mu.Unlock()
	s.broadcast(m)
	if s.updateWindow != nil {
		s.updateWindow(m.Batch)
	}
	return map[string]any{"state": state, "pack_id": m.PackID, "batch": m.Batch, "previous_pack_id": a.Previous}, nil
}
func (s *Service) install(id string) (map[string]any, error) {
	if e := s.beginOperation(); e != nil {
		return nil, e
	}
	defer s.endOperation()
	s.mu.Lock()
	st := s.staged[id]
	s.mu.Unlock()
	if st == nil {
		return nil, errors.New("未找到已校验更新包")
	}
	for f, h := range st.Manifest.FileHashes {
		if _, e := checked(st.Dir, f, h); e != nil {
			return nil, e
		}
	}
	if s.cancelled() {
		return nil, errors.New("更新已取消")
	}
	packs := filepath.Join(s.root, "packs")
	dest := filepath.Join(packs, st.Manifest.PackID)
	if _, e := os.Stat(dest); os.IsNotExist(e) {
		if e = os.Rename(st.Dir, dest); e != nil {
			return nil, e
		}
	} else {
		existing, e := loadManifest(s.root, st.Manifest.PackID)
		if e != nil {
			return nil, e
		}
		old, _ := json.Marshal(existing)
		new, _ := json.Marshal(st.Manifest)
		if digest(old) != digest(new) {
			return nil, errors.New("同版本内容不一致")
		}
	}
	_, old := s.current()
	if old.Current == st.Manifest.PackID {
		return nil, errors.New("当前已是此版本")
	}
	a := Active{Version: 1, Current: st.Manifest.PackID, Previous: &old.Current}
	v, e := s.activate(st.Manifest, a, "installed")
	if e == nil {
		s.mu.Lock()
		delete(s.staged, id)
		s.mu.Unlock()
	}
	return v, e
}
func (s *Service) rollback() (map[string]any, error) {
	if e := s.beginOperation(); e != nil {
		return nil, e
	}
	defer s.endOperation()
	_, old := s.current()
	if old.Previous == nil {
		return nil, errors.New("没有可回退版本")
	}
	m, e := loadManifest(s.root, *old.Previous)
	if e != nil {
		return nil, e
	}
	for f, h := range m.FileHashes {
		if _, e = checked(filepath.Join(s.root, "packs", m.PackID), f, h); e != nil {
			return nil, e
		}
	}
	a := Active{Version: 1, Current: *old.Previous, Previous: &old.Current}
	return s.activate(m, a, "rolled_back")
}
