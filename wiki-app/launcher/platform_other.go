//go:build !windows

package main

import (
	"errors"
	"os"
)

func replaceFile(src, dst string) error { return os.Rename(src, dst) }
func startControlWindow(s *Service) error {
	return errors.New("控制窗口仅支持 Windows；使用 --headless")
}
func openBrowser(url string) error { return nil }
func showError(message string)     {}
