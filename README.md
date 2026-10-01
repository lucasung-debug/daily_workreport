# WorkReport — 일일 업무일지 · 회의록 자동화 (Windows 11)

PC 활동과 회의 녹음을 자동으로 기록하고, 퇴근 무렵 **금일 실적 / 명일 계획 / 이슈** 형식의 업무일지 초안과 회의록을 만들어 주는 데스크톱 프로그램입니다.

- **활동 기록**: 활성 창의 앱 이름·창 제목·사용 시간을 2초마다 기록합니다. 5분 이상 입력이 없거나 잠금 화면이면 '자리 비움'으로 기록합니다.
- **회의록**: Windows 11 기본 **녹음기** 앱으로 녹음한 파일이나 앱 내장 녹음(마이크 + PC 소리)을 녹음이 끝난 뒤 텍스트로 변환(STT)합니다. 이어서 Claude 가 요약·결정 사항·액션아이템을 정리합니다.
- **업무일지 초안**: 활동 집계, 회의록, 내 액션아이템, 메모, 전날 계획을 모아 Claude 가 초안을 씁니다. 확인·수정한 뒤 확정합니다.
- **저장**: 모든 기록은 이 PC 의 SQLite DB 에 날짜별로 쌓이고, 기록 조회 탭의 달력에서 지난 일지와 회의록을 볼 수 있습니다.

> 실시간 음성 인식은 하지 않습니다. 녹음 파일이 완성된 뒤에 변환합니다.

---

## 설치와 실행

### 방법 1. exe 실행
GitHub Actions 의 `WorkReport-windows` 아티팩트(`WorkReport.exe`)를 내려받아 실행합니다. 직접 빌드하려면 [exe 빌드](#exe-빌드)를 보세요.

### 방법 2. Python 소스 실행
Python 3.11 이상이 필요합니다.

```powershell
git clone https://github.com/lucasung-debug/daily_workreport.git
cd daily_workreport
python -m pip install -e ".[whisper]"     # 로컬 Whisper 포함 (클라우드 STT 만 쓸 거면 [whisper] 생략)
pythonw -m workreport                      # 콘솔 창 없이 실행 (디버그 시 python -m workreport)
```

창을 닫아도 **시스템 트레이**에서 계속 기록합니다. 완전히 끄려면 트레이 아이콘 → 종료를 누릅니다.

### 처음 설정 (설정 탭)
1. **내 이름**: 회의록 액션아이템 중 내 일을 가려내는 데 씁니다.
2. **출근/퇴근 시각**: 퇴근 10분 전(변경 가능)에 업무일지 초안을 자동으로 만듭니다.
3. **Claude API 키**: https://console.anthropic.com 에서 발급합니다. 키는 Windows 자격 증명 관리자에 저장됩니다.
   - 키가 없어도 동작합니다. 업무일지는 사용 시간 통계로 만든 초안을 쓰고, 회의는 전사문만 저장됩니다.
4. **Windows 시작 시 자동 실행**을 켜면 로그인할 때 트레이로 시작합니다.

---

## 화면 구성

왼쪽 사이드바에서 화면을 오가고, 사이드바 아래에서 **활동 기록 켜기/끄기**와 **회의 녹음**을 바로 할 수 있습니다. 라이트·다크 테마는 Windows 설정을 따르며 설정에서 고정할 수 있습니다.

| 화면 | 내용 |
|---|---|
| 오늘 | PC 활동·가장 많이 쓴 앱·회의·자리 비움 지표, 하루 타임라인(마우스를 올리면 상세), 앱별 사용 시간, 오늘 회의, 빠른 메모 |
| 업무일지 | 날짜 이동, **AI 초안**, 항목 카드 편집(입력하면 자동 저장), 확정, 평문 복사·Markdown 내보내기, 메모·회의 액션아이템(‘＋’로 명일 계획에 추가) |
| 회의록 | 회의 검색, 변환 진행률, 회의록 카드(요약·액션아이템 체크리스트·결정·논의·미결), 채팅형 전사문(더블클릭하면 그 위치부터 재생) |
| 기록 | 달력의 점(초록 확정·주황 초안·보라 회의), 이번 달 요약, 날짜별 미리보기 |
| 설정 | 일반·활동 기록·회의 녹음·음성 인식·AI·개인정보 페이지, Claude 연결 테스트 |

처음 실행하면 3단계 시작 마법사에서 이름·근무시간·API 키(선택)를 설정합니다.

## 사용법

### 업무일지
- **업무일지** → **AI 초안** → 항목 카드에서 수정(자동 저장) → **확정**
- **평문 복사**: 그룹웨어·메신저에 붙여넣기 좋은 형식으로 복사합니다.
- **Markdown 내보내기**: 앱별 사용 시간과 시간대별 타임라인이 부록으로 붙습니다.
- 트레이 → **빠른 메모**로 적은 내용은 그날 초안에 반영됩니다.
- 사용자가 이미 작성한 일지는 자동 초안이 덮어쓰지 않습니다.

### 회의록 — 녹음 방법 두 가지

| | Windows 녹음기 앱 | 앱 내장 녹음 |
|---|---|---|
| 시작 | 녹음기 앱에서 녹음 → 저장 | 회의록 탭 또는 트레이의 **● 회의 녹음 시작** |
| 녹음 대상 | 마이크만 (대면 회의에 적합) | 마이크 + PC 소리 (Teams·Zoom 상대방 목소리 포함) |
| 처리 | 저장 폴더(`문서\Sound Recordings`)를 감시하다 새 파일을 자동 처리 | 녹음을 멈추면 바로 처리 |
| 화자 구분 | Azure·CLOVA 사용 시 화자 분리 | **나 / 상대방** 자동 구분(2채널 녹음) + 클라우드 엔진 화자 분리 |

- 녹음기 앱에서 녹음 파일 이름을 바꾸면(예: `고객사 킥오프`) 그 이름이 회의 제목이 됩니다.
- Teams·Zoom 등에서 마이크가 켜지면 "회의 중인가요?" 알림이 뜹니다. 알림을 누르면 녹음이 시작됩니다.
- 처리 순서: `대기 → 음성 변환 중(진행률) → 회의록 작성 중 → 완료`. 앱을 껐다 켜도 이어서 처리합니다.
- 회의록 화면에서 요약·결정 사항·액션아이템을 고치면 자동 저장되고, 전사문 말풍선을 더블클릭하면 그 위치부터 재생합니다.
- **연동**: 회의 참석은 '금일 실적'에, 끝나지 않은 내 액션아이템은 '명일 계획'에 반영됩니다.
- 회의를 녹음할 때는 **반드시 참석자에게 알리고 동의를 받으세요.**

### STT 엔진 (설정 → 음성 인식)

| 엔진 | 특징 | 준비물 |
|---|---|---|
| 로컬 Whisper (기본) | 오프라인이라 음성이 PC 밖으로 나가지 않음. CPU 기준 1시간 회의에 수십 분 걸릴 수 있음 | `pip install -e .[whisper]` (exe 에는 포함) |
| Azure AI Speech | 빠르고 화자 분리 지원 | Speech 리소스 키, 지역(예: `koreacentral`) |
| 네이버 CLOVA Speech | 한국어 인식률·화자 분리 우수 | CLOVA Speech Invoke URL, Secret Key |

**사내망에서 Whisper 모델 다운로드가 막힐 때**: 인터넷이 되는 PC 에서 Hugging Face `Systran/faster-whisper-small` 저장소 파일(`model.bin`, `config.json`, `tokenizer.json`, `vocabulary.txt`)을 한 폴더에 받습니다. 그다음 설정의 **Whisper 모델 폴더**에 그 폴더를 지정하세요. GPU(NVIDIA)가 있으면 장치 `cuda`, 모델 `large-v3` 로 정확도를 높일 수 있습니다.

---

## 개인정보·보안

- **로컬 저장**: 활동 기록·회의·업무일지는 `%LOCALAPPDATA%\WorkReport\workreport.db` 에, 설정은 `%APPDATA%\WorkReport\config.json` 에 저장됩니다. API 키는 Windows 자격 증명 관리자에 저장됩니다.
- **외부 전송** (설정한 경우에만)
  - Claude API: 업무일지 초안용 활동 집계(앱·창 제목·시간), 회의 전사문, 메모
  - Azure/CLOVA: 회의 오디오 (로컬 Whisper 를 쓰면 전송 없음)
  - 스크린샷 분석(기본 OFF): 켜면 N분마다 화면을 Claude 로 보내 한 문장 설명만 저장하고, 원본 이미지는 지웁니다.
- **제외 목록**: 설정의 제외 앱·제목 키워드(기본값: InPrivate, 시크릿, 뱅킹 등)에 걸리면 창 제목을 `[제외됨]`으로 가립니다.
- **기록 범위**: 트레이 → **기록 일시정지**로 언제든 멈출 수 있습니다. 기본으로는 출근 30분 전부터 퇴근 3시간 후까지만 기록합니다.
- **녹음 파일 정리**: 앱 내장 녹음 파일은 보관 기간(기본 30일)이 지나면 삭제됩니다. 녹음기 앱 폴더의 원본은 건드리지 않습니다.

---

## Windows 11 PC 점검 절차

CI 는 오디오 장치가 없는 환경에서 돌기 때문에, 처음 설치한 PC 에서 아래를 한 번 확인해 주세요.

1. 앱 실행 → **오늘** 탭에 지금 쓰는 앱·창 제목이 1분 안에 나타나는지 확인
2. 5분간 입력하지 않거나 `Win+L` 로 잠근 뒤 → '(자리 비움)'으로 기록되는지 확인
3. **녹음기 앱**으로 30초 녹음 → 저장 → 약 10초 뒤 **회의록** 탭에 회의가 추가되고 변환되는지 확인
4. **● 회의 녹음 시작** → 유튜브 영상 재생 + 말하기 → 중지 → 전사문에 '나'와 '상대방'이 함께 나오는지 확인
5. Teams/Zoom 테스트 통화를 시작하면 "회의 중인가요?" 알림이 뜨는지 확인
6. Azure·CLOVA 를 쓸 경우 짧은 파일로 한 번 변환해 보기
   - 두 서비스는 공개 API 문서와 예제 형식대로 구현했지만 이 저장소의 테스트는 실제 키 없이 모의 응답으로만 돌았습니다.

## 문제 해결

| 증상 | 확인할 것 |
|---|---|
| 회의가 자동으로 안 들어옴 | 설정의 녹음기 앱 저장 폴더가 녹음기 앱 설정(⋯ → 설정 → 녹음 위치)과 같은지. 앱을 처음 켜기 전에 녹음된 파일은 가져오지 않으니 **파일 가져오기**로 추가 |
| "Whisper 모델을 불러오지 못했습니다" | 인터넷·프록시 확인 또는 모델 폴더 수동 지정 |
| 상대방 목소리가 녹음 안 됨 | 설정의 스피커(루프백)를 실제 출력 장치(헤드셋 등)로 지정 |
| 초안이 통계형으로만 나옴 | Claude API 키 설정 여부(설정 탭 상태 표시) |
| 로그 | `%LOCALAPPDATA%\WorkReport\logs\workreport.log` |

---

## exe 빌드

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1            # dist\WorkReport.exe (단일 파일)
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1 -OneDir    # dist\WorkReport\ (폴더형, 시작이 빠름)
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1 -NoWhisper # 로컬 Whisper 제외 (용량↓)
```

push 할 때마다 GitHub Actions 가 Windows·Linux 테스트, exe 빌드, 실행 스모크 테스트를 돌리고 `WorkReport-windows` 아티팩트를 올립니다.

## 개발

```bash
python -m pip install -e ".[whisper,dev]"
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

```
src/workreport/
  collector/   활성 창·유휴·잠금 감지(Win32), 세션 기록, 제외 규칙, 옵션 스크린샷
  meeting/     녹음기 폴더 감시, 앱 내장 녹음, 회의 감지, STT(whisper/azure/clova), 회의록, 처리 파이프라인
  analysis/    하루 집계, Claude 공통 호출, 업무일지 초안, 프롬프트
  report/      업무일지·회의록 Markdown/평문 렌더링
  ui/          PySide6 화면(theme·icons·widgets 디자인 시스템, Pretendard 글꼴 번들), 트레이
  services.py  백그라운드 서비스 묶음 · app.py 부트스트랩 · db.py SQLite
```

Claude 모델은 기본 `claude-opus-5-5` 이고 설정에서 바꿀 수 있습니다. 요청이 거절되면 서버 측 대체 모델로 다시 시도합니다(`fallbacks: "default"`, 설정에서 끌 수 있음).

글꼴: [Pretendard](https://github.com/orioncactus/pretendard) (SIL Open Font License 1.1, `src/workreport/ui/assets/fonts/OFL-Pretendard.txt`).
