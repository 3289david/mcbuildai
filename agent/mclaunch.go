// 자체 마인크래프트+Fabric 런처. Prism Launcher 를 완전히 대체한다.
//
// Prism 은 CLI 로 띄우면 "Got translations index!" 찍고 아무 이유 없이 영원히 멈추는
// 문제를 Linux/Windows 양쪽에서 똑같이 겪었고, 끝까지 원인을 알아낼 수 없었다 (Java
// 설치, 설정 마법사 비활성화, 토큰 갱신까지 다 해봐도 동일). 더 이상 그 블랙박스와
// 씨름하지 않고, 모장/Fabric 의 공개 메타 API 를 그대로 호출해서 버전 정보 -> 라이브러리
// -> 에셋을 직접 받고 javaw 를 직접 실행한다. 모든 단계가 우리 코드라서 어디서 막히면
// 바로 로그로 보인다 (이게 모든 서드파티 런처들이 실제로 하는 일이다).
package main

import (
	"archive/zip"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
)

const (
	mcVersion          = "1.21.1"
	fabricLoaderVer    = "0.20.1"
	versionManifestURL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
)

func fabricProfileURL() string {
	return fmt.Sprintf("https://meta.fabricmc.net/v2/versions/loader/%s/%s/profile/json", mcVersion, fabricLoaderVer)
}

func mcDir() string { return filepath.Join(exeDir, "minecraft") }

// ---------------- 모장/Fabric 메타 JSON 모델 ----------------

type mcRule struct {
	Action string `json:"action"`
	OS     struct {
		Name string `json:"name"`
		Arch string `json:"arch"`
	} `json:"os"`
	Features map[string]bool `json:"features"`
}

func rulesAllow(rules []mcRule, features map[string]bool) bool {
	if len(rules) == 0 {
		return true
	}
	allow := false
	for _, r := range rules {
		if r.OS.Name != "" && r.OS.Name != "windows" {
			continue
		}
		if r.OS.Arch == "x86" { // 32비트 전용 규칙은 우리(64비트)에 해당 안 됨
			continue
		}
		if len(r.Features) > 0 {
			match := true
			for k, v := range r.Features {
				if features[k] != v {
					match = false
					break
				}
			}
			if !match {
				continue
			}
		}
		allow = r.Action == "allow"
	}
	return allow
}

type mcLibrary struct {
	Name      string   `json:"name"`
	URL       string   `json:"url"`
	Rules     []mcRule `json:"rules"`
	Downloads struct {
		Artifact struct {
			Path string `json:"path"`
			URL  string `json:"url"`
			SHA1 string `json:"sha1"`
		} `json:"artifact"`
	} `json:"downloads"`
}

type versionDetail struct {
	MainClass  string `json:"mainClass"`
	AssetIndex struct {
		ID  string `json:"id"`
		URL string `json:"url"`
	} `json:"assetIndex"`
	Downloads struct {
		Client struct {
			URL string `json:"url"`
		} `json:"client"`
	} `json:"downloads"`
	Libraries []mcLibrary     `json:"libraries"`
	Arguments struct {
		Game json.RawMessage `json:"game"`
		JVM  json.RawMessage `json:"jvm"`
	} `json:"arguments"`
}

type fabricLibrary struct {
	Name string `json:"name"`
	URL  string `json:"url"`
}

type fabricProfile struct {
	MainClass string          `json:"mainClass"`
	Libraries []fabricLibrary `json:"libraries"`
	Arguments struct {
		Game []string `json:"game"`
		JVM  []string `json:"jvm"`
	} `json:"arguments"`
}

// 모장 arguments.game/jvm 은 원소가 그냥 문자열이거나 {"rules":[...],"value": 문자열|배열} 다.
func parseArgList(raw json.RawMessage, features map[string]bool) []string {
	var items []json.RawMessage
	if err := json.Unmarshal(raw, &items); err != nil {
		return nil
	}
	var out []string
	for _, item := range items {
		var s string
		if json.Unmarshal(item, &s) == nil {
			out = append(out, s)
			continue
		}
		var obj struct {
			Rules []mcRule        `json:"rules"`
			Value json.RawMessage `json:"value"`
		}
		if json.Unmarshal(item, &obj) != nil || !rulesAllow(obj.Rules, features) {
			continue
		}
		var one string
		if json.Unmarshal(obj.Value, &one) == nil {
			out = append(out, one)
			continue
		}
		var many []string
		if json.Unmarshal(obj.Value, &many) == nil {
			out = append(out, many...)
		}
	}
	return out
}

// ---------------- 메타 정보 받기 (캐시) ----------------

func fetchCachedJSON(url, cachePath string, out any) error {
	if data, err := os.ReadFile(cachePath); err == nil {
		if json.Unmarshal(data, out) == nil {
			return nil
		}
	}
	resp, err := http.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	if resp.StatusCode != 200 {
		return fmt.Errorf("메타 정보 요청 실패 (%d): %s", resp.StatusCode, url)
	}
	if err := json.Unmarshal(data, out); err != nil {
		return err
	}
	os.MkdirAll(filepath.Dir(cachePath), 0755)
	os.WriteFile(cachePath, data, 0644)
	return nil
}

func resolveVersionDetail() (*versionDetail, error) {
	cacheDir := filepath.Join(mcDir(), "meta")
	var manifest struct {
		Versions []struct {
			ID  string `json:"id"`
			URL string `json:"url"`
		} `json:"versions"`
	}
	if err := fetchCachedJSON(versionManifestURL, filepath.Join(cacheDir, "version_manifest.json"), &manifest); err != nil {
		return nil, fmt.Errorf("버전 매니페스트: %w", err)
	}
	var versionURL string
	for _, v := range manifest.Versions {
		if v.ID == mcVersion {
			versionURL = v.URL
			break
		}
	}
	if versionURL == "" {
		return nil, fmt.Errorf("버전 매니페스트에서 %s 를 찾지 못했습니다", mcVersion)
	}
	var vd versionDetail
	if err := fetchCachedJSON(versionURL, filepath.Join(cacheDir, mcVersion+".json"), &vd); err != nil {
		return nil, fmt.Errorf("버전 상세 정보: %w", err)
	}
	return &vd, nil
}

func resolveFabricProfile() (*fabricProfile, error) {
	cacheDir := filepath.Join(mcDir(), "meta")
	var fp fabricProfile
	if err := fetchCachedJSON(fabricProfileURL(), filepath.Join(cacheDir, "fabric-"+fabricLoaderVer+".json"), &fp); err != nil {
		return nil, fmt.Errorf("Fabric 프로필: %w", err)
	}
	return &fp, nil
}

// ---------------- 라이브러리/에셋 다운로드 ----------------

func mavenPath(coord string) (relPath string) {
	parts := strings.Split(coord, ":")
	group := strings.ReplaceAll(parts[0], ".", "/")
	artifact, version := parts[1], parts[2]
	classifier := ""
	if len(parts) > 3 {
		classifier = "-" + parts[3]
	}
	return fmt.Sprintf("%s/%s/%s/%s-%s%s.jar", group, artifact, version, artifact, version, classifier)
}

func downloadToFile(url, dest string) error {
	if st, err := os.Stat(dest); err == nil && st.Size() > 0 {
		return nil
	}
	resp, err := http.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("다운로드 실패 (%d): %s", resp.StatusCode, url)
	}
	os.MkdirAll(filepath.Dir(dest), 0755)
	tmp := dest + ".part"
	f, err := os.Create(tmp)
	if err != nil {
		return err
	}
	if _, err := io.Copy(f, resp.Body); err != nil {
		f.Close()
		os.Remove(tmp)
		return err
	}
	f.Close()
	return os.Rename(tmp, dest)
}

// 라이브러리들을 받아서 클래스패스 목록과, 네이티브(.dll)를 추출해야 할 jar 목록을 돌려준다.
func gatherLibraries(vd *versionDetail, fp *fabricProfile, features map[string]bool) (classpath []string, nativeJars []string, err error) {
	libDir := filepath.Join(mcDir(), "libraries")
	seen := map[string]bool{}

	add := func(path, url string) error {
		if seen[path] {
			return nil
		}
		seen[path] = true
		dest := filepath.Join(libDir, filepath.FromSlash(path))
		if err := downloadToFile(url, dest); err != nil {
			return err
		}
		classpath = append(classpath, dest)
		if strings.Contains(path, "natives-windows") && !strings.Contains(path, "natives-windows-") {
			nativeJars = append(nativeJars, dest)
		}
		return nil
	}

	for _, lib := range vd.Libraries {
		if !rulesAllow(lib.Rules, features) {
			continue
		}
		path, url := lib.Downloads.Artifact.Path, lib.Downloads.Artifact.URL
		if path == "" { // downloads 가 없는 항목은 maven 좌표로 직접 계산 (드묾)
			path = mavenPath(lib.Name)
			base := lib.URL
			if base == "" {
				base = "https://libraries.minecraft.net/"
			}
			url = strings.TrimRight(base, "/") + "/" + path
		}
		if err := add(path, url); err != nil {
			return nil, nil, fmt.Errorf("라이브러리 %s: %w", lib.Name, err)
		}
	}
	for _, lib := range fp.Libraries {
		path := mavenPath(lib.Name)
		base := lib.URL
		if base == "" {
			base = "https://maven.fabricmc.net/"
		}
		url := strings.TrimRight(base, "/") + "/" + path
		if err := add(path, url); err != nil {
			return nil, nil, fmt.Errorf("Fabric 라이브러리 %s: %w", lib.Name, err)
		}
	}
	return classpath, nativeJars, nil
}

func extractNatives(jarPaths []string, destDir string) error {
	os.MkdirAll(destDir, 0755)
	for _, jarPath := range jarPaths {
		r, err := zip.OpenReader(jarPath)
		if err != nil {
			return fmt.Errorf("네이티브 jar 열기 실패 %s: %w", jarPath, err)
		}
		for _, f := range r.File {
			if f.FileInfo().IsDir() || strings.HasPrefix(f.Name, "META-INF/") {
				continue
			}
			rc, err := f.Open()
			if err != nil {
				r.Close()
				return err
			}
			out, err := os.Create(filepath.Join(destDir, filepath.Base(f.Name)))
			if err != nil {
				rc.Close()
				r.Close()
				return err
			}
			io.Copy(out, rc)
			out.Close()
			rc.Close()
		}
		r.Close()
	}
	return nil
}

func ensureClientJar(vd *versionDetail) (string, error) {
	dest := filepath.Join(mcDir(), "versions", mcVersion, mcVersion+".jar")
	if err := downloadToFile(vd.Downloads.Client.URL, dest); err != nil {
		return "", fmt.Errorf("클라이언트 jar: %w", err)
	}
	return dest, nil
}

// 에셋(텍스처/사운드)은 수백~수천 개라서 병렬로 받는다. 이미 있는 건 건너뛴다.
func ensureAssets(vd *versionDetail) error {
	var index struct {
		Objects map[string]struct {
			Hash string `json:"hash"`
		} `json:"objects"`
	}
	if err := fetchCachedJSON(vd.AssetIndex.URL, filepath.Join(mcDir(), "assets", "indexes", vd.AssetIndex.ID+".json"), &index); err != nil {
		return fmt.Errorf("에셋 인덱스: %w", err)
	}
	type job struct{ hash string }
	jobs := make(chan job, 64)
	var wg sync.WaitGroup
	var mu sync.Mutex
	var failed error
	done, total := 0, len(index.Objects)
	worker := func() {
		defer wg.Done()
		for j := range jobs {
			dest := filepath.Join(mcDir(), "assets", "objects", j.hash[:2], j.hash)
			url := "https://resources.download.minecraft.net/" + j.hash[:2] + "/" + j.hash
			err := downloadToFile(url, dest)
			mu.Lock()
			done++
			if err != nil && failed == nil {
				failed = err
			}
			if done%200 == 0 || done == total {
				say(fmt.Sprintf(">>> 에셋 다운로드 %d/%d", done, total))
			}
			mu.Unlock()
		}
	}
	for i := 0; i < 16; i++ {
		wg.Add(1)
		go worker()
	}
	for _, obj := range index.Objects {
		jobs <- job{hash: obj.Hash}
	}
	close(jobs)
	wg.Wait()
	return failed
}

// ---------------- 실행 커맨드 조립 ----------------

func formatUUID(id string) string {
	if len(id) != 32 {
		return id
	}
	return id[0:8] + "-" + id[8:12] + "-" + id[12:16] + "-" + id[16:20] + "-" + id[20:32]
}

func buildMinecraftCommand(javaExe string, acc *Account, joinServer string, logFile *os.File) (*exec.Cmd, error) {
	say(">>> 버전/Fabric 정보 확인 중...")
	vd, err := resolveVersionDetail()
	if err != nil {
		return nil, err
	}
	fp, err := resolveFabricProfile()
	if err != nil {
		return nil, err
	}

	features := map[string]bool{"is_quick_play_multiplayer": joinServer != ""}

	say(">>> 라이브러리 확인/다운로드 중...")
	classpath, nativeJars, err := gatherLibraries(vd, fp, features)
	if err != nil {
		return nil, err
	}
	clientJar, err := ensureClientJar(vd)
	if err != nil {
		return nil, err
	}
	classpath = append(classpath, clientJar)

	nativesDir := filepath.Join(mcDir(), "natives")
	if err := extractNatives(nativeJars, nativesDir); err != nil {
		return nil, err
	}

	say(">>> 에셋(텍스처/사운드) 확인/다운로드 중... (처음 한 번은 시간이 걸릴 수 있습니다)")
	if err := ensureAssets(vd); err != nil {
		return nil, fmt.Errorf("에셋 다운로드: %w", err)
	}

	gameDir := filepath.Join(mcDir(), "instance")
	os.MkdirAll(gameDir, 0755)

	vars := map[string]string{
		"natives_directory": nativesDir,
		"launcher_name":      "mcbuildai",
		"launcher_version":   "1.0",
		"classpath":          strings.Join(classpath, ";"),
		"auth_player_name":   acc.ProfileName,
		"version_name":       mcVersion,
		"game_directory":     gameDir,
		"assets_root":        filepath.Join(mcDir(), "assets"),
		"assets_index_name":  vd.AssetIndex.ID,
		"auth_uuid":          formatUUID(acc.ProfileID),
		"auth_access_token":  acc.MCAccess,
		"clientid":           "-",
		"auth_xuid":          "-",
		"user_type":          "msa",
		"version_type":       "release",
		"quickPlayMultiplayer": joinServer,
	}
	substitute := func(args []string) []string {
		out := make([]string, len(args))
		for i, a := range args {
			for k, v := range vars {
				a = strings.ReplaceAll(a, "${"+k+"}", v)
			}
			out[i] = a
		}
		return out
	}

	jvmArgs := substitute(parseArgList(vd.Arguments.JVM, features))
	jvmArgs = append(jvmArgs, fp.Arguments.JVM...)
	gameArgs := substitute(parseArgList(vd.Arguments.Game, features))
	gameArgs = append(gameArgs, fp.Arguments.Game...)

	allArgs := append([]string{}, jvmArgs...)
	allArgs = append(allArgs, "-cp", strings.Join(classpath, ";"), fp.MainClass)
	allArgs = append(allArgs, gameArgs...)

	say(">>> javaw 실행:", javaExe, "(인자", strconv.Itoa(len(allArgs)), "개)")
	cmd := exec.Command(javaExe, allArgs...)
	cmd.Dir = gameDir
	cmd.Stdout = logFile
	cmd.Stderr = logFile
	return cmd, nil
}
