// mcbuildai Windows 촬영 에이전트.
// 서버(yt.audest.me)에 폴링으로 접속해서, 서버가 카메라를 RCON으로 움직이는 동안
// 이 PC에서 마인크래프트를 실행하고 화면을 녹화해서 서버로 올려준다.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

type Config struct {
	ServerURL      string `json:"server_url"`
	Token          string `json:"token"`
	PrismPath      string `json:"prism_path"`
	Instance       string `json:"instance"`
	Profile        string `json:"profile"`
	JoinServer     string `json:"join_server"`
	WindowTitle    string `json:"window_title"`
	FFmpegPath     string `json:"ffmpeg_path"`
}

type Command struct {
	ID      string `json:"id"`
	Type    string `json:"type"`
	Name    string `json:"name"`
	WorkDir string `json:"work_dir"`
}

var cfg Config
var launchProc *os.Process
var recordings = map[string]*exec.Cmd{}
var recordFiles = map[string]string{}

func loadConfig() {
	exe, _ := os.Executable()
	dir := filepath.Dir(exe)
	data, err := os.ReadFile(filepath.Join(dir, "agent.json"))
	if err != nil {
		fmt.Println("agent.json 을 읽을 수 없습니다:", err)
		fmt.Println("agent.json.example 을 참고해서 같은 폴더에 agent.json 을 만들어주세요.")
		os.Exit(1)
	}
	if err := json.Unmarshal(data, &cfg); err != nil {
		fmt.Println("agent.json 파싱 실패:", err)
		os.Exit(1)
	}
	if cfg.WindowTitle == "" {
		cfg.WindowTitle = "Minecraft"
	}
	if cfg.FFmpegPath == "" {
		cfg.FFmpegPath = "ffmpeg.exe"
	}
}

func apiURL(path string) string {
	return strings.TrimRight(cfg.ServerURL, "/") + path
}

func heartbeat() {
	body, _ := json.Marshal(map[string]string{"token": cfg.Token})
	resp, err := http.Post(apiURL("/api/agent/heartbeat"), "application/json", bytes.NewReader(body))
	if err != nil {
		fmt.Println("하트비트 실패:", err)
		return
	}
	resp.Body.Close()
}

func poll() []Command {
	u := apiURL("/api/agent/poll") + "?token=" + url.QueryEscape(cfg.Token)
	resp, err := http.Get(u)
	if err != nil {
		fmt.Println("poll 실패:", err)
		return nil
	}
	defer resp.Body.Close()
	var out struct {
		Commands []Command `json:"commands"`
	}
	json.NewDecoder(resp.Body).Decode(&out)
	return out.Commands
}

func ack(id string, result map[string]any) {
	body, _ := json.Marshal(map[string]any{"token": cfg.Token, "id": id, "result": result})
	resp, err := http.Post(apiURL("/api/agent/ack"), "application/json", bytes.NewReader(body))
	if err != nil {
		fmt.Println("ack 실패:", err)
		return
	}
	resp.Body.Close()
}

func psSendKey(key string) {
	ps := fmt.Sprintf(`$w=New-Object -ComObject WScript.Shell; if($w.AppActivate('%s')){Start-Sleep -Milliseconds 300; $w.SendKeys('{%s}')}`, cfg.WindowTitle, key)
	exec.Command("powershell", "-NoProfile", "-Command", ps).Run()
}

func handleLaunch() map[string]any {
	args := []string{"-l", cfg.Instance, "-s", cfg.JoinServer}
	if cfg.Profile != "" {
		args = append(args, "-a", cfg.Profile)
	}
	cmd := exec.Command(cfg.PrismPath, args...)
	if err := cmd.Start(); err != nil {
		return map[string]any{"ok": false, "error": err.Error()}
	}
	launchProc = cmd.Process
	return map[string]any{"ok": true}
}

func handleHideHud() map[string]any {
	psSendKey("F1")
	return map[string]any{"ok": true}
}

func handleClose() map[string]any {
	if launchProc != nil {
		launchProc.Kill()
	}
	return map[string]any{"ok": true}
}

func handleRecordStart(name string) map[string]any {
	tmp := filepath.Join(os.TempDir(), "mcbuildai_"+name+".mp4")
	os.Remove(tmp)
	args := []string{
		"-y", "-f", "gdigrab", "-framerate", "30", "-i", "title=" + cfg.WindowTitle,
		"-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", tmp,
	}
	cmd := exec.Command(cfg.FFmpegPath, args...)
	stdin, _ := cmd.StdinPipe()
	cmd.Stdout = nil
	cmd.Stderr = nil
	if err := cmd.Start(); err != nil {
		return map[string]any{"ok": false, "error": err.Error()}
	}
	recordings[name] = cmd
	recordFiles[name] = tmp
	go func() {
		// ffmpeg 가 'q' 입력을 받으면 정상 종료하므로, stdin 을 살려둔다.
		_ = stdin
	}()
	return map[string]any{"ok": true}
}

func handleRecordStop(name, workDir string) map[string]any {
	cmd, ok := recordings[name]
	if !ok {
		return map[string]any{"ok": false, "error": "녹화 중이 아님: " + name}
	}
	if stdin, err := cmd.StdinPipe(); err == nil {
		io.WriteString(stdin, "q")
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	select {
	case <-done:
	case <-time.After(10 * time.Second):
		cmd.Process.Kill()
	}
	delete(recordings, name)
	path := recordFiles[name]
	delete(recordFiles, name)

	if err := uploadFile(path, name, workDir); err != nil {
		return map[string]any{"ok": false, "error": err.Error()}
	}
	os.Remove(path)
	return map[string]any{"ok": true}
}

func uploadFile(path, name, workDir string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()

	buf := &bytes.Buffer{}
	w := multipart.NewWriter(buf)
	w.WriteField("token", cfg.Token)
	w.WriteField("name", name)
	w.WriteField("work_dir", workDir)
	fw, err := w.CreateFormFile("file", filepath.Base(path))
	if err != nil {
		return err
	}
	if _, err := io.Copy(fw, f); err != nil {
		return err
	}
	w.Close()

	req, _ := http.NewRequest("POST", apiURL("/api/agent/upload"), buf)
	req.Header.Set("Content-Type", w.FormDataContentType())
	client := &http.Client{Timeout: 5 * time.Minute}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("업로드 실패 (%d): %s", resp.StatusCode, string(body))
	}
	return nil
}

func handle(c Command) {
	var result map[string]any
	switch c.Type {
	case "launch":
		result = handleLaunch()
	case "hide_hud":
		result = handleHideHud()
	case "close":
		result = handleClose()
	case "record_start":
		result = handleRecordStart(c.Name)
	case "record_stop":
		result = handleRecordStop(c.Name, c.WorkDir)
	default:
		result = map[string]any{"ok": false, "error": "알 수 없는 명령: " + c.Type}
	}
	ack(c.ID, result)
}

func main() {
	loadConfig()
	fmt.Println("mcbuildai 에이전트 시작 ->", cfg.ServerURL)
	lastHeartbeat := time.Now().Add(-time.Hour)
	for {
		if time.Since(lastHeartbeat) > 10*time.Second {
			heartbeat()
			lastHeartbeat = time.Now()
		}
		for _, c := range poll() {
			fmt.Println("명령 수신:", c.Type, c.Name)
			go handle(c)
		}
		time.Sleep(2 * time.Second)
	}
}
