# mcbuildai - Minecraft 건축 타임랩스 자동 제작 시스템

매일 새 건축물을 기획하고, genMC(AI 건축 스튜디오)로 설계하고, Paper 서버에 블록 단위로 지으면서
화면을 녹화한 뒤, 두 가지 영상을 만들어 YouTube 업로드까지 처리합니다.

- `raw.mp4` : 편집하지 않은 원본 버전. 촬영 해상도(16:9) 그대로, 건축 전 장면 + 일정 속도 타임랩스 + 완성 회전 장면을 단순 연결. 음악, 자막, 효과, 크롭 없음.
- `edited.mp4` : 편집 버전 (YouTube Shorts 1080x1920, 58초 이내).
- `edited_landscape.mp4` : 가로 편집 버전 (1920x1080, 90초 이내, `video.landscape_edit`).
- A/B 실험 작업이면 `edited_b.mp4` 도 만듭니다 (전환, 색감, 구성, 썸네일 스타일이 다름).

영상 속 문구, 제목, 설명, 해시태그는 **영어**가 기본입니다 (`content.language: en`). YouTube 에는 한국어 제목/설명도 현지화로 함께 등록합니다 (`content.localize_ko`). 한국어 영상으로 바꾸려면 `content.language: ko`.

### 편집 버전에 들어가는 것
- 시작 훅 문구 ("Building a Gothic Cathedral" + 이름과 블록 수), 끝 댓글 유도 문구 ("Which build should I make next?")
- 건축 중 블록 수 카운터, 화면 아래 진행 바, "240x SPEED" 배속 표시
- 속도 램프(처음/끝 느리게, 중간 빠르게) + 고속 구간 모션 블러
- 비트 싱크: 곡의 BPM 과 박 위치를 분석해 장면 길이를 박자에 맞추므로 모든 전환이 박자 위에서 일어남
- 곡의 클라이맥스 후보 중 하나를 완성 공개 순간에 맞추고, 공개 직전 상승음(riser) + 공개 순간 타격음(impact) + 완성 효과음
- 블록 설치 효과음을 8분음표 박자에 맞춰 배치, 전환마다 whoosh
- 공개 장면 줌, 색보정, 안개, 비네팅, Before & After 분할, 첫 장면으로 이어지는 루프 엔딩
- 썸네일 3가지 스타일(bold, banner, split before/after)을 모두 만들고 영상마다 돌려 가며 사용

## 1. 구성도

```
기획(planner) -> 설계(genMC 또는 내장 생성기) -> 월드 준비(평탄화, 지형)
  -> 건축 + 촬영(RCON 블록 배치, 카메라 리그, FFmpeg 녹화)
  -> 편집(원본/편집본, 음악, 썸네일, 중복 검사) -> 업로드(YouTube) -> 성과 수집 -> 다음 기획에 반영
```

| 영역 | 사용 기술 |
|---|---|
| 서버 | Paper (Minecraft Java 1.21.x), RCON |
| 클라이언트 | Prism Launcher 인스턴스 (Fabric + Sodium + Iris 권장), 관전 모드 촬영 계정 |
| 건축물 설계 | genMC MCP 서버 + Claude API 에이전트, 연결이 없으면 내장 절차적 생성기 |
| 녹화/편집 | FFmpeg (Windows gdigrab, Linux x11grab) |
| 업로드/분석 | YouTube Data API v3, YouTube Analytics API |
| 관리 | FastAPI 대시보드 (http://127.0.0.1:8765), SQLite |

## 2. 설치

저장소 구조: `mcbuildai/` (코드), `platform/windows`, `platform/linux` (OS별 설치/실행 스크립트와 기본 설정), `tests/`.
설치 스크립트는 저장소 최상위에서 실행하도록 되어 있으므로, 쓰는 OS 의 `platform/<os>/` 파일을 저장소 최상위로 복사한 뒤 실행하세요.

Windows: `install.ps1` 실행 (PowerShell: `powershell -ExecutionPolicy Bypass -File install.ps1`)
Linux: `./install.sh` 실행

필요한 프로그램: Python 3.11+, FFmpeg, Java 21, (Linux) xdotool, 한글 폰트.

## 3. 처음 한 번 설정

1. **Paper 서버**: https://papermc.io 에서 Minecraft 버전에 맞는 Paper jar 를 받아 `server/paper.jar` 로 저장합니다. 한 번 실행해 생성되는 `server/eula.txt` 를 읽고 `eula=true` 로 바꿉니다 (Minecraft EULA 동의는 직접 해야 합니다).
2. **config.yaml**
   - `server.rcon_password`: 긴 임의 문자열
   - `client.camera_player`: 촬영용 계정 닉네임 (정품 계정. 서버에서 op 권한 불필요, 자동으로 관전 모드가 됩니다)
   - `client.launch_command`: 예) `prismlauncher -l BuildCam -s 127.0.0.1:25565`
   - `client.game_dir`: Prism 인스턴스의 `.minecraft` 폴더 (셰이더/리소스팩 자동 설정)
   - `capture.*`: Minecraft 창이 보이는 화면 영역. 전체 화면(또는 테두리 없는 창) 권장
   - `genmc.export_version`: 서버 버전과 같게
3. **Claude API 키**: 환경 변수 `ANTHROPIC_API_KEY`. genMC 설계 에이전트, 이름/콘셉트/제목 생성, 이미지 참고 생성에 씁니다. 없으면 규칙 기반 이름과 내장 생성기로 동작합니다.
4. **genMC 연결**: 브라우저로 https://genmc.app/build 를 열어 둡니다. 대시보드 AI Studio 에서 스튜디오에 표시된 페어링 코드를 입력합니다 (또는 `python -m mcbuildai genmc pair <코드>`). 이후에는 저장된 재연결 키로 같은 탭에 자동 연결됩니다. 이 탭은 계속 열려 있어야 합니다.
5. **YouTube**: Google Cloud 콘솔에서 YouTube Data API v3 와 YouTube Analytics API 를 켜고, OAuth 클라이언트(데스크톱 앱) JSON 을 `data/client_secret.json` 에 둔 뒤 `youtube_auth` 스크립트를 실행합니다. 맞춤 썸네일은 채널 인증(전화번호)이 되어 있어야 적용됩니다.
6. **음악/효과음 자동 수집**: 음원이 부족하면 자동으로 받아 옵니다 (매주 보충, 대시보드 Music Studio 버튼, `python -m mcbuildai music fetch`).
   - Openverse: 키 없이 사용. Jamendo/Freesound/Wikimedia 의 CC 음원 검색.
   - Jamendo: `JAMENDO_CLIENT_ID` 환경 변수가 있으면 추가 사용 (devportal.jamendo.com 에서 무료 발급). 다운로드 허용 곡만.
   - Freesound: `FREESOUND_API_KEY` 가 있으면 효과음 검색 (CC0 만).
   - CC0, 퍼블릭 도메인, CC BY 만 받고 NC/ND/SA 는 받지 않습니다. 받은 파일마다 출처 .json 이 생기고, 영상 설명에 출처가 자동으로 들어갑니다.
   - CC 음원도 원작자가 Content ID 에 등록해 둔 경우가 드물게 있습니다. 문제가 생긴 곡은 파일을 지우면 다시 쓰지 않습니다.
7. **트렌드 분석** (선택): `YOUTUBE_API_KEY` 환경 변수(또는 연결된 계정)가 있으면 하루 한 번 최근 30일 인기 마인크래프트 건축 영상 제목을 분석해 종류/양식/지형 가중치와 키워드를 기획에 반영합니다 (`python -m mcbuildai trends`).
8. **음악/효과음 직접 넣기** (저작권 위험 필터링)
   - `assets/music/곡.mp3` 와 함께 `assets/music/곡.mp3.json` 을 둡니다:
     `{"title": "...", "artist": "...", "license": "CC0", "source_url": "...", "moods": ["epic", "calm"]}`
   - `license` 가 `audio.allowed_licenses` 에 없거나 json 이 없으면 사용하지 않습니다.
   - CC-BY 곡은 설명란에 출처가 자동으로 들어갑니다.
   - 효과음: `assets/sfx/place_*` (블록 설치), `whoosh_*` (전환), `riser_*` (공개 직전), `impact_*` (공개 순간), `complete_*` (완성). 직접 만든 소리나 사용 허가된 소리만 넣으세요.
   - AI 음악 생성 API 를 쓰려면 `music_api.endpoint` 와 `music_api.license` 를 설정합니다.

## 4. 실행

| 명령 | 설명 |
|---|---|
| `python -m mcbuildai run` | 스케줄러 + 작업자 + 대시보드 전체 (start_all) |
| `python -m mcbuildai dashboard` | 대시보드만 |
| `python -m mcbuildai plan --days 7` | 일주일 콘텐츠 일정 생성 (`--days 30` 한 달) |
| `python -m mcbuildai generate --prompt "..." [--image 사진.png]` | 텍스트/이미지로 건축물 생성 |
| `python -m mcbuildai generate --variant-of 3` | 기존 건축물 변형 |
| `python -m mcbuildai generate --engine procedural` | genMC 없이 내장 생성기 |
| `python -m mcbuildai job --building 3 [--experiment]` | 제작 작업 생성 |
| `python -m mcbuildai worker --roles capture` / `--roles edit,upload` | 작업자를 역할별로 나눠 실행 |
| `python -m mcbuildai server start/backup/configure --new-world` | 서버 관리 |
| `python -m mcbuildai music` | 음원 라이선스 검사 |
| `python -m mcbuildai music fetch [--mood epic]` | 음악/효과음 자동 수집 |
| `python -m mcbuildai trends` | YouTube 인기 건축 트렌드 분석 |
| `python -m mcbuildai analytics` | 성과 수집과 분석 출력 |

매일 `jobs.daily_time` 에 그날 일정의 작업이 만들어집니다. 업로드는 기본적으로 대시보드 Upload Manager 에서 확인 후 버튼으로 올립니다. 완전 자동으로 올리려면 `youtube.auto_upload: true` 로 바꿉니다 (기본 공개 범위 private + 예약 시각).

## 5. 결과물 위치

`output/buildings/<번호>_<이름>/`
- `building.schem` (WorldEdit, Sponge v3/v2), `building.mcfunction`, `model.json`, `materials.json`, `preview.png`
- `capture/` 원본 촬영 조각과 `camera_keyframes.json` (촬영 위치 기록)
- `raw.mp4`, `edited.mp4`, `edited_b.mp4`, `edited_landscape.mp4`, `edited_preview.mp4`
- `thumb.jpg`(업로드용), `thumb_bold.jpg`, `thumb_banner.jpg`, `thumb_split.jpg`, `thumb_vertical.jpg`

## 6. 동작 방식 메모

- **카메라**: 촬영 계정을 관전 모드로 바꾸고 `item_display` 엔티티(카메라 리그)를 관전하게 한 뒤, 서버가 틱마다 리그를 옮깁니다. `teleport_duration` 보간 덕분에 클라이언트에서 부드럽게 움직입니다. 문제가 있으면 `camera.mode: tp` 로 바꾸세요.
- **HUD 숨김**: 접속 후 F1 키를 자동으로 보냅니다 (Windows PowerShell SendKeys, Linux xdotool).
- **중단 후 재개**: 각 단계와 건축 진행 블록 번호가 저장됩니다. 프로그램을 다시 켜면 이어서 진행합니다. 건축이 중간에 끊긴 경우 새 녹화 조각을 만들어 편집 시 이어 붙입니다.
- **충돌 감지**: RCON 응답이 없으면 서버 프로세스를 정리하고 다시 띄운 뒤 재시도합니다.
- **블록 검증**: 건축 후 `execute if block` 으로 일부(기본 25%)를 확인하고 다른 블록은 다시 놓습니다.
- **성과 반영**: 6시간마다 조회수/평균 시청률을 모아 종류, 양식, 연출, 카메라, 길이, 실험 버전별 가중치를 계산하고 다음 기획의 선택 확률에 반영합니다. 영상 수가 적을 때는 가중치를 1.0 쪽으로 보정합니다.

## 7. 알려진 제약

- genMC 는 브라우저 탭에서 동작하는 서비스라 그 탭이 열려 있어야 합니다. 내보내기는 `build_export` 의 mcfunction 텍스트(최대 65,536 명령)를 사용하고, 실패하면 schem 을 시도합니다. genMC 의 도구 응답 형식이 바뀌면 `mcbuildai/generator/genmc.py` 를 조정해야 할 수 있습니다.
- Linux 는 X11 세션이 필요합니다 (Wayland 는 x11grab 녹화 불가).
- 녹화는 실제 시간으로 진행되므로 건축 시간 범위(`build.min_build_sec`~`max_build_sec`)만큼 걸립니다.
- 설날/추석 날짜는 `planner.lunar_dates` 에 양력으로 들어 있습니다. 실제 달력과 확인하세요.
