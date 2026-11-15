// mcbuildai Windows 촬영 에이전트.
// 처음 실행하면 Prism Launcher, ffmpeg, 마인크래프트 인스턴스, 계정 로그인까지 전부 알아서 준비하고,
// 그 다음부터는 서버에 접속해서 카메라 녹화를 위임받아 처리한다.
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
	defaultInstance    = "BuildCam"
	defaultProfile     = "davvid3289"
	defaultJoinServer  = "audestyt.gamehost.kr:1035"
	defaultWindowTitle = "Minecraft* 1.21.1"
	msaClientID        = "c36a9fb6-4f2a-41ff-90bd-ae7cc92031eb" // Prism Launcher 공개 Azure 앱 ID
	prismReleaseAPI    = "https://api.github.com/repos/PrismLauncher/PrismLauncher/releases/latest"
	ffmpegZipURL       = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
)

var defaultToken = "" // ldflags -X 로 빌드 시점에 주입, 소스에는 비워둠

type Config struct {
	ServerURL   string `json:"server_url"`
	Token       string `json:"token"`
	PrismPath   string `json:"prism_path"`
	Instance    string `json:"instance"`
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
		Instance:    defaultInstance,
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
	if cfg.PrismPath == "" {
		cfg.PrismPath = filepath.Join(exeDir, "PrismLauncher", "prismlauncher.exe")
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
	if o.PrismPath != "" {
		base.PrismPath = o.PrismPath
	}
	if o.Instance != "" {
		base.Instance = o.Instance
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

// ---------------- 부트스트랩: Prism Launcher ----------------

func ensurePrism() {
	if _, err := os.Stat(cfg.PrismPath); err == nil {
		return
	}
	say(">>> Prism Launcher 가 없어서 자동으로 설치합니다...")
	resp, err := http.Get(prismReleaseAPI)
	must(err)
	defer resp.Body.Close()
	var rel struct {
		Assets []struct {
			Name               string `json:"name"`
			BrowserDownloadURL string `json:"browser_download_url"`
		} `json:"assets"`
	}
	must(json.NewDecoder(resp.Body).Decode(&rel))
	var assetURL string
	for _, a := range rel.Assets {
		if strings.Contains(a.Name, "Windows-MSVC-Portable") && strings.HasSuffix(a.Name, ".zip") {
			assetURL = a.BrowserDownloadURL
			break
		}
	}
	if assetURL == "" {
		must(fmt.Errorf("Prism Launcher Windows 포터블 다운로드를 찾지 못했습니다"))
	}
	zipPath := filepath.Join(os.TempDir(), "prismlauncher.zip")
	must(downloadFile(assetURL, zipPath))
	installDir := filepath.Join(exeDir, "PrismLauncher")
	os.MkdirAll(installDir, 0755)
	must(unzip(zipPath, installDir))
	os.Remove(zipPath)
	found := findFile(installDir, "prismlauncher.exe")
	if found == "" {
		must(fmt.Errorf("압축 해제 후 prismlauncher.exe 를 찾지 못했습니다"))
	}
	cfg.PrismPath = found
	// 포터블 모드 표시 파일 (Prism 은 exe 옆에 이 파일이 있으면 설정을 exe 폴더 기준으로 저장한다)
	os.WriteFile(filepath.Join(filepath.Dir(found), "portable.txt"), []byte(""), 0644)
	say(">>> Prism Launcher 설치 완료:", found)
}

func prismDataDir() string {
	return filepath.Dir(cfg.PrismPath)
}

// Prism 은 Java/언어/테마/계정 중 뭐라도 설정이 비어있으면 첫 실행 시 "설정 마법사" 모달을
// 띄우는데, 그걸 눌러줄 사람이 없는 헤드리스 환경에선 그 창에서 영원히 멈춘다
// (Application::createSetupWizard() 조건 참고). 그래서 이 값들을 미리 다 채워서
// 마법사 자체가 뜰 조건을 아예 없앤다. Linux 쪽에서 dialog_killer.sh 로 다이얼로그를
// 자동으로 눌러준 것과 같은 목적, Windows 에선 그 방식이 안 통해서 설정으로 막는다.
func ensurePrismSettings() {
	path := filepath.Join(prismDataDir(), "prismlauncher.cfg")
	content := "[General]\n" +
		"IgnoreJavaWizard=true\n" +
		"UserAskedAboutAutomaticJavaDownload=true\n" +
		"AutomaticJavaSwitch=true\n" +
		"Language=en_US\n" +
		"ApplicationTheme=system\n" +
		"IconTheme=pe_colored\n"
	must(os.WriteFile(path, []byte(content), 0644))
	say(">>> Prism 설정 마법사 비활성화 완료")
}

// ---------------- 부트스트랩: Java 런타임 ----------------
// Prism 자체가 실행되는 건 Java 와 무관하지만, 실제 마인크래프트(Fabric)를 켜려면 JRE 가 있어야
// 한다. 없으면 Prism 이 설정 대화상자를 띄우거나 조용히 실패해서, Prism 프로세스는 뜨는데
// 플레이어는 영원히 서버에 접속 못 하는 상태가 된다 (겉보기로는 "실행 성공"처럼 보여서 더 헷갈림).
// Prism 의 자동 Java 다운로드 기능에 맡기지 않고 우리가 직접 받아서 인스턴스에 고정 지정한다.

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

// ---------------- 부트스트랩: BuildCam 인스턴스 ----------------

func ensureInstance(javaPath string) {
	instDir := filepath.Join(prismDataDir(), "instances", cfg.Instance)
	os.MkdirAll(filepath.Join(instDir, ".minecraft"), 0755)
	// 항상 다시 써서 Java 경로 지정이 기존에 이미 만들어진 인스턴스에도 적용되게 한다
	// (처음에 instance.cfg 가 있으면 건너뛰던 방식이라, Java 를 나중에 추가해도 기존 설치엔
	// 반영이 안 되는 문제가 있었다).
	javaForIni := strings.ReplaceAll(javaPath, `\`, "/")
	instanceCfg := "[General]\nConfigVersion=1.2\nInstanceType=OneSix\nname=" + cfg.Instance +
		"\niconKey=default\nOverrideJavaLocation=true\nJavaPath=" + javaForIni + "\n"
	wasNew := true
	if _, err := os.Stat(filepath.Join(instDir, "instance.cfg")); err == nil {
		wasNew = false
	}
	must(os.WriteFile(filepath.Join(instDir, "instance.cfg"), []byte(instanceCfg), 0644))
	if wasNew {
		mmcPack := `{
  "components": [
    {"important": true, "uid": "net.minecraft", "version": "1.21.1"},
    {"uid": "net.fabricmc.fabric-loader", "version": "0.20.1"}
  ],
  "formatVersion": 1
}`
		must(os.WriteFile(filepath.Join(instDir, "mmc-pack.json"), []byte(mmcPack), 0644))
		say(">>> 인스턴스 생성 완료")
	} else {
		say(">>> 인스턴스 Java 경로 갱신 완료")
	}
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

func ensureAccount() {
	accPath := filepath.Join(prismDataDir(), "accounts.json")
	if data, err := os.ReadFile(accPath); err == nil && len(data) > 100 {
		return // 이미 로그인된 계정이 있다고 가정
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
	account := map[string]any{
		"type": "MSA", "msa-client-id": msaClientID,
		"msa": map[string]any{"iat": now, "exp": now + int64(expiresIn), "token": msAccess,
			"refresh_token": msToken["refresh_token"], "extra": msToken},
		"utoken": map[string]any{"iat": now, "token": xblToken, "extra": map[string]string{"uhs": uhs}},
		"xrp-mc": map[string]any{"iat": now, "token": xstsToken, "extra": map[string]string{"uhs": uhs}},
		"ygg":    map[string]any{"iat": now, "exp": now + int64(mcExpires), "token": mcAccess},
		"profile":     map[string]any{"id": profileID, "name": profileName},
		"entitlement": map[string]any{"ownsMinecraft": true, "canPlayMinecraft": true},
		"active": true, "id": profileID,
	}
	data := map[string]any{"formatVersion": 3, "accounts": []any{account}}
	b, _ := json.MarshalIndent(data, "", "  ")
	must(os.WriteFile(accPath, b, 0644))
	say(">>> 계정 저장 완료:", profileName)
	if cfg.Profile == "" {
		cfg.Profile = profileName
	}
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

func handleLaunch() map[string]any {
	args := []string{"-l", cfg.Instance, "-s", cfg.JoinServer}
	if cfg.Profile != "" {
		args = append(args, "-a", cfg.Profile)
	}
	// Prism 의 실제 출력(라이브러리 다운로드, 계정 인증, 크래시 등)을 파일로 받아서 실행 후
	// 몇 초 뒤 콘솔에 그대로 찍어준다 - 창이 안 뜨는 이유를 사람이 직접 Prism GUI 를 파고들지
	// 않고도 이 콘솔만 보고 알 수 있게 하기 위함.
	logPath := filepath.Join(exeDir, "prism_launch.log")
	logFile, err := os.Create(logPath)
	if err != nil {
		return map[string]any{"ok": false, "error": err.Error()}
	}
	cmd := exec.Command(cfg.PrismPath, args...)
	cmd.Stdout = logFile
	cmd.Stderr = logFile
	if err := cmd.Start(); err != nil {
		logFile.Close()
		return map[string]any{"ok": false, "error": err.Error()}
	}
	launchProc = cmd.Process
	go func() {
		cmd.Wait() // 좀비 프로세스 방지용 (출력은 이미 파일로 가고 있어서 결과는 안 봄)
		logFile.Close()
	}()
	go func() {
		time.Sleep(10 * time.Second)
		data, _ := os.ReadFile(logPath)
		say(">>> ---- Prism 실행 로그 (실행 후 10초) ----")
		say(string(data))
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
	ensurePrism()
	ensurePrismSettings()
	javaPath := ensureJava()
	ensureInstance(javaPath)
	ensureAccount()
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
