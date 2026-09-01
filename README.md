# docs viewer

로컬 문서 + Google Drive 문서를 브라우저에서 바로 보는 **의존성 없는 단일 파일 뷰어**.
Python 3.7+ 표준 라이브러리만 사용합니다 (pip install 없음).

**받기 → [최신 릴리스](https://github.com/kimgxxn/docs-viewer/releases/latest) 의 `docs-viewer-*.zip`**
— 압축을 풀면 바로 실행됩니다. 처음이라면 아래 [빠른 시작](#빠른-시작-처음-받은-분) 을 보세요.

```bash
./dv                                                    # 런처 (기본 폴더 + Drive + 토큰 없이)
python3 docs_viewer.py ~/Documents ~/workspace/notes    # 폴더 여러 개 등록
python3 docs_viewer.py --no-token --drive .             # 현재 폴더 + Drive 마운트
```

> 이름 규칙: 디렉터리는 kebab-case(`docs-viewer`), 파이썬 모듈은 snake_case
> (`docs_viewer.py`). 하이픈이 든 파일명은 `import` 가 불가하므로 PEP 8 은 모듈에
> 언더스코어를 요구하고, 레포/디렉터리는 URL·CLI 관례상 하이픈을 씁니다.

터미널을 붙잡지 않고 띄우려면 시작/종료 스크립트를 씁니다.

```bash
./dv-start      # 백그라운드 기동 → URL 출력 + 브라우저 열기 (-n 이면 안 엶)
./dv-stop       # 종료
./dv-restart    # 껐다 켜기
```

`127.0.0.1` 에만 바인딩되고 브라우저가 자동으로 열립니다.

```
http://127.0.0.1:8765/          (--no-token 일 때)
http://127.0.0.1:8765/?t=<token>  (기본값: 실행마다 랜덤 토큰)
```

혼자 쓰는 머신이면 `--no-token` 을 쓰거나 `config.json` 에
`"no_token": true` 를 넣어두면 됩니다. 토큰이 필요한 경우는 아래 [토큰이 필요한
경우](#토큰이-필요한-경우) 참고.

## 빠른 시작 (처음 받은 분)

**준비물**

| | |
|---|---|
| 필수 | Python 3.7 이상 — macOS·리눅스에는 이미 깔려 있습니다 (`python3 -V` 로 확인) |
| 선택 | LibreOffice — docx·xlsx·pptx 미리보기용 (`brew install --cask libreoffice`) |

`pip install` 은 없습니다. 표준 라이브러리만 씁니다.

**1) 내려받기**

[**최신 릴리스**](https://github.com/kimgxxn/docs-viewer/releases/latest) 페이지에서 `docs-viewer-<버전>.zip` 을 받습니다.
터미널에서 받으려면:

```bash
# gh CLI 가 있으면 (버전을 몰라도 최신 것을 받습니다)
gh release download --repo kimgxxn/docs-viewer --pattern '*.zip'

# 없으면 최신 릴리스의 zip 주소를 찾아서 받기
curl -sL https://api.github.com/repos/kimgxxn/docs-viewer/releases/latest \
  | grep -o 'https://[^"]*\.zip' | head -1 | xargs curl -LO
```

> `git clone https://github.com/kimgxxn/docs-viewer.git` 로 받아도 동작은 같습니다. 다만 **zip 에는
> `assets/mermaid.min.js`(3.4MB) 가 동봉**되어 있어 네트워크 없이도 다이어그램이
> 바로 그려지고, clone 은 처음 다이어그램을 볼 때 CDN 에서 한 번 내려받습니다.
> 사내망이 CDN 을 막는다면 zip 쪽이 확실합니다.

**2) 압축을 풀고 폴더로 이동**

```bash
unzip docs-viewer-*.zip && cd docs-viewer
chmod +x dv dv-start dv-stop dv-restart   # 실행 권한이 지워졌을 때만
xattr -dr com.apple.quarantine .          # macOS 가 "확인되지 않은 개발자" 라고 막을 때만
```

**3) 볼 폴더 정하기** — 둘 중 편한 쪽

```bash
# (a) 그때그때 인자로 넘기기
python3 docs_viewer.py --no-token ~/Documents ~/workspace

# (b) 매번 같은 폴더를 본다면 설정 파일에 적어두기
cp config.json.example config.json
```

`config.json` 은 이렇게 생겼습니다. 견본 파일을 못 찾겠으면 아래를 터미널에 그대로
붙여넣어 만들어도 됩니다 (**`roots` 만 내 폴더 경로로 고치면 끝**).

```bash
cat > config.json <<'EOF'
{
  "roots": ["~/Documents", "~/workspace"],
  "port": 8765,
  "no_token": true,
  "allow_edit": false,
  "drive": false,
  "show_hidden": false,
  "no_mermaid": false
}
EOF
```

| 키 | 뜻 |
|---|---|
| `roots` | 사이드바에 띄울 폴더들. `~` 를 써도 되고, 없는 경로는 조용히 건너뜁니다 |
| `port` | 기본 8765. 사용 중이면 다음 빈 포트를 자동으로 잡습니다 |
| `no_token` | `true` 면 `?t=토큰` 없이 접속 (127.0.0.1 전용일 때만 가능) |
| `allow_edit` | `true` 여야 분할 편집기에서 저장이 됩니다 (기본 읽기 전용) |
| `drive` | `true` 면 Google Drive for Desktop 마운트를 폴더로 자동 추가 |
| `show_hidden` | 숨김 파일 표시 |
| `no_mermaid` | `true` 면 mermaid 다이어그램을 그리지 않음 (다운로드도 안 함) |

**4) 띄우고 끄기**

```bash
./dv-start     # 백그라운드 기동 → 브라우저가 자동으로 열립니다
./dv-stop      # 종료
./dv-restart   # config.json 을 고쳤거나 LibreOffice 를 새로 깔았을 때
```

브라우저에 `http://127.0.0.1:8765/` 가 뜨면 성공입니다. 이 주소는 **내 컴퓨터에서만**
열리고 (외부 노출 없음), 기본은 **읽기 전용**입니다.

**잘 안 될 때**

| 증상 | 원인 · 해결 |
|---|---|
| `표시할 폴더가 없습니다` | `config.json` 의 `roots` 에 적은 경로가 실제로 없습니다 |
| 포트가 이미 사용 중 | 자동으로 다음 빈 포트를 잡습니다. 고정하려면 `./dv-start -p 9000` |
| docx·xlsx 가 안 열림 | 시작 배너의 `office :` 줄이 `soffice 없음` 이면 LibreOffice 설치 후 `./dv-restart` |
| macOS 가 폴더 접근을 물어봄 | 터미널(iTerm 등)에 문서·다운로드 폴더 접근을 허용해야 그 폴더를 읽습니다 |
| 다이어그램이 코드 블록으로만 보임 | mermaid 스크립트가 없는 경우. 네트워크가 되면 처음 한 번 자동으로 받아옵니다 |
| 편집기가 읽기 전용 | 기본값입니다. `config.json` 에 `"allow_edit": true` 를 넣고 `./dv-restart` |

## 파일 구성

설정·캐시·토큰까지 모두 이 폴더 안에서 관리됩니다 (홈 디렉터리를 더럽히지 않음).

```
docs-viewer/
  docs_viewer.py      뷰어 본체 (단일 파일)
  dv                  런처 (포그라운드)
  dv-start            백그라운드 기동 (멱등)
  dv-stop             종료
  dv-restart          껐다 켜기
  config.json.example 설정 견본 (config.json 으로 복사해서 수정)
  config.json         기본 폴더·포트·옵션 (직접 만듦, .gitignore 됨)
  fonts.conf          office 변환용 폰트 대체 규칙 (한글 깨짐 방지)
  run.pid             실행 중인 PID (자동 생성, .gitignore 됨)
  run.log             실행 로그 (자동 생성, .gitignore 됨)
  gdrive_client.json  Google OAuth 클라이언트 (선택, .gitignore 됨)
  gdrive_token.json   Drive refresh token (자동 생성, 0600, .gitignore 됨)
  cache/              office 변환 캐시 + mermaid 스크립트 (자동 생성)
  assets/             mermaid.min.js 를 직접 넣어두고 싶을 때 (선택)
  mermaid-test.md     동작 확인용 예제 문서 (열어서 다이어그램이 그려지면 정상)
  make-dist.sh        배포용 zip 생성 (dist/docs-viewer-<버전>.zip)
  README.md
  LICENSE             MIT
  THIRD-PARTY-NOTICES.md  동봉한 mermaid 스크립트의 라이선스 고지
```

`DOCS_VIEWER_HOME=/다른/경로` 환경변수로 상태 폴더만 옮길 수도 있습니다.

## 지원 포맷

| 종류 | 처리 방식 |
|---|---|
| `.md` `.markdown` `.mdx` | 서버에서 HTML 로 렌더 (제목·목록·표·코드펜스·체크박스·인용·GitHub 알림 블록·링크정의·4칸 코드블록) + 목차 자동 생성 + **코드펜스 문법 하이라이팅** + ```` ```mermaid ```` **다이어그램** |
| `.html` `.htm` | 스크립트 차단 iframe 에서 렌더 / **분할 편집**(소스+실시간 미리보기) / 필요 시 "스크립트 허용" 토글 |
| 텍스트·코드 (60여 종) | 줄번호 뷰 + **문법 하이라이팅**, 줄바꿈 토글 |
| `.csv` `.tsv` | 표로 렌더 |
| 이미지 · PDF | 인라인 표시 |
| `.docx` `.xlsx` 등 | LibreOffice(`soffice`) 가 PATH 에 있을 때만 HTML 로 변환해 표시. 시트가 여러 장이면 **오른쪽 목차에 시트 목록** |
| `.pptx` `.ppt` `.odp` | 같은 조건에서 **PDF** 로 변환해 표시 (장표당 1페이지) |
| `.gdoc` `.gsheet` `.gslides` | Drive for Desktop 바로가기 파일 → 문서 카드 + "Drive에서 열기". OAuth 연결 시 내용까지 인라인 렌더 |
| Google Drive | ① 로컬 마운트(`--drive`, 설정 0) ② OAuth API. 구글 문서는 Markdown, 스프레드시트는 CSV, 프레젠테이션은 PDF 로 export 해서 표시 |

## 분할 편집 (VS Code 미리보기 스타일)

md · html · csv · 텍스트/코드 문서에서 툴바의 **[분할 편집]** 을 누르면 왼쪽 소스 /
오른쪽 미리보기로 갈라지고, 타이핑하면 **350ms 뒤 미리보기가 자동 갱신**됩니다.

- md·csv·코드 → 서버가 렌더/하이라이팅해서 즉시 반영
- html → 편집 중인 내용을 서버 메모리에 잠깐 올려 iframe 으로 렌더합니다.
  `<base href="/f/<root>/<원본폴더>/">` 를 주입하므로 **상대경로 이미지·CSS 가 그대로 보이고**,
  스크립트는 `Content-Security-Policy: sandbox` 로 차단됩니다 (10분 뒤 자동 만료)
- 왼쪽 소스에는 **줄번호 거터**가 붙고 커서 위치(줄:칸)가 표시됩니다
- **소스 ↔ 미리보기 스크롤 동기화** (미리보기 헤더의 [⇅ 동기화] 로 토글, 기본 켜짐)
  - md·코드·csv → 렌더 결과의 블록마다 원본 줄번호(`data-line`)를 심어 **줄 단위로 정확히**
    맞추고, 블록 사이는 비례 보간합니다
  - html → 줄 매핑이 불가능하므로 **비율 동기화**. 미리보기 iframe 은 CSP
    `sandbox allow-same-origin` + `script-src 'none'` 으로 격리한 채 부모가 스크롤만 제어합니다
  - 방향별 락(120ms)으로 되울림(echo)만 막아 스크롤이 끊기지 않습니다
- 편집 모드에서는 본문 여백이 줄고 폭 제한(960px)이 풀려 화면을 최대로 씁니다
  (트리는 경계 드래그로, 목차는 `t` 로 접을 수 있습니다)
- 가운데 경계를 드래그해 좌우 비율 조절 (더블클릭 = 50%, 기억됨)
- `⌘S` / [저장] 으로 파일에 기록 — **`--allow-edit` 를 준 경우에만** 활성화되고,
  없으면 편집기는 읽기 전용으로 뜹니다
- 편집 중에는 파일 변경 자동 감지가 멈춰 입력 내용을 덮어쓰지 않습니다.
  저장하지 않고 나가려 하면 경고합니다
- URL 뒤에 `?edit=1` 을 붙이면 바로 편집 모드로 열립니다
  (`#fs/r0/path/to/doc.html?edit=1`)

### 저장의 안전장치

| 항목 | 처리 |
|---|---|
| 기본값 | **읽기 전용** — `--allow-edit` 또는 `config.json` 의 `"allow_edit": true` 필요 |
| 대상 | md·html·csv·텍스트/코드만. pdf·office·이미지 등은 거부 |
| 경로 | 등록된 root 하위인지 `realpath` 로 재검증 |
| 쓰기 방식 | 임시파일 + `fsync` + `os.replace` 원자적 교체, 권한/소유 보존 |
| 인코딩 | 원본 인코딩 유지 (utf-8 / utf-8-sig / cp949 / euc-kr 자동 판별) |
| CSRF | `X-Docs-Viewer` 커스텀 헤더 필수(교차출처는 preflight 로 차단) + `Sec-Fetch-Site` 검사 |
| 크기 | 4MB 초과 거부 |

버전 관리는 없습니다 — 되돌리려면 git 등 외부 도구가 필요합니다.

## 문법 하이라이팅

의존성 없이 쓰기 위해 하이라이터도 직접 넣었습니다 (highlight.js 등 불필요, 완전 오프라인).
언어별 규칙을 하나의 마스터 정규식으로 합쳐 서버에서 토큰화하고, **줄 단위로 완결된
`<span>`** 으로 내보내므로 줄번호 뷰와 그대로 맞물립니다.

지원: java · kotlin · javascript/typescript(jsx/tsx) · python · go · rust · swift · c/c++ ·
c# · php · ruby · dart · scala · groovy/gradle · lua · sql · shell(sh/bash/zsh) ·
html/xml/vue · css/scss/less · json · yaml · ini/properties/toml/conf · diff/patch

- 멀티라인 문자열·주석(python `"""`, js 템플릿 리터럴, `/* */`, `<!-- -->`)이 줄을 넘어가도
  색이 이어집니다
- 알 수 없는 언어이거나 1MB / 20,000줄을 넘으면 하이라이팅을 건너뛰고 평문으로 표시
- 색은 CSS 변수(`--hl-*`)라 라이트/다크 각각 조정 가능

## 주요 기능

- **여러 폴더를 그룹으로 동시에 표시** — 등록한 루트가 사이드바에 나란히 놓이고 각각
  독립적으로 펼쳐집니다 (선택 드롭다운 없음). 문서를 열면 그 문서의 조상 폴더가 자동으로
  펼쳐지고 선택 표시됩니다
- 트리 지연 로딩 + 펼친 상태 기억, 이름 필터(로드된 노드 대상)
- 필터 칸 옆 **↻ 버튼으로 트리만 새로고침** — 외부에서 파일을 추가·삭제했을 때 씁니다.
  펼침 상태·스크롤 위치를 유지하고 열려 있는 문서는 건드리지 않습니다
  (헤더의 ↻ / `r` 은 문서까지 통째로 다시 읽습니다)
- 검색은 기본이 **전체 루트 동시 검색**이며, 결과를 루트별로 라운드로빈 수집해 한 폴더가
  결과 상한(200건)을 독식하지 않습니다. 헤더의 [전체]/[현재] 버튼으로 범위를 바꿉니다
- 전체 검색: 파일명 + 내용 (매칭된 줄 미리보기 → 클릭하면 해당 줄로 이동/하이라이트)
- 마크다운 문서 간 상대 링크 이동 (`[문서](sub/other.md)` 클릭 시 뷰어 안에서 열림)
- 우측 목차 + 스크롤 위치 추적
- 북마크 / 최근 본 문서 (브라우저 localStorage)
- **파일 변경 자동 감지** — 편집기에서 저장하면 2.5초 안에 스크롤 위치를 유지한 채 갱신
- **Mermaid 다이어그램** — ```` ```mermaid ```` 코드펜스를 그림으로 렌더합니다 (flowchart·sequence·class·state·ER·gantt·pie 등). 테마(라이트/다크)를 따라가고,
  문법이 틀리면 오류 메시지와 함께 원문을 코드 블록으로 남깁니다
- **본문 폭 3단 토글** — 헤더의 ↔ 또는 `w` 로 기본 960px → 넓게 1440px → 창 전체.
  기본 960px 은 글이 읽기 좋은 한 줄 길이고, 넓은 표·다이어그램은 늘려서 봅니다
  (선택은 localStorage 에 기억. PDF·office·CSV 는 원래부터 폭 제한 없음)
- 라이트/다크/시스템 테마, 인쇄 스타일
- 트리 항목에 마우스를 올리면 **전체 이름·크기·경로** 툴팁이 뜹니다
- 사이드바 경계를 드래그해 **너비 조절** (더블클릭 = 기본값 290px, localStorage 기억)
- 단축키: `/` 검색 · `b` 사이드바 · `t` 목차 · `w` 본문 폭 · `r` 새로고침 · `s` 북마크 · `esc` 닫기
- 루트 표시 이름은 **지정한 경로의 마지막 조각** 기준입니다 (심볼릭 링크를 걸어둔 경우
  대상 폴더 이름이 아니라 적어준 이름으로 보입니다)

## 옵션

```
docs_viewer.py [폴더...] [-p PORT] [--no-token] [--drive] [--lan] [--host ADDR]
               [-n] [--hidden] [--md-unsafe] [--no-mermaid] [-v]

  -p, --port      포트 (기본 8765, 사용 중이면 다음 빈 포트를 자동 선택)
      --no-token  토큰 없이 접속 허용 (루프백 전용일 때만 가능)
      --drive     Google Drive for Desktop 마운트를 폴더로 자동 추가
      --allow-edit 분할 편집기에서 파일 저장 허용 (기본: 읽기 전용)
      --lan       같은 네트워크의 다른 기기에서도 접속 허용 (= --host 0.0.0.0)
                  이때는 토큰이 강제됩니다
      --host      바인딩 주소 (기본 127.0.0.1)
  -n, --no-browser  브라우저 자동 실행 안 함
      --hidden    숨김 파일도 표시
      --md-unsafe 마크다운 안의 raw HTML 을 살균 없이 렌더 (신뢰하는 문서에만)
      --no-mermaid  mermaid 코드펜스를 다이어그램으로 그리지 않음 (다운로드도 안 함)
  -v, --verbose   요청 로그 출력
```

`config.json`(스크립트와 같은 폴더)으로 기본값을 둘 수도 있습니다.

```json
{
  "roots": ["~/Documents", "~/workspace"],
  "port": 8765,
  "no_token": true,
  "drive": true,
  "drive_tab": false,
  "allow_edit": true,
  "show_hidden": false,
  "no_mermaid": false
}
```

## Google Drive 붙이는 두 가지 방법

### ① 로컬 마운트 (권장, 설정 0)

Google Drive for Desktop 이 깔려 있으면 마운트 경로를 폴더로 등록하면 끝입니다.

```bash
python3 docs_viewer.py --drive          # 마운트 자동 탐색해서 폴더로 추가
```

- 탐색 경로: `~/Library/CloudStorage/GoogleDrive-*`(macOS), `~/Google Drive`,
  `/Volumes/GoogleDrive`
- md·pdf·html·docx·이미지 등 **실제 파일은 전부 그대로** 열립니다
- **내용 검색은 "이미 로컬에 내려온 파일"만** 합니다. Drive for Desktop 스트리밍 모드는
  아직 안 받은 파일을 `논리 크기 > 0, 할당 블록 = 0` 인 placeholder 로 노출하는데, 이걸
  읽으면 그 순간 다운로드가 걸립니다. 뷰어는 `st_blocks` 로 판별해 placeholder 만
  건너뛰고 (검색 결과에 "미다운로드 N개는 파일명만 검색" 표시), 나머지는 정상 검색합니다
- 따라서 **미러링 모드**거나 폴더를 **"오프라인 액세스 사용 가능"** 으로 고정해두면 그
  폴더는 옵션 변경 없이 자동으로 내용 검색 대상이 됩니다
- 트리에서 클라우드 전용 파일은 ☁ 로 표시되며, 열면 그때 다운로드됩니다 (의도된 동작)
- `.gdoc`/`.gsheet`/`.gslides` 는 실제 문서가 아니라 `doc_id` 만 담긴 JSON 바로가기입니다.
  뷰어는 이걸 알아보고 문서 카드 + "Drive에서 열기" 를 보여줍니다

상단 **Drive 탭은 OAuth 전용** 이라, `gdrive_client.json` 이 없으면 자동으로 숨습니다
(마운트 기반으로만 쓸 때 UI 가 깔끔해집니다). `config.json` 에 `"drive_tab": true/false` 로
강제할 수도 있습니다.

### ② OAuth API 연결 (Google 네이티브 문서까지 보려면)

1. [Google Cloud Console](https://console.cloud.google.com/) → 프로젝트 생성
2. **API 및 서비스 → 라이브러리 → Google Drive API 사용 설정**
3. **사용자 인증 정보 → OAuth 클라이언트 ID → 애플리케이션 유형: 데스크톱 앱**
4. JSON 다운로드 → `gdrive_client.json (docs-viewer 폴더 안)` 으로 저장
5. 뷰어 재시작 → 상단 **Drive** 탭 → **Drive 연결** → 구글 로그인 승인

데스크톱 앱 클라이언트는 루프백(`http://127.0.0.1:<임의포트>/oauth/callback`) 리다이렉트를
자동 허용하므로 포트를 바꿔도 됩니다. 권한은 **읽기 전용**(`drive.readonly`) 이며,
refresh token 은 `gdrive_token.json` (0600) 에만 저장됩니다.

연결하면 추가로 되는 것: `.gdoc`/`.gsheet`/`.gslides` **인라인 렌더**, Drive 탭에서
폴더 탐색, Drive 전체 텍스트 검색, 동기화하지 않은 공유 문서 접근.

### 어느 쪽을 쓸까

| | 로컬 마운트 | OAuth API |
|---|---|---|
| 설정 | 없음 | Cloud Console 5분 |
| 일반 파일(md/pdf/docx…) | ✅ | ✅ |
| Google 문서·시트·슬라이드 | 링크만 | ✅ 인라인 |
| 내용 검색 | 내려온 파일만 ✅ (미러링/오프라인고정 시 전체) | ✅ 전체 |
| 동기화 안 한 문서 | ❌ | ✅ |
| 오프라인 | ✅ (내려온 파일) | ❌ |

> "동기화 중" ≠ "내용이 로컬에 있음". 스트리밍 모드의 동기화는 메타데이터·변경 추적이고,
> 파일 내용은 열 때 받아옵니다. `du -sh` 결과가 논리 크기보다 훨씬 작으면 스트리밍입니다.

**로컬 마운트를 기본으로 쓰고, Google 네이티브 문서를 자주 본다면 OAuth 를 추가**하는 조합이
가장 편합니다. 두 방법은 함께 켜둘 수 있고, 그러면 마운트의 `.gsheet` 를 클릭했을 때
API 로 내용을 받아 바로 렌더합니다.

## 보안 설계

이 뷰어는 로컬 파일시스템을 HTTP 로 노출하므로 다음 방어를 넣었습니다.

- **127.0.0.1 전용 바인딩** — 기본값. 다른 기기에서는 소켓 자체에 닿지 못합니다
- **Host 헤더 검증** — DNS rebinding 공격 차단. 허용되는 Host 는 `127.0.0.1`, `localhost`,
  `::1`, 이 머신의 호스트명이며, `--lan` 일 때만 이 머신에 실제로 존재하는 IP 를
  추가 허용합니다 (bind 가능 여부로 확인). 그 외 이름은 421 로 거부
- **토큰(선택)** — 기본값은 실행마다 랜덤 토큰이며 쿠키(HttpOnly, SameSite=Lax) 또는
  `?t=` 로 전달합니다. `--no-token` 으로 끌 수 있고, `--lan` 에서는 끌 수 없습니다
- **경로 탈출 차단** — 모든 요청 경로를 `realpath` 로 정규화한 뒤 등록된 root 하위인지 검증
  (심볼릭 링크로 root 밖을 가리키는 경우도 거부)
- **문서 내 HTML 살균** — 마크다운 안의 raw HTML 은 태그/속성 allowlist 통과분만 렌더
  (`<script>`, `on*` 핸들러, `javascript:` URL 제거)
- **`.html` / `.svg` 는 격리 렌더** — `Content-Security-Policy: sandbox` + iframe sandbox 로
  스크립트 차단. "스크립트 허용" 을 켜면 `allow-scripts` 만 주고 same-origin 은 주지 않으므로
  문서가 뷰어 API/쿠키에 접근할 수 없습니다
- 쓰기는 **`--allow-edit` 를 켤 때만** 열립니다 (기본 읽기 전용). 켜도 저장 대상은
  텍스트 계열 문서로 제한되고, 삭제·이동·업로드 엔드포인트는 아예 없습니다

### 토큰이 필요한 경우

루프백 전용으로 띄우면 토큰 없이도 다음이 성립합니다.

- 다른 기기 → **소켓에 도달 불가**
- 브라우저로 방문한 악성 웹페이지 → `127.0.0.1` 로 요청은 보낼 수 있지만 CORS 헤더를
  주지 않으므로 **응답 본문을 읽지 못함**
- 그 우회로인 DNS rebinding → **Host 검증에서 차단**

그래서 **1인 사용 머신이면 `--no-token` 이 실질적인 위험 증가 없이 편합니다.**
토큰이 의미 있는 상황은 이 세 가지입니다.

| 상황 | 이유 |
|---|---|
| 한 맥에 다른 사용자 계정이 로그인함 | 같은 머신의 다른 계정도 `127.0.0.1:8765` 에 접속 가능 |
| `--lan` 으로 네트워크에 노출 | 같은 네트워크 누구나 접근 가능 → 토큰 강제 |
| 신뢰 못 할 로컬 프로세스가 도는 환경 | 브라우저 없이도 API 를 그대로 호출 가능 |

외부에서 접근해야 하면 `--lan` 보다 **Tailscale** 이나 **Cloudflare Tunnel** 이 안전합니다
(평문 HTTP + LAN 노출을 피할 수 있음).

## 원격(SaaS)으로 못 올리는 이유

브라우저 샌드박스 때문에 원격 서버의 JS 는 사용자 디스크를 읽을 수 없습니다. 선택지는
① 로컬 서버(이 방식) ② File System Access API(Chrome/Edge 전용, 사용자가 고른 폴더만)
③ SaaS UI + 로컬 브리지 데몬(결국 로컬 설치 필요) ④ 서버로 싱크(사본이므로 로컬 문서 아님)
뿐입니다. Google Drive 만 서버 OAuth 로 접근할 수 있어 이 뷰어도 그 조합을 씁니다.

## 시작·종료 스크립트

| 스크립트 | 하는 일 |
|---|---|
| `./dv` | **포그라운드** 실행. 로그가 눈앞에 흐르고 `Ctrl+C` 로 종료 |
| `./dv-start` | **백그라운드** 기동. 이미 떠 있으면 URL 만 알려주고 아무것도 하지 않음(멱등) |
| `./dv-stop` | 종료. `SIGTERM` 먼저, 10초 안 죽으면 `SIGKILL` |
| `./dv-restart` | `dv-stop` + `dv-start` |

- 추가 인자는 그대로 `docs_viewer.py` 로 넘어갑니다 — `./dv-start --lan`, `./dv-start -p 9000`
- `-n` 을 주면 브라우저를 열지 않습니다
- PID 는 `run.pid`, 로그는 `run.log` 에 쌓입니다 (실행마다 `===== 시작: ... =====` 구분선)
- **`dv-stop` 은 PID 파일이 없어도 끕니다** — `./dv` 나 `python3 docs_viewer.py` 로 직접
  띄운 프로세스도 찾아냅니다. 이 폴더에서 띄운 것만 대상으로 하므로 다른 경로의
  인스턴스는 건드리지 않습니다
- PID 재사용에 속지 않도록 `run.pid` 의 번호가 실제로 `docs_viewer.py` 인지 명령줄까지
  확인하고, 죽은 프로세스가 남긴 PID 파일은 `dv-start` 가 알아서 지웁니다

> `docs_viewer.py` 는 **기동 시 한 번만** `soffice` 를 찾고 `config.json` 을 읽습니다.
> LibreOffice 를 새로 깔았거나 설정을 고쳤다면 `./dv-restart` 를 해야 반영됩니다.
> 배너의 `office :` 줄에 경로가 찍히면 docx/xlsx 미리보기가 켜진 것입니다.

## 알아둘 점

- 마크다운 렌더러는 GFM 부분집합입니다. 각주와 수식(LaTeX)은 아직 없습니다
- **mermaid 스크립트(3.5MB)만은 예외적으로 외부 의존입니다.** 파이썬으로 다이어그램을
  그릴 수는 없어서 브라우저에서 렌더하는데, 그 스크립트를 `assets/` 에서 찾고 없으면
  **처음 다이어그램을 볼 때 한 번** jsdelivr 에서 `cache/` 로 내려받습니다. 이후로는
  네트워크를 쓰지 않고, 문서 내용은 어디로도 나가지 않습니다 (페이지 CSP 가
  `script-src 'self'` 라 스크립트도 우리 서버가 대신 내려줍니다).
  외부 호출이 싫으면 `--no-mermaid` 로 끄거나, 미리 받아 `assets/mermaid.min.js`
  로 두면 됩니다
- 하이라이터는 정규식 토큰화 방식이라 파서 기반(tree-sitter 등)만큼 정밀하지는 않습니다.
  드물게 오탐이 나면 해당 언어 규칙(`_ruleset`)만 손보면 됩니다
- office 변환은 `soffice` 설치 시에만 동작합니다: `brew install --cask libreoffice`
- **슬라이드만 PDF 로 변환합니다.** LibreOffice 의 `--convert-to html` 은 Impress 문서를
  odf2xhtml(XSLT) 필터로 처리하는데, 이게 장표 경계 없이 전체를 한 문서로 이어 붙이고
  도형·이미지도 대부분 버립니다. 대안인 `impress_html_Export` 는 반대로 텍스트 개요만
  남깁니다. PDF 는 장표당 1페이지에 원본 레이아웃이 그대로 남고 뷰어가 이미 PDF 를
  인라인 렌더하므로, 슬라이드는 PDF 로 뽑습니다 (`office_convert`)
- 워드·시트는 그대로 HTML 입니다. `HTML (StarWriter)` / `HTML (StarCalc)` 필터는 표와
  이미지를 잘 살리고, 특히 넓은 시트는 PDF 로 뽑으면 열이 페이지마다 잘려 오히려 못 봅니다
- **한글 깨짐 방지**: 문서가 `맑은 고딕` 같은 윈도우 전용 폰트를 지정했는데 이 머신에
  없으면, LibreOffice 는 자기가 번들한 폰트에서 대체품을 고릅니다. 그런데 번들 폰트에는
  한글이 하나도 없어서(Alef·Amiri·DavidCLM 등 히브리·아랍 폰트뿐) 히브리어 폰트
  `FrankRuhlHofshi` 같은 걸 집어오고, 결국 **한글이 통째로 사라집니다**.
  그래서 `fonts.conf` 로 시스템에 실제로 있는 CJK 폰트를 찍어주고
  (`맑은 고딕`·`굴림`·`돋움` → Apple SD Gothic Neo, `바탕`·`궁서` → AppleMyungjo,
  일본어 → Hiragino, 중국어 → PingFang), `FONTCONFIG_FILE` 로 soffice 에 넘깁니다.
  규칙에 없는 폰트도 마지막 후보로 CJK 폰트가 붙으므로(weak binding) 글자가 사라지진
  않습니다. 다른 폰트로 바꾸고 싶으면 `fonts.conf` 만 고치면 되고, 파일을 지우면
  LibreOffice 기본 동작으로 돌아갑니다
- **시트 이동은 오른쪽 목차 패널**: LibreOffice 는 시트를 전부 한 파일에 이어 붙이고 각
  시트 앞에 `<a name="tableN">` 앵커를 남깁니다. 문서 맨 위 Overview 에 링크가 있긴
  하지만 32장짜리 7MB 문서에서 매번 맨 위로 스크롤해 올라가야 해서, 앵커 목록을 뽑아
  마크다운 목차 자리에 그대로 싣습니다 (헤더가 `시트 32` 로 바뀝니다).
  누르면 iframe 의 hash 만 바꿔 점프하므로 문서를 다시 그리지 않고, iframe 이 같은
  오리진이라 **스크롤에 따라 현재 시트가 하이라이트**됩니다 (마크다운 목차와 동일).
  시트가 한 장뿐이면(LibreOffice 가 머리말을 안 넣습니다) 목차는 뜨지 않습니다.
  목록은 `cache/office/<키>/_sheets.json` 에 캐시됩니다
- office·pdf·csv 문서는 본문 폭 제한(960px)을 풉니다. 16:9 슬라이드나 넓은 표에
  글 읽기용 폭 제한을 걸면 내용은 쪼그라들고 좌우 여백만 커집니다
- 검색은 파일당 2MB, 전체 6초, 결과 200건에서 끊습니다 (대형 트리 보호)

## 라이선스

MIT — Copyright (c) 2026 KimGoon. 자세한 내용은 [LICENSE](LICENSE) 참고.

뷰어 본체는 파이썬 표준 라이브러리만 씁니다. 배포판에 함께 들어 있는
`assets/mermaid.min.js` 만 서드파티(mermaid, MIT)이며 고지는
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) 에 있습니다.
