package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

func normalizePrefix(s string) string {
	return strings.TrimSpace(strings.TrimLeft(strings.TrimSpace(s), "-"))
}
func validateConfig(b []byte) (map[string]any, error) {
	var c map[string]any
	if json.Unmarshal(b, &c) != nil {
		return nil, errors.New("阵营配置不是有效 JSON")
	}
	if c["version"] != float64(1) {
		return nil, errors.New("阵营配置版本无效")
	}
	rows, ok := c["factions"].([]any)
	if !ok || len(rows) == 0 {
		return nil, errors.New("阵营配置不能为空")
	}
	ids, prefixes := map[string]bool{}, map[string]bool{}
	for _, v := range rows {
		r, ok := v.(map[string]any)
		if !ok {
			return nil, errors.New("阵营记录无效")
		}
		id, ok := r["id"].(string)
		if !ok || !validPack(id) || ids[strings.ToLower(id)] {
			return nil, errors.New("阵营键无效或重复")
		}
		ids[strings.ToLower(id)] = true
		n, ok := r["name"].(string)
		if !ok || strings.TrimSpace(n) == "" {
			return nil, fmt.Errorf("阵营 %s 名称为空", id)
		}
		if _, ok = r["defaultVisible"].(bool); !ok {
			return nil, errors.New("defaultVisible 必须是布尔值")
		}
		if _, ok = r["order"].(float64); !ok {
			return nil, errors.New("order 必须是数字")
		}
		ps, ok := r["prefixes"].([]any)
		if !ok || len(ps) == 0 {
			return nil, errors.New("前缀不能为空")
		}
		local := map[string]bool{}
		for _, p := range ps {
			x, ok := p.(string)
			x = strings.ToLower(normalizePrefix(x))
			if !ok || x == "" || prefixes[x] {
				return nil, errors.New("前缀为空或重复")
			}
			prefixes[x] = true
			local[x] = true
		}
		if branches, exists := r["branches"]; exists {
			bs, ok := branches.(map[string]any)
			if !ok {
				return nil, errors.New("分支映射无效")
			}
			for k, v := range bs {
				label, ok := v.(string)
				if !ok || strings.TrimSpace(label) == "" || !local[strings.ToLower(normalizePrefix(k))] {
					return nil, errors.New("分支必须对应本阵营前缀")
				}
			}
		}
	}
	return c, nil
}
func (s *Service) reloadConfig(startup bool) error {
	p := filepath.Join(s.root, "config", "factions.json")
	b, e := os.ReadFile(p)
	var c map[string]any
	if e == nil {
		c, e = validateConfig(b)
	}
	if e != nil {
		s.mu.Lock()
		s.configWarning = "阵营配置无效，保留上一份有效配置：" + e.Error()
		s.mu.Unlock()
		if startup {
			old, err := os.ReadFile(filepath.Join(s.root, "config", "factions.last-valid.json"))
			if err == nil {
				if c, err = validateConfig(old); err == nil {
					s.mu.Lock()
					s.config = c
					s.configRevision = digest(old)
					s.mu.Unlock()
					return e
				}
			}
			def, err := os.ReadFile(filepath.Join(s.root, "defaults", "factions.json"))
			if err == nil {
				if c, err = validateConfig(def); err == nil {
					s.mu.Lock()
					s.config = c
					s.configRevision = digest(def)
					s.mu.Unlock()
					return e
				}
			}
		}
		return e
	}
	s.mu.Lock()
	s.config = c
	s.configRevision = digest(b)
	s.configWarning = ""
	s.mu.Unlock()
	if e = atomicWrite(filepath.Join(s.root, "config", "factions.last-valid.json"), b); e != nil {
		s.mu.Lock()
		s.configWarning = "配置已加载，但无法保存恢复副本：" + e.Error()
		s.mu.Unlock()
	}
	return nil
}
func (s *Service) configReply(w http.ResponseWriter) {
	s.mu.Lock()
	defer s.mu.Unlock()
	reply(w, map[string]any{"config": s.config, "revision": s.configRevision, "warning": s.configWarning, "path": filepath.Join(s.root, "config", "factions.json")})
}
