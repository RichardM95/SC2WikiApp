//go:build windows

package main

import (
	"fmt"
	"os/exec"
	"runtime"
	"syscall"
	"unsafe"
)

var user32 = syscall.NewLazyDLL("user32.dll")
var controlMu = make(chan struct{}, 1)

func wide(s string) *uint16 { p, _ := syscall.UTF16PtrFromString(s); return p }
func openBrowser(url string) error {
	c := exec.Command("rundll32.exe", "url.dll,FileProtocolHandler", url)
	c.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	return c.Start()
}
func showError(s string) {
	user32.NewProc("MessageBoxW").Call(0, uintptr(unsafe.Pointer(wide(s))), uintptr(unsafe.Pointer(wide("SCORE百科"))), 0x10)
}
func replaceFile(src, dst string) error {
	v, _, e := syscall.NewLazyDLL("kernel32.dll").NewProc("MoveFileExW").Call(uintptr(unsafe.Pointer(wide(src))), uintptr(unsafe.Pointer(wide(dst))), 0x1|0x8)
	if v == 0 {
		return e
	}
	return nil
}

type wndclass struct {
	Size, Style                        uint32
	Proc                               uintptr
	ClsExtra, WndExtra                 int32
	Instance, Icon, Cursor, Background uintptr
	Menu, Class                        *uint16
	SmallIcon                          uintptr
}
type point struct{ X, Y int32 }
type message struct {
	Window         uintptr
	Message        uint32
	Wparam, Lparam uintptr
	Time           uint32
	Point          point
	Private        uint32
}

func startControlWindow(s *Service) error {
	ready := make(chan error, 1)
	go func() {
		runtime.LockOSThread()
		defer runtime.UnlockOSThread()
		instance, _, _ := syscall.NewLazyDLL("kernel32.dll").NewProc("GetModuleHandleW").Call(0)
		var hwnd, label uintptr
		proc := syscall.NewCallback(func(h uintptr, msg uint32, wp, lp uintptr) uintptr {
			switch msg {
			case 0x10:
				go s.shutdown()
				return 0
			case 0x111:
				if wp&0xffff == 1 {
					go openBrowser(s.origin + "/")
				} else if wp&0xffff == 2 {
					go s.shutdown()
				}
				return 0
			case 0x8001:
				user32.NewProc("ShowWindow").Call(h, 9)
				user32.NewProc("SetForegroundWindow").Call(h)
				return 0
			case 0x8002:
				m, _ := s.current()
				user32.NewProc("SetWindowTextW").Call(label, uintptr(unsafe.Pointer(wide("运行中\r\n"+s.origin+"\r\n数据版本："+m.Batch))))
				return 0
			case 0x8003:
				user32.NewProc("DestroyWindow").Call(h)
				return 0
			case 2:
				user32.NewProc("PostQuitMessage").Call(0)
				return 0
			}
			r, _, _ := user32.NewProc("DefWindowProcW").Call(h, uintptr(msg), wp, lp)
			return r
		})
		name := wide(fmt.Sprintf("SCOREWikiControl%d", instance))
		wc := wndclass{Proc: proc, Instance: instance, Background: 16, Class: name}
		wc.Size = uint32(unsafe.Sizeof(wc))
		atom, _, err := user32.NewProc("RegisterClassExW").Call(uintptr(unsafe.Pointer(&wc)))
		if atom == 0 {
			ready <- err
			return
		}
		hwnd, _, err = user32.NewProc("CreateWindowExW").Call(0, uintptr(unsafe.Pointer(name)), uintptr(unsafe.Pointer(wide("SCORE百科控制窗口"))), 0x00ca0000, uintptr(200), uintptr(200), uintptr(540), uintptr(230), 0, 0, instance, 0)
		if hwnd == 0 {
			ready <- err
			return
		}
		label, _, _ = user32.NewProc("CreateWindowExW").Call(0, uintptr(unsafe.Pointer(wide("STATIC"))), uintptr(unsafe.Pointer(wide("运行中\r\n"+s.origin+"\r\n数据版本："+s.manifest.Batch))), 0x50000000, 20, 20, 480, 95, hwnd, 0, instance, 0)
		for i, t := range []string{"打开百科", "退出"} {
			user32.NewProc("CreateWindowExW").Call(0, uintptr(unsafe.Pointer(wide("BUTTON"))), uintptr(unsafe.Pointer(wide(t))), 0x50010000, uintptr(20+i*150), 125, 130, 35, hwnd, uintptr(i+1), instance, 0)
		}
		s.windowHandle = hwnd
		s.activateWindow = func() { user32.NewProc("PostMessageW").Call(hwnd, 0x8001, 0, 0) }
		s.closeWindow = func() { user32.NewProc("PostMessageW").Call(hwnd, 0x8003, 0, 0) }
		s.updateWindow = func(string) { user32.NewProc("PostMessageW").Call(hwnd, 0x8002, 0, 0) }
		user32.NewProc("ShowWindow").Call(hwnd, 5)
		user32.NewProc("UpdateWindow").Call(hwnd)
		ready <- nil
		var msg message
		for {
			v, _, _ := user32.NewProc("GetMessageW").Call(uintptr(unsafe.Pointer(&msg)), 0, 0, 0)
			if int32(v) <= 0 {
				break
			}
			user32.NewProc("TranslateMessage").Call(uintptr(unsafe.Pointer(&msg)))
			user32.NewProc("DispatchMessageW").Call(uintptr(unsafe.Pointer(&msg)))
		}
	}()
	return <-ready
}
