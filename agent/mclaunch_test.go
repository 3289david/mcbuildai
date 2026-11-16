package main

import (
	"os"
	"strings"
	"testing"
)

func TestResolveAndBuildCommand(t *testing.T) {
	tmp := t.TempDir()
	exeDir = tmp

	mcVersion, err := resolveLatestReleaseVersion()
	if err != nil {
		t.Fatalf("resolveLatestReleaseVersion: %v", err)
	}
	if mcVersion == "" {
		t.Fatalf("empty resolved version")
	}
	t.Logf("resolved latest release: %s", mcVersion)

	vd, err := resolveVersionDetail(mcVersion)
	if err != nil {
		t.Fatalf("resolveVersionDetail: %v", err)
	}
	if vd.MainClass == "" || vd.Downloads.Client.URL == "" || vd.AssetIndex.URL == "" {
		t.Fatalf("version detail missing fields: %+v", vd)
	}
	t.Logf("vanilla mainClass=%s libs=%d", vd.MainClass, len(vd.Libraries))

	fp, err := resolveFabricProfile(mcVersion)
	if err != nil {
		t.Fatalf("resolveFabricProfile: %v", err)
	}
	if fp.MainClass == "" {
		t.Fatalf("fabric profile missing mainClass")
	}
	t.Logf("fabric mainClass=%s libs=%d", fp.MainClass, len(fp.Libraries))

	features := map[string]bool{"is_quick_play_multiplayer": true}
	classpath, nativeJars, err := gatherLibraries(vd, fp, features)
	if err != nil {
		t.Fatalf("gatherLibraries: %v", err)
	}
	if len(classpath) == 0 {
		t.Fatalf("classpath empty")
	}
	if len(nativeJars) == 0 {
		t.Fatalf("expected windows native jars to be selected")
	}
	t.Logf("classpath entries=%d nativeJars=%d", len(classpath), len(nativeJars))
	for _, n := range nativeJars {
		if !strings.Contains(n, "natives-windows") {
			t.Fatalf("non-native-windows jar in nativeJars: %s", n)
		}
	}

	clientJar, err := ensureClientJar(vd, mcVersion)
	if err != nil {
		t.Fatalf("ensureClientJar: %v", err)
	}
	if st, err := os.Stat(clientJar); err != nil || st.Size() < 1_000_000 {
		t.Fatalf("client jar looks wrong: %v size=%v", err, st)
	}
	t.Logf("client jar: %s (%d bytes)", clientJar, mustSize(clientJar))

	natDir := tmp + "/natives_test"
	if err := extractNatives(nativeJars, natDir); err != nil {
		t.Fatalf("extractNatives: %v", err)
	}
	entries, _ := os.ReadDir(natDir)
	if len(entries) == 0 {
		t.Fatalf("no native files extracted")
	}
	foundDLL := false
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".dll") {
			foundDLL = true
		}
	}
	if !foundDLL {
		t.Fatalf("no .dll found among extracted natives: %v", entries)
	}
	t.Logf("extracted %d native files, sample: %s", len(entries), entries[0].Name())

	// 실제 javaw 실행은 Linux 에서 테스트 못 하니 커맨드 조립까지만 확인한다.
	acc := &Account{ProfileID: "bae1154433f2424bac6c1a795c45f2af", ProfileName: "davvid3289", MCAccess: "dummy-token"}
	logFile, _ := os.CreateTemp(tmp, "log")
	defer logFile.Close()
	cmd, err := buildMinecraftCommand(acc, "audestyt.gamehost.kr:1035", logFile)
	if err != nil {
		t.Fatalf("buildMinecraftCommand: %v", err)
	}
	if len(cmd.Args) < 3 {
		t.Fatalf("command args too short: %v", cmd.Args)
	}
	joined := strings.Join(cmd.Args, " ")
	if !strings.Contains(joined, "KnotClient") {
		t.Fatalf("mainClass (fabric KnotClient) missing from args: %s", joined[:min(500, len(joined))])
	}
	// audestyt.gamehost.kr 는 A 레코드가 없고 SRV 로만 실제 주소(host.gamehost.kr:1035)를
	// 알려주는 서버라서, 최종 인자에는 SRV 로 풀린 주소가 들어가야 한다 (원본 그대로면
	// "Unknown Host" 로 또 깨진다).
	if !strings.Contains(joined, "host.gamehost.kr:1035") {
		t.Fatalf("resolved (SRV) quickPlayMultiplayer target missing from args: %s", joined)
	}
	if strings.Contains(joined, "audestyt.gamehost.kr:1035") {
		t.Fatalf("unresolved raw hostname leaked into args instead of SRV target: %s", joined)
	}
	// 26.3 은 Java 25(class file version 69)가 필요한데, 예전엔 21 을 하드코딩해서
	// UnsupportedClassVersionError 로 바로 죽었다. 실제로 더 높은 버전을 받았는지 확인.
	if !strings.Contains(cmd.Path, "jre25") {
		t.Fatalf("expected a jre25 javaw.exe for a Java-25-requiring version, got: %s", cmd.Path)
	}
	t.Logf("resolved java: %s", cmd.Path)
	if strings.Contains(joined, "${") {
		t.Fatalf("unsubstituted template variable left in args: %s", joined)
	}
	t.Logf("final arg count: %d", len(cmd.Args))
}

func mustSize(p string) int64 {
	st, _ := os.Stat(p)
	return st.Size()
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
