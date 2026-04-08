# Dispatch Map

운영 메모

- 공개 도메인 `dispatch.nasilfamily.com` 은 Cloudflare Tunnel 을 통해 Streamlit 앱으로 연결된다.
- 현재 공개 라우팅 기준:
  `dispatch.nasilfamily.com -> 127.0.0.1:8514`
- Dispatch 프론트가 호출하는 Django API 기준:
  `http://127.0.0.1:8010/api/dispatch`
- `8000` 포트는 dispatch API 용도로 쓰지 않는다. 이 포트에는 다른 Django 앱이 떠 있을 수 있어 `assignment-runs/*` 가 404 날 수 있다.

텔레그램 관련

- 배정 이력 저장 성공 시 텔레그램 요약/메모를 자동 전송하도록 연결되어 있다.
- 수동 전송은 앱 내 `텔레그램 단체방 전송` 영역에서 동일 로직을 사용한다.
- 요약 메시지 집계는 다음 기준을 사용한다.
  `총박스`, `나실 할당`, `기사 할당`
- `나실 할당` 은 현재 `김태경`, `김태균`, 또는 이름에 `나실` 이 포함된 기사 기준으로 계산한다.

운영 시 주의사항

- 사이트는 살아 있는데 저장만 실패하면 먼저 `DJANGO_API_BASE_URL` 이 `8010` 을 보고 있는지 확인한다.
- Cloudflare `1033` 이 뜨면 앱 문제가 아니라 `cloudflared` 프로세스가 죽은 경우가 많다.
- Streamlit 첫 실행 프롬프트를 막기 위해 사용자 홈 `C:\Users\niceh\.streamlit\credentials.toml` 이 필요하다.
- 공개 포트와 내부 API 포트를 혼용하지 말고, 변경 시 Tunnel 설정과 실행 스크립트를 함께 수정한다.
