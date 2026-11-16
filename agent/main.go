// mcbuildai Windows 촬영 에이전트.
// 처음 실행하면 Java, ffmpeg, 계정 로그인까지 전부 알아서 준비하고 (마인크래프트 자체는
// Prism 없이 agent/mclaunch.go 의 자체 런처로 직접 실행한다), 그 다음부터는 서버에 접속해서
// 카메라 녹화를 위임받아 처리한다.
package main

import (
	"archive/zip"
	"bufio"
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
	"sync"
	"time"
)

// say 는 fmt.Println 과 똑같이 콘솔에 찍으면서, 동시에 최근 줄들을 버퍼에 남겨서
// 서버로 주기적으로 올린다 - 사람이 콘솔 내용을 일일이 복사해서 보내지 않아도
// 대시보드/서버 쪽에서 에이전트가 실제로 뭘 하고 있는지 바로 볼 수 있게 하기 위함.
var (
	consoleMu  sync.Mutex
	consoleLog []string
)

func say(a ...any) {
	line := fmt.Sprintln(a...)
	fmt.Print(line)
	consoleMu.Lock()
	consoleLog = append(consoleLog, strings.TrimRight(line, "\n"))
	if len(consoleLog) > 500 {
		consoleLog = consoleLog[len(consoleLog)-500:]
	}
	consoleMu.Unlock()
}

func consoleSnapshot() string {
	consoleMu.Lock()
	defer consoleMu.Unlock()
	return strings.Join(consoleLog, "\n")
}

// ---- 이 프로젝트 전용 기본값 (서버 쪽 설정과 동일) ----
// defaultToken 은 소스에 넣지 않는다 (이 저장소는 public). 빌드할 때
// -ldflags "-X main.defaultToken=실제토큰" 으로 주입한다.
const (
	defaultServerURL   = "https://yt.audest.me"
	defaultProfile     = "davvid3289"
	defaultJoinServer  = "audestyt.gamehost.kr:1035"
	defaultWindowTitle = "Minecraft 1.21.1" // 우리가 직접 실행하는 순정 클라이언트의 실제 창 제목 (Prism 쓰던 시절의 "Minecraft* 1.21.1" 과 다름)
	msaClientID        = "c36a9fb6-4f2a-41ff-90bd-ae7cc92031eb" // Prism Launcher 공개 Azure 앱 ID (MS 로그인 용도로 재사용)
	ffmpegZipURL       = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
)

var defaultToken = "" // ldflags -X 로 빌드 시점에 주입, 소스에는 비워둠

type Config struct {
	ServerURL   string `json:"server_url"`
	Token       string `json:"token"`
	Profile     string `json:"profile"`
	JoinServer  string `json:"join_server"`
	WindowTitle string `json:"window_title"`
	FFmpegPath  string `json:"ffmpeg_path"`
}

type Command struct {
	ID      string `json:"id"`
	Type    string `json:"type"`
	Name    string `json:"name"`
	WorkDir string `json:"work_dir"`
}

var cfg Config
var exeDir string
var launchProc *os.Process
var javaPath string
var account *Account
var recordings = map[string]*exec.Cmd{}
var recordFiles = map[string]string{}

func must(err error) {
	if err != nil {
		say("오류:", err)
		say("아무 키나 누르면 종료합니다...")
		bufio.NewReader(os.Stdin).ReadString('\n')
		os.Exit(1)
	}
}

// ---------------- 설정 ----------------

func loadConfig() {
	exe, _ := os.Executable()
	exeDir = filepath.Dir(exe)
	cfg = Config{
		ServerURL:   defaultServerURL,
		Token:       defaultToken,
		Profile:     defaultProfile,
		JoinServer:  defaultJoinServer,
		WindowTitle: defaultWindowTitle,
	}
	data, err := os.ReadFile(filepath.Join(exeDir, "agent.json"))
	if err == nil {
		var override Config
		if err := json.Unmarshal(data, &override); err == nil {
			merge(&cfg, override)
		}
	}
	if cfg.FFmpegPath == "" {
		cfg.FFmpegPath = filepath.Join(exeDir, "ffmpeg", "ffmpeg.exe")
	}
	if cfg.Token == "" {
		must(fmt.Errorf("토큰이 설정되지 않았습니다. exe 옆에 agent.json 을 만들고 token 값을 넣어주세요"))
	}
}

func merge(base *Config, o Config) {
	if o.ServerURL != "" {
		base.ServerURL = o.ServerURL
	}
	if o.Token != "" {
		base.Token = o.Token
	}
	if o.Profile != "" {
		base.Profile = o.Profile
	}
	if o.JoinServer != "" {
		base.JoinServer = o.JoinServer
	}
	if o.WindowTitle != "" {
		base.WindowTitle = o.WindowTitle
	}
	if o.FFmpegPath != "" {
		base.FFmpegPath = o.FFmpegPath
	}
}

// ---------------- 다운로드 / 압축 해제 ----------------

func downloadFile(url, dest string) error {
	say("다운로드 중:", url)
	resp, err := http.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("다운로드 실패 (%d): %s", resp.StatusCode, url)
	}
	f, err := os.Create(dest)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = io.Copy(f, resp.Body)
	return err
}

func unzip(zipPath, destDir string) error {
	r, err := zip.OpenReader(zipPath)
	if err != nil {
		return err
	}
	defer r.Close()
	for _, f := range r.File {
		path := filepath.Join(destDir, f.Name)
		if !strings.HasPrefix(path, filepath.Clean(destDir)+string(os.PathSeparator)) {
			continue // zip slip 방지
		}
		if f.FileInfo().IsDir() {
			os.MkdirAll(path, 0755)
			continue
		}
		os.MkdirAll(filepath.Dir(path), 0755)
		rc, err := f.Open()
		if err != nil {
			return err
		}
		out, err := os.Create(path)
		if err != nil {
			rc.Close()
			return err
		}
		io.Copy(out, rc)
		out.Close()
		rc.Close()
	}
	return nil
}

// 압축 푼 폴더 아래서 특정 파일 이름을 재귀적으로 찾는다 (zip 안에 버전별 하위 폴더가 있는 경우 대응).
func findFile(root, name string) string {
	var found string
	filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil || found != "" {
			return nil
		}
		if !d.IsDir() && strings.EqualFold(d.Name(), name) {
			found = path
		}
		return nil
	})
	return found
}

// ---------------- 부트스트랩: Java 런타임 ----------------
// Prism Launcher 는 완전히 걷어냈다 (원인을 알 수 없는 상태로 "Got translations index!"
// 이후 영원히 멈추는 문제를 계속 겪어서, 그 블랙박스를 거치지 않고 우리가 직접 버전
// 매니페스트/라이브러리/에셋을 받아 javaw 를 직접 실행하는 자체 런처로 교체했다 -
// mclaunch.go 참고). 이 함수만 남아서 그 javaw.exe 를 준비한다.

func ensureJava() string {
	javaw := filepath.Join(exeDir, "jre", "bin", "javaw.exe")
	if _, err := os.Stat(javaw); err == nil {
		return javaw
	}
	say(">>> Java 런타임이 없어서 자동으로 설치합니다...")
	zipPath := filepath.Join(os.TempDir(), "jre.zip")
	must(downloadFile("https://api.adoptium.net/v3/binary/latest/21/ga/windows/x64/jre/hotspot/normal/eclipse", zipPath))
	jreDir := filepath.Join(exeDir, "jre")
	os.MkdirAll(jreDir, 0755)
	must(unzip(zipPath, jreDir))
	os.Remove(zipPath)
	found := findFile(jreDir, "javaw.exe")
	if found == "" {
		must(fmt.Errorf("압축 해제 후 javaw.exe 를 찾지 못했습니다"))
	}
	say(">>> Java 설치 완료:", found)
	return found
}

// ---------------- 부트스트랩: 마이크로소프트 계정 로그인 ----------------

func postForm(u string, data url.Values) map[string]any {
	resp, err := http.PostForm(u, data)
	must(err)
	defer resp.Body.Close()
	var out map[string]any
	json.NewDecoder(resp.Body).Decode(&out)
	return out
}

func postJSON(u string, body map[string]any, headers map[string]string) map[string]any {
	b, _ := json.Marshal(body)
	req, _ := http.NewRequest("POST", u, bytes.NewReader(b))
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := http.DefaultClient.Do(req)
	must(err)
	defer resp.Body.Close()
	var out map[string]any
	json.NewDecoder(resp.Body).Decode(&out)
	return out
}

// Account 는 Prism 의 복잡한 accounts.json 스키마 대신 우리 런처가 직접 쓰는 간단한 형태.
// Prism 을 완전히 들어내면서 더 이상 그 스키마를 흉내 낼 필요가 없어졌다.
type Account struct {
	ProfileID    string `json:"profile_id"`
	ProfileName  string `json:"profile_name"`
	MCAccess     string `json:"mc_access_token"`
	MCExp        int64  `json:"mc_exp"`
	MSARefresh   string `json:"msa_refresh_token"`
	MSAExp       int64  `json:"msa_exp"`
}

func accountPath() string { return filepath.Join(exeDir, "mc_account.json") }

func loadAccount() (*Account, error) {
	data, err := os.ReadFile(accountPath())
	if err != nil {
		return nil, err
	}
	var a Account
	if err := json.Unmarshal(data, &a); err != nil {
		return nil, err
	}
	return &a, nil
}

func saveAccount(a *Account) { must(os.WriteFile(accountPath(), mustMarshal(a), 0644)) }

func mustMarshal(v any) []byte {
	b, err := json.MarshalIndent(v, "", "  ")
	must(err)
	return b
}

func ensureAccount() *Account {
	if a, err := loadAccount(); err == nil {
		return a
	}
	say(">>> 마인크래프트 계정 로그인이 필요합니다.")

	dev := postForm("https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode", url.Values{
		"client_id": {msaClientID}, "scope": {"XboxLive.SignIn XboxLive.offline_access"},
	})
	say()
	say(">>> 아래 주소를 열고 코드를 입력해 로그인하세요:")
	say(">>>", dev["verification_uri"])
	say(">>> 코드:", dev["user_code"])
	say()

	deviceCode := dev["device_code"].(string)
	interval := 5.0
	if v, ok := dev["interval"].(float64); ok {
		interval = v
	}
	var msToken map[string]any
	for {
		time.Sleep(time.Duration(interval) * time.Second)
		resp := postForm("https://login.microsoftonline.com/consumers/oauth2/v2.0/token", url.Values{
			"client_id": {msaClientID}, "grant_type": {"urn:ietf:params:oauth:grant-type:device_code"},
			"device_code": {deviceCode},
		})
		if e, ok := resp["error"].(string); ok {
			if e == "authorization_pending" {
				say("...아직 로그인 안 함, 계속 기다림")
				continue
			}
			if e == "slow_down" {
				interval += 5
				continue
			}
			must(fmt.Errorf("로그인 실패: %v", resp))
		}
		msToken = resp
		break
	}
	say(">>> Microsoft 로그인 완료. Xbox Live 인증 중...")
	return finishLogin(msToken)
}

// msToken(최초 로그인 또는 refresh_token 갱신으로 얻은 것) 으로 Xbox Live -> XSTS -> 마인크래프트
// 순서를 거쳐 계정 파일을 (다시) 쓴다. ensureAccount() 와 refreshAccountIfNeeded() 가 공유한다.
func finishLogin(msToken map[string]any) *Account {
	msAccess := msToken["access_token"].(string)
	xbl := postJSON("https://user.auth.xboxlive.com/user/authenticate", map[string]any{
		"Properties": map[string]any{"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com", "RpsTicket": "d=" + msAccess},
		"RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT",
	}, map[string]string{"x-xbl-contract-version": "1"})
	xblToken := xbl["Token"].(string)
	uhs := xbl["DisplayClaims"].(map[string]any)["xui"].([]any)[0].(map[string]any)["uhs"].(string)

	xsts := postJSON("https://xsts.auth.xboxlive.com/xsts/authorize", map[string]any{
		"Properties": map[string]any{"SandboxId": "RETAIL", "UserTokens": []string{xblToken}},
		"RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT",
	}, map[string]string{"x-xbl-contract-version": "1"})
	xstsToken := xsts["Token"].(string)

	say(">>> 마인크래프트 토큰 발급 중...")
	mc := postJSON("https://api.minecraftservices.com/authentication/login_with_xbox", map[string]any{
		"identityToken": fmt.Sprintf("XBL3.0 x=%s;%s", uhs, xstsToken),
	}, nil)
	mcAccess := mc["access_token"].(string)

	req, _ := http.NewRequest("GET", "https://api.minecraftservices.com/minecraft/profile", nil)
	req.Header.Set("Authorization", "Bearer "+mcAccess)
	resp, err := http.DefaultClient.Do(req)
	must(err)
	defer resp.Body.Close()
	var profile map[string]any
	json.NewDecoder(resp.Body).Decode(&profile)
	profileID, ok := profile["id"].(string)
	if !ok {
		must(fmt.Errorf("이 계정은 마인크래프트를 소유하지 않은 것 같습니다: %v", profile))
	}
	profileName := profile["name"].(string)
	say(">>> 연결됨:", profileName, "("+profileID+")")

	now := time.Now().Unix()
	expiresIn, _ := msToken["expires_in"].(float64)
	mcExpires, _ := mc["expires_in"].(float64)
	if mcExpires == 0 {
		mcExpires = 86400
	}
	refreshToken, _ := msToken["refresh_token"].(string)
	acc := &Account{
		ProfileID: profileID, ProfileName: profileName,
		MCAccess: mcAccess, MCExp: now + int64(mcExpires),
		MSARefresh: refreshToken, MSAExp: now + int64(expiresIn),
	}
	saveAccount(acc)
	say(">>> 계정 저장(갱신) 완료:", profileName)
	if cfg.Profile == "" {
		cfg.Profile = profileName
	}
	return acc
}

// Microsoft 액세스 토큰은 보통 1시간 정도만 유효한데, 실제 촬영 작업은 하루 중 아무 때나
// (며칠 뒤에) 들어올 수 있어서 부트스트랩 때 받은 토큰은 거의 항상 만료된 상태로 launch
// 시점을 맞는다. launch 직전에 우리가 먼저 refresh_token 으로 갱신해서 항상 싱싱한
// 토큰으로 게임을 실행한다.
func refreshAccountIfNeeded(a *Account) *Account {
	say(">>> (진단) MSA 토큰 만료:", a.MSAExp, "/ 현재:", time.Now().Unix(),
		"/ 남은 시간(초):", a.MSAExp-time.Now().Unix())
	if a.MSAExp > time.Now().Unix()+60 {
		return a // 아직 1분 이상 여유 있음
	}
	if a.MSARefresh == "" {
		say(">>> 경고: 계정 토큰이 만료됐는데 refresh_token 이 없습니다 (재로그인 필요할 수 있음)")
		return a
	}
	say(">>> 계정 토큰이 만료돼서 갱신합니다...")
	msToken := postForm("https://login.microsoftonline.com/consumers/oauth2/v2.0/token", url.Values{
		"client_id": {msaClientID}, "grant_type": {"refresh_token"}, "refresh_token": {a.MSARefresh},
		"scope": {"XboxLive.SignIn XboxLive.offline_access"},
	})
	if e, ok := msToken["error"].(string); ok {
		say(">>> 토큰 갱신 실패:", e, "- 기존 토큰으로 그냥 시도합니다")
		return a
	}
	return finishLogin(msToken)
}

// ---------------- 부트스트랩: ffmpeg ----------------

func ensureFFmpeg() {
	if _, err := os.Stat(cfg.FFmpegPath); err == nil {
		return
	}
	say(">>> ffmpeg 가 없어서 자동으로 설치합니다...")
	zipPath := filepath.Join(os.TempDir(), "ffmpeg.zip")
	must(downloadFile(ffmpegZipURL, zipPath))
	installDir := filepath.Join(exeDir, "ffmpeg")
	os.MkdirAll(installDir, 0755)
	must(unzip(zipPath, installDir))
	os.Remove(zipPath)
	found := findFile(installDir, "ffmpeg.exe")
	if found == "" {
		must(fmt.Errorf("압축 해제 후 ffmpeg.exe 를 찾지 못했습니다"))
	}
	cfg.FFmpegPath = found
	say(">>> ffmpeg 설치 완료:", found)
}

// ---------------- 서버와의 통신 ----------------

func apiURL(path string) string { return strings.TrimRight(cfg.ServerURL, "/") + path }

func heartbeat() bool {
	body, _ := json.Marshal(map[string]string{"token": cfg.Token})
	resp, err := http.Post(apiURL("/api/agent/heartbeat"), "application/json", bytes.NewReader(body))
	if err != nil {
		say("하트비트 실패:", err, "- 5초 후 재시도")
		return false
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		body, _ := io.ReadAll(resp.Body)
		say("하트비트 거부됨 (", resp.StatusCode, "):", string(body))
		return false
	}
	return true
}

func pushLog() {
	body, _ := json.Marshal(map[string]string{"token": cfg.Token, "log": consoleSnapshot()})
	resp, err := http.Post(apiURL("/api/agent/log"), "application/json", bytes.NewReader(body))
	if err != nil {
		return // 콘솔에 또 찍으면 그게 또 로그에 남아서 반복될 수 있으니 조용히 무시
	}
	resp.Body.Close()
}

func poll() []Command {
	u := apiURL("/api/agent/poll") + "?token=" + url.QueryEscape(cfg.Token)
	resp, err := http.Get(u)
	if err != nil {
		say("poll 실패:", err)
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
		say("ack 실패:", err)
		return
	}
	resp.Body.Close()
}

// ---------------- 명령 처리 ----------------

func psSendKey(key string) {
	ps := fmt.Sprintf(`$w=New-Object -ComObject WScript.Shell; if($w.AppActivate('%s')){Start-Sleep -Milliseconds 300; $w.SendKeys('{%s}')}`, cfg.WindowTitle, key)
	exec.Command("powershell", "-NoProfile", "-Command", ps).Run()
}

var launchMu sync.Mutex
var launchInFlight bool

func handleLaunch() map[string]any {
	launchMu.Lock()
	if launchInFlight {
		launchMu.Unlock()
		// 이전 launch 가 아직 다운로드/실행 중인데 서버 쪽이 ack 를 못 받아서(타임아웃)
		// 또 launch 를 보낸 경우 - 이미 하나 돌고 있으니 중복으로 또 받지 않는다.
		return map[string]any{"ok": true, "note": "이미 실행 처리 중"}
	}
	launchInFlight = true
	launchMu.Unlock()

	// 버전/라이브러리/에셋 다운로드는 처음 한 번은 수 분 걸릴 수 있어서, 그걸 다 끝내고
	// ack 하면 서버 쪽 대기 시간(30초)을 넘겨버려 타임아웃 -> 중복 재시도로 이어졌다.
	// 그래서 명령을 "받았다"는 것만 바로 ack 하고, 실제 준비+실행은 백그라운드에서 한다.
	go func() {
		defer func() { launchMu.Lock(); launchInFlight = false; launchMu.Unlock() }()
		account = refreshAccountIfNeeded(account)
		logPath := filepath.Join(exeDir, "mc_launch.log")
		logFile, err := os.Create(logPath)
		if err != nil {
			say(">>> 실행 준비 실패:", err)
			return
		}
		cmd, err := buildMinecraftCommand(javaPath, account, cfg.JoinServer, logFile)
		if err != nil {
			say(">>> 실행 준비 실패:", err)
			logFile.Close()
			return
		}
		if err := cmd.Start(); err != nil {
			say(">>> 실행 시작 실패:", err)
			logFile.Close()
			return
		}
		launchProc = cmd.Process
		go func() {
			cmd.Wait() // 좀비 프로세스 방지용 (출력은 이미 파일로 가고 있어서 결과는 안 봄)
			logFile.Close()
		}()
		var lastLen int
		for elapsed := 10; elapsed <= 90; elapsed += 20 {
			time.Sleep(20 * time.Second)
			data, _ := os.ReadFile(logPath)
			if len(data) == lastLen {
				say(">>> ---- 마인크래프트 실행", elapsed, "초째: 추가 출력 없음 ----")
				continue
			}
			say(">>> ---- 마인크래프트 실행 로그 (실행 후", elapsed, "초, 새 내용만) ----")
			say(string(data[lastLen:]))
			lastLen = len(data)
		}
		say(">>> ---- 로그 끝 (이 창에 뜬 내용을 그대로 복사해서 보내주세요) ----")
	}()
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
	cmd.Stdin, _ = os.Open(os.DevNull)
	stdinPipe, _ := cmd.StdinPipe()
	if err := cmd.Start(); err != nil {
		return map[string]any{"ok": false, "error": err.Error()}
	}
	recordings[name] = cmd
	recordFiles[name] = tmp
	_ = stdinPipe
	return map[string]any{"ok": true}
}

func handleRecordStop(name, workDir string) map[string]any {
	cmd, ok := recordings[name]
	if !ok {
		return map[string]any{"ok": false, "error": "녹화 중이 아님: " + name}
	}
	cmd.Process.Signal(os.Interrupt)
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

func ensureAutostart() {
	exe, err := os.Executable()
	if err != nil {
		return
	}
	// Windows 로그인 시 자동 실행 등록 (완전 자동화: 사람이 매번 수동으로 켜지 않아도 됨).
	// 실패해도 치명적이지 않음 - 그래도 지금 실행은 계속 진행한다.
	cmd := exec.Command("reg", "add", `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`,
		"/v", "mcbuildaiAgent", "/t", "REG_SZ", "/d", `"`+exe+`"`, "/f")
	if err := cmd.Run(); err != nil {
		say("(참고) 자동 시작 등록 실패 - 매번 직접 실행해야 합니다:", err)
	} else {
		say(">>> Windows 시작 시 자동 실행 등록됨")
	}
}

func main() {
	loadConfig()
	say("=== mcbuildai 촬영 에이전트 ===")
	ensureAutostart()
	javaPath = ensureJava()
	account = ensureAccount()
	ensureFFmpeg()
	say()
	say(">>> 준비 완료! 서버에 연결합니다:", cfg.ServerURL)

	lastHeartbeat := time.Now().Add(-time.Hour)
	lastStatusPrint := time.Now().Add(-time.Hour)
	lastLogPush := time.Now().Add(-time.Hour)
	wasOnline := false
	for {
		if time.Since(lastHeartbeat) > 10*time.Second {
			ok := heartbeat()
			lastHeartbeat = time.Now()
			if ok && !wasOnline {
				say(">>> 서버 연결됨. 명령을 기다립니다. (이 창을 열어두세요)")
			}
			wasOnline = ok
		}
		if wasOnline && time.Since(lastStatusPrint) > 60*time.Second {
			say("... 정상 작동 중 (", time.Now().Format("15:04:05"), ")")
			lastStatusPrint = time.Now()
		}
		if time.Since(lastLogPush) > 5*time.Second {
			pushLog()
			lastLogPush = time.Now()
		}
		for _, c := range poll() {
			say("명령 수신:", c.Type, c.Name)
			go handle(c)
		}
		time.Sleep(2 * time.Second)
	}
}
