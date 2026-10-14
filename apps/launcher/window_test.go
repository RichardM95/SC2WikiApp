//go:build windows

package main

import (
	"testing"
	"time"
)

func TestNativeControlWindowCloseStopsService(t *testing.T) {
	s, e := load(fixture(t))
	if e != nil {
		t.Fatal(e)
	}
	if e = startControlWindow(s); e != nil {
		t.Fatal(e)
	}
	s.activateWindow()
	if s.windowHandle == 0 {
		t.Fatal("own control window missing")
	}
	user32.NewProc("PostMessageW").Call(s.windowHandle, 0x10, 0, 0)
	select {
	case <-s.stop:
	case <-time.After(2 * time.Second):
		t.Fatal("window X left service alive")
	}
}
