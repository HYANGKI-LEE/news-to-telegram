# news-to-telegram

여러 신문사 섹션 페이지를 주기적으로 확인해서 새 기사만 텔레그램 **채널**(`@kaylie_corp_news`)로 전송합니다. 누구나 그 채널 링크(`t.me/kaylie_corp_news`)로 구독할 수 있고, 봇에게 따로 말을 걸 필요는 없습니다.

- `main.py` — 실행 진입점. `SOURCES` 목록의 각 URL을 파싱해서 신규 기사를 추려낸 뒤 채널로 전송하고, 이미 보낸 기사 id는 `state.json`에 기록합니다.
- `config.json` — 로컬 실행용 토큰/chat_id 자리 (빈 값). 이 리포는 public이라 실제 값은 여기 넣지 말고 GitHub Actions secrets(`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`)로 주입합니다. `TELEGRAM_CHAT_ID`는 채널 username(`@kaylie_corp_news`)입니다. 로컬에서 테스트할 땐 환경변수로 넘기거나 이 파일에 잠깐 값을 넣었다가 커밋하지 않도록 주의하세요.
- `.github/workflows/poll.yml` — 10분마다 `python main.py`를 실행하는 GitHub Actions 워크플로. 필요한 secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (Settings → Secrets and variables → Actions에서 등록).
- `state.json` — 소스별로 이미 전송한 기사 id 목록과 전체 소스를 통틀어 이미 보낸 URL 목록(`_global_sent_urls`, 중복 방지용). 매 실행마다 갱신되어 자동 커밋·push 됩니다.

채널로 보내려면 봇을 그 채널의 관리자로 추가하고 "Post Messages" 권한을 켜야 합니다.

## 실행

```
python main.py
```

토큰/chat_id는 환경변수 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`로 주는 게 기본이고(GitHub Actions에서는 secrets로 주입), 없으면 `config.json` 값을 씁니다.

첫 실행 시에는 각 소스별로 현재 기사 목록을 기준선(baseline)으로만 저장하고 전송하지 않습니다. 그 다음 실행부터 새로 올라온 기사만 전송됩니다.

## 소스 추가/변경

`main.py`의 `SOURCES` 리스트에 항목을 추가하면 됩니다. 새로운 도메인이면 해당 사이트의 목록 페이지 HTML 구조에 맞는 parser 함수를 추가해야 합니다.
