package main

import (
	"os"
	"strings"
	"testing"
)

func TestResolveAndBuildCommand(t *testing.T) {
	tmp := t.TempDir()
	exeDir = tmp

	vd, err := resolveVersionDetail()
	if err != nil {
		t.Fatalf("resolveVersionDetail: %v", err)
	}
	if vd.MainClass == "" || vd.Downloads.Client.URL == "" || vd.AssetIndex.URL == "" {
		t.Fatalf("version detail missing fields: %+v", vd)
	}
	t.Logf("vanilla mainClass=%s libs=%d", vd.MainClass, len(vd.Libraries))

	fp, err := resolveFabricProfile()
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

	clientJar, err := ensureClientJar(vd)
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
	cmd, err := buildMinecraftCommand("/usr/bin/true", acc, "audestyt.gamehost.kr:1035", logFile)
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
	if !strings.Contains(joined, "audestyt.gamehost.kr:1035") {
		t.Fatalf("quickPlayMultiplayer target missing from args")
	}
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
