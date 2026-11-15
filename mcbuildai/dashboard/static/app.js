// mcbuildai 관리자 대시보드. 모든 데이터는 /api 에서 받아온 실제 값만 표시한다.
const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fileUrl = p => "/files?path=" + encodeURIComponent(p);
const pct = v => v == null ? "-" : (v * 100).toFixed(0) + "%";
const fmtT = s => s ? new Date(s).toLocaleString("ko-KR") : "-";
let timer = null, catalog = null;

async function api(path, opt = {}) {
  const o = {...opt};
  if (o.json !== undefined) { o.method = o.method || "POST"; o.headers = {"Content-Type": "application/json"}; o.body = JSON.stringify(o.json); }
  const r = await fetch(path, o);
  const t = await r.text();
  let d; try { d = JSON.parse(t); } catch { d = t; }
  if (!r.ok) throw new Error((d && d.detail) || t);
  return d;
}
function toast(msg, bad) {
  const t = $("#toast"); t.textContent = msg; t.style.display = "block"; t.style.color = bad ? "var(--err)" : "";
  clearTimeout(t._h); t._h = setTimeout(() => t.style.display = "none", 3500);
}
const pill = s => `<span class="pill ${esc(s)}">${esc(s)}</span>`;
const empty = msg => `<div class="empty">${esc(msg)}</div>`;
function table(rows, cols) {
  if (!rows || !rows.length) return empty("데이터가 없습니다");
  return `<table><thead><tr>${cols.map(c => `<th>${esc(c[0])}</th>`).join("")}</tr></thead><tbody>${
    rows.map(r => `<tr>${cols.map(c => `<td>${c[1](r)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}
async function getCatalog() { return catalog || (catalog = await api("/api/catalog")); }
const opts = (obj, sel, blank) => (blank ? `<option value="">${blank}</option>` : "") +
  Object.entries(obj).map(([k, v]) => `<option value="${esc(k)}" ${k === sel ? "selected" : ""}>${esc(v)} (${esc(k)})</option>`).join("");

// ---------------------------------------------------------------- Dashboard
async function vDashboard() {
  const d = await api("/api/overview");
  const s = d.system;
  const gpu = s.gpu ? `${s.gpu.util_percent}% / ${(s.gpu.mem_used_mb / 1024).toFixed(1)}GB` : "감지 안 됨";
  view.innerHTML = `
  <div class="grid g4">
    <div class="card stat"><h2>오늘 제작 진행률</h2><div class="v">${d.today_progress == null ? "-" : pct(d.today_progress)}</div>
      <div class="bar"><i style="width:${(d.today_progress || 0) * 100}%"></i></div><div class="s">오늘 작업 ${d.today_jobs}건</div></div>
    <div class="card stat"><h2>오늘 업로드</h2><div class="v">${d.today_uploads}</div><div class="s">YouTube 에 올라간 영상</div></div>
    <div class="card stat"><h2>대기 중</h2><div class="v">${d.queued}</div><div class="s">대기/재시도 작업</div></div>
    <div class="card stat"><h2>남은 디스크</h2><div class="v">${s.disk_free_gb}GB</div><div class="s">출력 폴더 기준</div></div>
  </div>
  <div class="grid g3" style="margin-top:14px">
    <div class="card"><h2>현재 건축물</h2>${d.current ? `
      ${d.current.preview_path ? `<img class="live" src="${fileUrl(d.current.preview_path)}">` : ""}
      <div style="margin-top:8px"><b>${esc(d.current.name || "생성 중")}</b> ${pill(d.current.stage)}</div>
      <div class="bar"><i style="width:${(d.current.progress || 0) * 100}%"></i></div>
      <div class="muted">단계 진행 ${pct(d.current.progress)}</div>` : empty("진행 중인 작업 없음")}</div>
    <div class="card"><h2>실시간 촬영 화면</h2>${d.live_preview.available ?
      `<img class="live" id="liveimg" src="/live.jpg?t=${Date.now()}"><div class="muted">${d.live_preview.age_sec}초 전 프레임</div>` :
      empty("녹화 중일 때 1초마다 갱신됩니다")}</div>
    <div class="card"><h2>시스템</h2><div class="kv">
      <div>CPU</div><div>${s.cpu_percent}%</div>
      <div>RAM</div><div>${s.ram_percent}% (${s.ram_used_gb}/${s.ram_total_gb}GB)</div>
      <div>GPU</div><div>${esc(s.gpu ? s.gpu.name + " " : "")}${gpu}</div>
      <div>건축 일시정지</div><div>${d.build_paused ? '<span class="warn">일시정지됨</span>' : "아니오"}</div></div>
      <h2 style="margin-top:14px">렌더링 상태</h2>${d.render.length ? d.render.map(r => `<div>${esc(r.name)} ${pct(r.progress)}</div>`).join("") : empty("렌더링 중인 작업 없음")}
    </div>
  </div>
  <div class="grid g3" style="margin-top:14px">
    <div class="card"><h2>대기 중인 건축물</h2>${table(d.waiting_buildings, [["작업", r => r.job_id], ["이름", r => esc(r.name || "(기획 단계)")], ["단계", r => pill(r.stage)]])}</div>
    <div class="card"><h2>YouTube 게시 상태</h2>${table(d.youtube, [["제목", r => r.url ? `<a href="${esc(r.url)}" target="_blank">${esc(r.title)}</a>` : esc(r.title)], ["상태", r => pill(r.status)], ["예약", r => fmtT(r.publish_at)]])}</div>
    <div class="card"><h2>최근 오류</h2>${table(d.recent_errors, [["시각", r => fmtT(r.ts)], ["출처", r => esc(r.source)], ["내용", r => `<span class="err">${esc(r.message.slice(0, 160))}</span>`]])}</div>
  </div>`;
}

// ---------------------------------------------------------------- AI Studio
async function vStudio() {
  const [c, bs, gm, ag] = await Promise.all([getCatalog(), api("/api/buildings?limit=60"), api("/api/studio/genmc/status"), api("/api/studio/agent_info")]);
  view.innerHTML = `
  <div class="grid g2">
    <div class="card"><h2>건축물 생성</h2>
      <form id="gen">
        <label>방식</label><select name="mode"><option value="random">무작위 / 조건 지정</option><option value="prompt">텍스트 프롬프트</option>
          <option value="image">이미지 참고</option><option value="variant">기존 건축물 변형</option></select>
        <label>프롬프트</label><textarea name="prompt" placeholder="예: 절벽 위에 세운 거대한 고딕 대성당, 보라색 스테인드글라스"></textarea>
        <label>참고 이미지</label><input type="file" name="image" accept="image/*">
        <div class="grid g3"><div><label>종류</label><select name="btype">${opts(c.types, "", "자동")}</select></div>
          <div><label>양식</label><select name="style">${opts(c.styles, "", "자동")}</select></div>
          <div><label>규모</label><select name="scale">${opts(c.scales, "", "자동")}</select></div></div>
        <div class="grid g2"><div><label>변형할 건축물 ID</label><input name="variant_of" type="number"></div>
          <div><label>생성 엔진</label><select name="engine"><option value="auto">자동 (genMC 우선)</option><option value="genmc">genMC 만</option><option value="procedural">내장 절차적 생성기</option></select></div></div>
        <div class="row" style="margin-top:12px"><button class="primary">생성 시작</button><span class="muted">genMC 설계는 수 분 걸릴 수 있습니다</span></div>
      </form></div>
    <div class="card"><h2>genMC 연결</h2>
      <div class="kv"><div>페어링</div><div>${gm.paired ? '<span class="ok">저장됨</span>' : '<span class="warn">필요</span>'}</div>
        <div>Claude API 키</div><div>${gm.ai_available ? '<span class="ok">설정됨</span>' : '<span class="warn">없음 (ANTHROPIC_API_KEY)</span>'}</div>
        <div>상태</div><div>${gm.error ? `<span class="err">${esc(gm.error)}</span>` : esc(gm.status || "-")}</div></div>
      <p class="muted">1) 브라우저에서 https://genmc.app/build 를 열어 둡니다. 2) 스튜디오 오른쪽 에이전트 칸의 페어링 코드(10분 유효)를 입력합니다. 이후에는 저장된 재연결 키를 씁니다. 스튜디오 탭은 계속 열려 있어야 합니다.</p>
      <form id="pair" class="row"><input name="code" placeholder="예: K7Q-4MX" style="max-width:200px"><button>페어링</button></form>
    </div>
  </div>
  <div class="card" style="margin-top:14px"><h2>Windows 촬영 에이전트</h2>
    <div class="kv"><div>연결 상태</div><div>${ag.online ? '<span class="ok">온라인</span>' : '<span class="warn">오프라인</span>'}</div>
      <div>토큰</div><div><code id="agent-token" style="word-break:break-all">${esc(ag.token || "(없음)")}</code>
        ${ag.token ? ' <button type="button" id="copy-token" class="btn">복사</button>' : ''}</div>
      <div>exe 다운로드</div><div>${ag.exe_download_url
        ? `<a class="btn" href="${esc(ag.exe_download_url)}">mcbuildai-agent.exe 받기</a> <span class="muted">(토큰 내장됨, agent.json 필요 없음${ag.exe_built_at ? ", 빌드: " + fmtT(ag.exe_built_at) : ""})</span>`
        : '<span class="warn">아직 빌드되지 않음</span>'}</div></div>
    <p class="muted">Windows PC에서 위 exe 를 그냥 실행하면 됩니다. 처음 실행 시 Prism Launcher/ffmpeg/계정 로그인까지 자동으로 준비합니다.</p>
  </div>
  <div class="card" style="margin-top:14px"><h2>생성된 건축물</h2>
    <div class="thumbs">${bs.length ? bs.map(b => `<div class="card">
      ${b.preview_path ? `<img src="${fileUrl(b.preview_path)}" loading="lazy">` : `<div class="empty">${esc(b.status)}</div>`}
      <div style="margin-top:8px"><b>#${b.id} ${esc(b.name)}</b></div>
      <div class="muted">${esc(c.types[b.btype] || b.btype)} / ${esc(c.styles[b.style] || b.style)} / ${esc(c.scales[b.scale] || b.scale)}</div>
      <div class="muted">${b.block_count ? b.block_count.toLocaleString() + " 블록" : ""} ${b.difficulty ? "/ 난이도 " + b.difficulty : ""} ${b.est_minutes != null ? "/ 예상 " + b.est_minutes + "분" : ""}</div>
      <div class="row" style="margin-top:6px">${pill(b.status)}
        ${b.schematic_path ? `<a class="btn" href="${fileUrl(b.schematic_path)}">설계도</a>` : ""}
        ${b.status === "generated" ? `<button data-job="${b.id}">제작 작업 만들기</button>` : ""}</div></div>`).join("") : empty("아직 생성된 건축물이 없습니다")}</div></div>`;
  $("#gen").onsubmit = async e => {
    e.preventDefault();
    try { await api("/api/studio/generate", {method: "POST", body: new FormData(e.target)}); toast("생성을 시작했습니다. 잠시 후 목록을 새로고침합니다."); setTimeout(render, 4000); }
    catch (err) { toast(err.message, true); }
  };
  $("#pair").onsubmit = async e => {
    e.preventDefault();
    try { await api("/api/studio/genmc/pair", {json: {code: e.target.code.value}}); toast("페어링 완료"); render(); }
    catch (err) { toast(err.message, true); }
  };
  const copyBtn = $("#copy-token");
  if (copyBtn) copyBtn.onclick = () => {
    navigator.clipboard.writeText($("#agent-token").textContent).then(() => toast("토큰을 복사했습니다"));
  };
  view.querySelectorAll("[data-job]").forEach(b => b.onclick = async () => {
    const r = await api("/api/jobs", {json: {building_id: +b.dataset.job, priority: 3, experiment: false}});
    toast(`작업 ${r.job_id} 생성`);
  });
}

// ---------------------------------------------------------------- Build Manager
async function vBuild() {
  const [jobs, ov] = await Promise.all([api("/api/jobs"), api("/api/overview")]);
  view.innerHTML = `<div class="card"><div class="row">
      <button id="pause">${ov.build_paused ? "건축 재개" : "건축 일시정지"}</button>
      <button data-srv="start">서버 시작</button><button data-srv="restart">서버 재시작</button><button data-srv="backup">월드 백업</button>
      <button id="newjob">새 작업 (자동 기획)</button></div></div>
    <div class="card" style="margin-top:14px"><h2>작업 대기열</h2>${table(jobs, [
      ["ID", r => r.id], ["건축물", r => esc(r.name || "(기획 전)")], ["단계", r => pill(r.stage)], ["상태", r => pill(r.status)],
      ["진행", r => `<div class="bar" style="width:120px"><i style="width:${(r.progress || 0) * 100}%"></i></div>${r.build_index != null && r.block_count ? `<span class="muted">${r.build_index.toLocaleString()}/${r.block_count.toLocaleString()}</span>` : ""}`],
      ["우선순위", r => `<input type="number" value="${r.priority}" data-prio="${r.id}" style="width:64px">`],
      ["시도", r => r.attempts], ["촬영 구간", r => esc(r.segments.join(", "))],
      ["검증", r => r.verify ? `${r.verify.checked} 확인 / ${r.verify.fixed} 수정` : "-"],
      ["오류", r => `<span class="err">${esc((r.last_error || "").slice(0, 120))}</span>`],
      ["", r => `<button data-act="retry" data-id="${r.id}">재시도</button> <button data-act="cancel" data-id="${r.id}">취소</button>`]])}</div>`;
  $("#pause").onclick = async () => { await api("/api/build/pause", {json: {paused: !ov.build_paused}}); render(); };
  $("#newjob").onclick = async () => { const r = await api("/api/jobs", {json: {building_id: null, priority: 5, experiment: false}}); toast(`작업 ${r.job_id} 생성`); render(); };
  view.querySelectorAll("[data-srv]").forEach(b => b.onclick = async () => { await api("/api/server/" + b.dataset.srv, {method: "POST"}); toast("요청했습니다"); });
  view.querySelectorAll("[data-act]").forEach(b => b.onclick = async () => { await api(`/api/jobs/${b.dataset.id}/action`, {json: {action: b.dataset.act}}); render(); });
  view.querySelectorAll("[data-prio]").forEach(i => i.onchange = async () => { await api(`/api/jobs/${i.dataset.prio}/action`, {json: {action: "priority", value: +i.value}}); toast("우선순위 변경"); });
}

// ---------------------------------------------------------------- Camera / Video / Music
async function vCamera() {
  const [d, c] = await Promise.all([api("/api/camera"), getCatalog()]);
  view.innerHTML = `<div class="grid g2">
    <div class="card"><h2>카메라 스타일</h2>${table(Object.entries(c.camera), [["ID", r => esc(r[0])], ["설명", r => esc(r[1])]])}</div>
    <div class="card"><h2>환경 프리셋</h2>${table(Object.entries(c.env_presets), [["ID", r => esc(r[0])], ["설명", r => esc(r[1])]])}
      <h2 style="margin-top:14px">셰이더 팩 (client.game_dir/shaderpacks)</h2>${d.shaderpacks.length ? d.shaderpacks.map(esc).join("<br>") : empty("없음 또는 client.game_dir 미설정")}
      <h2 style="margin-top:14px">리소스팩</h2>${d.resourcepacks.length ? d.resourcepacks.map(esc).join("<br>") : empty("없음")}</div>
  </div>
  <div class="card" style="margin-top:14px"><h2>현재 촬영 설정</h2><pre>${esc(JSON.stringify(d.settings, null, 2))}</pre><div class="muted">수정은 Settings 화면에서 합니다.</div></div>
  <div class="card" style="margin-top:14px"><h2>저장된 카메라 경로</h2>${d.saved_paths.length ? d.saved_paths.map(p => `<a href="${fileUrl(p)}">${esc(p)}</a>`).join("<br>") : empty("아직 촬영 기록이 없습니다")}</div>`;
}
async function vVideo() {
  const [d, c] = await Promise.all([api("/api/video"), getCatalog()]);
  view.innerHTML = `<div class="grid g2"><div class="card"><h2>편집 설정</h2><pre>${esc(JSON.stringify(d.settings, null, 2))}</pre></div>
    <div class="card"><h2>타임랩스 연출</h2>${table(Object.entries(c.timelapse), [["ID", r => esc(r[0])], ["연출", r => esc(r[1])]])}</div></div>
    <div class="card" style="margin-top:14px"><h2>영상</h2>${table(d.videos, [["ID", r => r.id], ["건축물", r => esc(r.name)],
      ["버전", r => ({raw: "원본(편집 안 함)", edited: "편집본 (세로)", edited_b: "편집본 B (실험)", edited_landscape: "편집본 (가로)"})[r.variant] || r.variant],
      ["길이", r => r.duration ? r.duration.toFixed(1) + "초" : "-"], ["생성", r => fmtT(r.created_at)],
      ["미리보기", r => `<video src="${fileUrl(r.path)}" controls preload="none" style="max-width:${["raw", "edited_landscape"].includes(r.variant) ? 320 : 160}px"></video>`]])}</div>`;
}
async function vMusic() {
  const d = await api("/api/music");
  const moods = ["epic", "adventure", "calm", "asian", "upbeat", "chill", "electronic", "mysterious", "dark", "warm", "elegant"];
  const rep = d.fetch_report;
  view.innerHTML = `<div class="card"><h2>자동 수집 (Openverse / Jamendo / Freesound)</h2>
      <div class="row"><button class="primary" id="fetchall">부족한 분위기 전부 채우기</button>
        <select id="mood" style="max-width:180px">${moods.map(m => `<option>${m}</option>`).join("")}</select><button id="fetchone">이 분위기 3곡 더 받기</button></div>
      <p class="muted">CC0, 퍼블릭 도메인, CC BY 만 받습니다 (NC/ND/SA 제외). CC BY 곡은 영상 설명에 출처가 자동으로 들어갑니다.
      Jamendo 는 JAMENDO_CLIENT_ID, Freesound 효과음은 FREESOUND_API_KEY 환경 변수가 있을 때만 씁니다.</p>
      ${rep ? `<div class="muted">마지막 수집 ${fmtT(rep.at)} · 음악 ${esc(JSON.stringify(rep.music))} · 효과음 ${esc(JSON.stringify(rep.sfx))}</div>` : ""}</div>
    <div class="card" style="margin-top:14px"><h2>사용 가능한 음원 ${d.tracks.length}곡</h2>${table(d.tracks, [["제목", r => r.source_url ? `<a href="${esc(r.source_url)}" target="_blank">${esc(r.title)}</a>` : esc(r.title)],
      ["아티스트", r => esc(r.artist)], ["라이선스", r => esc(r.license)], ["분위기", r => esc(r.moods.join(", "))],
      ["BPM", r => r.bpm ?? "-"], ["길이", r => r.duration ? r.duration.toFixed(0) + "초" : "-"],
      ["듣기", r => `<audio controls preload="none" src="${fileUrl(r.path)}" style="height:30px"></audio>`]])}
      <p class="muted">음원 폴더: ${esc(d.music_dir)} (직접 넣는 곡은 같은 이름의 .json 에 license 필요)</p></div>
    <div class="grid g2" style="margin-top:14px"><div class="card"><h2>저작권 위험으로 제외된 파일</h2>${table(d.rejected, [["파일", r => esc(r.file)], ["사유", r => `<span class="warn">${esc(r.reason)}</span>`]])}</div>
      <div class="card"><h2>효과음 (${esc(d.sfx_dir)})</h2><div class="kv">${Object.entries(d.sfx).map(([k, v]) => `<div>${k}</div><div>${v.length ? v.length + "개" : '<span class="muted">없음</span>'}</div>`).join("")}</div></div></div>`;
  $("#fetchall").onclick = async () => { await api("/api/music/fetch", {json: {mood: ""}}); toast("수집을 시작했습니다. 잠시 후 새로고침하세요."); };
  $("#fetchone").onclick = async () => { await api("/api/music/fetch", {json: {mood: $("#mood").value}}); toast("수집을 시작했습니다"); };
}

// ---------------------------------------------------------------- Upload Manager
async function vUpload() {
  const [ups, yt] = await Promise.all([api("/api/uploads"), api("/api/youtube")]);
  view.innerHTML = `<div class="card"><div class="kv"><div>계정 연결</div><div>${yt.connected ? `<span class="ok">연결됨</span> ${esc(yt.channel ? yt.channel.title : "")}` : '<span class="warn">미연결: python -m mcbuildai youtube-auth 실행</span>'}</div>
      <div>자동 업로드</div><div>${yt.auto_upload ? "켜짐" : "꺼짐 (아래에서 직접 업로드)"}</div><div>예약 시각</div><div>${esc((yt.schedule_times || []).join(", "))}</div></div>
      <div class="row" style="margin-top:10px"><button id="refresh">상태 새로고침</button></div></div>
    <div class="card" style="margin-top:14px">${table(ups, [["ID", r => r.id], ["건축물", r => esc(r.name)], ["버전", r => esc(r.variant)],
      ["제목", r => `<input value="${esc(r.title)}" data-f="title" data-id="${r.id}">`],
      ["예약 시각", r => `<input value="${esc(r.publish_at || "")}" data-f="publish_at" data-id="${r.id}" style="width:210px">`],
      ["공개 범위", r => `<select data-f="privacy" data-id="${r.id}">${["private", "unlisted", "public"].map(p => `<option ${p === r.privacy ? "selected" : ""}>${p}</option>`).join("")}</select>`],
      ["상태", r => pill(r.status) + (r.error ? `<div class="err">${esc(r.error.slice(0, 120))}</div>` : "")],
      ["URL", r => r.url ? `<a href="${esc(r.url)}" target="_blank">${esc(r.youtube_id)}</a>` : "-"],
      ["", r => `<button data-save="${r.id}">저장</button> ${r.youtube_id ? "" : `<button class="primary" data-up="${r.id}">업로드</button>`}`]])}</div>`;
  $("#refresh").onclick = async () => { await api("/api/uploads/refresh", {method: "POST"}); toast("확인 중"); setTimeout(render, 3000); };
  view.querySelectorAll("[data-up]").forEach(b => b.onclick = async () => {
    if (!confirm("이 영상을 YouTube 에 업로드할까요?")) return;
    await api(`/api/uploads/${b.dataset.up}/upload`, {method: "POST"}); toast("업로드를 시작했습니다"); setTimeout(render, 3000);
  });
  view.querySelectorAll("[data-save]").forEach(b => b.onclick = async () => {
    const id = b.dataset.save, row = ups.find(u => u.id == id);
    const val = f => view.querySelector(`[data-f="${f}"][data-id="${id}"]`).value;
    await api(`/api/uploads/${id}/edit`, {json: {title: val("title"), description: row.description, publish_at: val("publish_at") || null, privacy: val("privacy")}});
    toast("저장했습니다");
  });
}

// ---------------------------------------------------------------- Schedule
async function vSchedule() {
  const rows = await api("/api/schedule?days=31");
  const byDate = {};
  rows.forEach(r => (byDate[r.date] = byDate[r.date] || []).push(r));
  const start = new Date(); start.setHours(0, 0, 0, 0);
  const days = [...Array(28)].map((_, i) => { const d = new Date(start); d.setDate(d.getDate() + i); return d.toLocaleDateString("sv-SE"); });
  view.innerHTML = `<div class="card row"><button data-days="7">일주일 일정 생성</button><button data-days="30">한 달 일정 생성</button>
    <span class="muted">이미 있는 날짜는 건너뜁니다. 이벤트(명절, 계절)에 맞춘 특별 건축물이 자동 배정됩니다.</span></div>
    <div class="cal" style="margin-top:14px">${days.map(d => `<div class="day"><b>${d}</b>${(byDate[d] || []).map(r => `<div class="item">
      <div><b>${esc(r.plan.name)}</b></div><div class="muted">${esc(r.plan.dna.btype)} / ${esc(r.plan.dna.style)} / ${esc(r.plan.dna.timelapse_style)}</div>
      ${r.plan.event ? `<div class="warn">${esc(r.plan.event)}</div>` : ""}
      ${r.job_id ? pill((r.job_status || "") + " " + (r.job_stage || "")) : `<button data-start="${r.id}">지금 제작</button>`}</div>`).join("")}</div>`).join("")}</div>`;
  view.querySelectorAll("[data-days]").forEach(b => b.onclick = async () => { const r = await api("/api/schedule/generate", {json: {days: +b.dataset.days}}); toast(`${r.created}건 생성`); render(); });
  view.querySelectorAll("[data-start]").forEach(b => b.onclick = async () => { const r = await api(`/api/schedule/${b.dataset.start}/start`, {method: "POST"}); toast(`작업 ${r.job_id} 생성`); render(); });
}

// ---------------------------------------------------------------- History / Analytics
async function vHistory() {
  const rows = await api("/api/history");
  view.innerHTML = `<div class="card">${table(rows, [["ID", r => r.id], ["미리보기", r => r.preview_path ? `<img src="${fileUrl(r.preview_path)}" style="width:90px;border-radius:6px">` : ""],
    ["이름", r => esc(r.name)], ["종류/양식", r => esc(r.btype + " / " + r.style)], ["연출", r => esc(r.timelapse_style + " / " + r.camera_style)],
    ["블록", r => (r.block_count || 0).toLocaleString()], ["상태", r => pill(r.status)], ["생성", r => fmtT(r.created_at)],
    ["파일", r => r.videos.map(v => `<a href="${fileUrl(v.path)}">${esc(v.variant)}</a>`).join(" ") + (r.schematic_path ? ` <a href="${fileUrl(r.schematic_path)}">schem</a>` : "")]])}</div>`;
}
async function vAnalytics() {
  const [d, tr] = await Promise.all([api("/api/analytics"), api("/api/trends")]);
  const s = d.summary, cats = s.by_category || {};
  const names = {btype: "건축물 종류", style: "건축 양식", timelapse_style: "타임랩스 연출", camera_style: "촬영 스타일", length_bucket: "영상 길이", terrain: "지형", variant: "편집 버전"};
  view.innerHTML = `<div class="card row"><button id="collect">지금 성과 수집</button><span class="muted">분석 영상 ${s.videos}개${s.total_views != null ? `, 총 조회수 ${s.total_views.toLocaleString()}` : ""}. 데이터가 적을 때 가중치는 1.0 쪽으로 보정됩니다.</span></div>
    <div class="grid g2" style="margin-top:14px">${Object.keys(names).map(k => `<div class="card"><h2>${names[k]}별 성과</h2>${table(cats[k] || [], [["값", r => esc(r.value)], ["영상 수", r => r.count],
      ["평균 조회수", r => r.avg_views.toLocaleString()], ["평균 시청률", r => r.avg_view_percentage == null ? "-" : r.avg_view_percentage + "%"], ["기획 가중치", r => r.weight]])}</div>`).join("")}</div>
    <div class="card" style="margin-top:14px"><h2>A/B 실험 결과</h2>${table(d.experiments, [["건축물", r => r.building_id], ["A 조회수", r => r.A_views], ["B 조회수", r => r.B_views],
      ["A 시청률", r => r.A_avp ?? "-"], ["B 시청률", r => r.B_avp ?? "-"], ["우세", r => r.winner]])}</div>
    <div class="card" style="margin-top:14px"><h2>영상별 최신 성과</h2>${table(d.latest, [["제목", r => esc(r.title)], ["조회수", r => (r.views || 0).toLocaleString()], ["좋아요", r => r.likes ?? "-"],
      ["평균 시청 시간", r => r.avg_view_duration == null ? "-" : r.avg_view_duration.toFixed(1) + "초"], ["평균 시청률", r => r.avg_view_percentage == null ? "-" : r.avg_view_percentage.toFixed(1) + "%"], ["수집", r => fmtT(r.fetched_at)]])}</div>`;
  view.insertAdjacentHTML("beforeend", `<div class="card" style="margin-top:14px"><h2>YouTube 인기 건축 트렌드 (최근 30일)</h2>
    <div class="row"><button id="trend">지금 분석</button><span class="muted">${tr.updated_at ? "마지막 분석 " + fmtT(tr.updated_at) : "아직 분석 안 함 (YOUTUBE_API_KEY 또는 계정 연결 필요)"}</span></div>
    ${tr.keywords.length ? `<p>${tr.keywords.map(k => `<span class="pill">${esc(k)}</span>`).join(" ")}</p>` : ""}
    <div class="grid g3">${Object.entries(tr.weights).map(([cat, vals]) => `<div><h2>${esc(cat)} 가중치</h2>${table(Object.entries(vals).sort((a, b) => b[1] - a[1]), [["값", r => esc(r[0])], ["가중치", r => r[1]]])}</div>`).join("")}</div>
    <h2 style="margin-top:14px">조회수 상위 영상</h2>${table(tr.videos, [["제목", r => `<a href="https://youtube.com/watch?v=${esc(r.youtube_id)}" target="_blank">${esc(r.title)}</a>`], ["채널", r => esc(r.channel)], ["조회수", r => (r.views || 0).toLocaleString()]])}</div>`);
  $("#collect").onclick = async () => { await api("/api/analytics/collect", {method: "POST"}); toast("수집을 시작했습니다"); };
  $("#trend").onclick = async () => { await api("/api/trends/update", {method: "POST"}); toast("분석을 시작했습니다"); setTimeout(render, 5000); };
}

// ---------------------------------------------------------------- Settings / Logs
async function vSettings() {
  const d = await api("/api/settings");
  const field = (sec, k, v) => {
    const id = `${sec}.${k}`;
    if (typeof v === "boolean") return `<label><input type="checkbox" data-k="${id}" ${v ? "checked" : ""} style="width:auto"> ${esc(k)}</label>`;
    if (typeof v === "object") return `<label>${esc(k)} (JSON)</label><input data-k="${id}" data-json="1" value='${esc(JSON.stringify(v))}'>`;
    return `<label>${esc(k)}</label><input data-k="${id}" data-num="${typeof v === "number" ? 1 : ""}" value="${esc(v)}">`;
  };
  view.innerHTML = `<form id="sf">${Object.entries(d).map(([sec, obj]) => typeof obj === "object" && !Array.isArray(obj) ?
    `<fieldset><legend>${esc(sec)}</legend><div class="grid g3">${Object.entries(obj).map(([k, v]) => `<div>${field(sec, k, v)}</div>`).join("")}</div></fieldset>` : "").join("")}
    <button class="primary">저장</button></form>`;
  $("#sf").onsubmit = async e => {
    e.preventDefault();
    const out = JSON.parse(JSON.stringify(d));
    try {
      view.querySelectorAll("[data-k]").forEach(i => {
        const [sec, k] = i.dataset.k.split(".");
        out[sec][k] = i.type === "checkbox" ? i.checked : i.dataset.json ? JSON.parse(i.value) : i.dataset.num ? Number(i.value) : i.value;
      });
      const r = await api("/api/settings", {method: "PUT", json: out}); toast(r.note);
    } catch (err) { toast("저장 실패: " + err.message, true); }
  };
}
async function vLogs() {
  const level = sessionStorage.getItem("loglevel") || "";
  const rows = await api("/api/logs?limit=400&level=" + level);
  view.innerHTML = `<div class="card row"><select id="lv">${["", "ERROR", "WARN", "INFO"].map(l => `<option ${l === level ? "selected" : ""} value="${l}">${l || "전체"}</option>`).join("")}</select></div>
    <div class="card" style="margin-top:14px">${table(rows, [["시각", r => fmtT(r.ts)], ["수준", r => `<span class="${r.level === "ERROR" ? "err" : r.level === "WARN" ? "warn" : ""}">${r.level}</span>`],
      ["출처", r => esc(r.source)], ["작업", r => r.job_id ?? ""], ["내용", r => `<pre style="margin:0;max-height:120px">${esc(r.message)}</pre>`]])}</div>`;
  $("#lv").onchange = e => { sessionStorage.setItem("loglevel", e.target.value); render(); };
}

// ---------------------------------------------------------------- 라우팅
const VIEWS = {dashboard: [vDashboard, 5000], studio: [vStudio, 0], build: [vBuild, 4000], camera: [vCamera, 0], video: [vVideo, 0],
  music: [vMusic, 0], upload: [vUpload, 0], schedule: [vSchedule, 0], history: [vHistory, 0], analytics: [vAnalytics, 0],
  settings: [vSettings, 0], logs: [vLogs, 10000]};
async function render() {
  const key = location.hash.slice(1) || "dashboard";
  const [fn, every] = VIEWS[key] || VIEWS.dashboard;
  document.querySelectorAll("nav a").forEach(a => a.classList.toggle("on", a.dataset.view === key));
  $("#title").textContent = document.querySelector(`nav a[data-view="${key}"]`).firstChild.textContent;
  clearTimeout(timer);
  try { await fn(); } catch (e) { view.innerHTML = `<div class="card err">불러오기 실패: ${esc(e.message)}</div>`; }
  if (every) timer = setTimeout(render, every);
}
document.querySelectorAll("nav a").forEach(a => a.onclick = () => { location.hash = a.dataset.view; });
window.onhashchange = render;
setInterval(() => $("#clock").textContent = new Date().toLocaleString("ko-KR"), 1000);
render();
