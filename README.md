# 📧 Auto Email Dispatcher (개인화 맞춤 이메일 대량 발송 시스템)

GitHub Actions 및 Python `smtplib`를 활용하여 수신자별 맞춤 이메일(이름, 제목, 본문)을 자동 발송하는 시스템입니다.

---

## 🚀 주요 기능

- **개인화 메일 발송**: `recipients.csv` 파일을 통해 수신자별 이름(`{name}`) 변수 치환 지원
- **자동화 파이프라인**: GitHub Actions 연동으로 웹 UI에서 버튼 클릭 한번으로 메일 발송 (`workflow_dispatch`)
- **다양한 SMTP 메일 서버 지원**: Gmail, Naver, Outlook/M365 등 주요 메일 서비스 연동 가능
- **보안 유지**: 계정 정보 및 암호는 GitHub Repository Secrets를 통한 안전한 관리

---

## 📁 프로젝트 구조

```text
├── .github/
│   └── workflows/
│       └── send_email.yml      # GitHub Actions 워크플로우 정의 파일
├── recipients.csv              # 메일 제목, 본문 템플릿 및 수신자 명단
├── send_custom_mail.py         # SMTP 연동 및 개인화 메일 발송 파이썬 스크립트
└── README.md                   # 프로젝트 안내 문서
```

---

## 웹 서버 (사무실 공유용)

```bash
pip install -r requirements.txt

# Gmail address and app password (https://myaccount.google.com/apppasswords)
export MAIL_USERNAME=you@gmail.com
export MAIL_PASSWORD=xxxxxxxxxxxxxxxx

python server.py          # http://127.0.0.1:5000
```

In the browser, enter the recipients (or upload a CSV), subject, body and attachments, then click **발송** (Send).

### Sharing in the office (internal network only)

Double-click `run_office.bat` (or run `HOST=0.0.0.0 python server.py`) and the address to share with staff is printed, e.g. `http://172.30.1.1:5000`.

- Only connections from the office internal network (private IPs: `10.x`, `172.16–31.x`, `192.168.x`) are allowed. Connections from the internet are refused, so no `API_TOKEN` is needed.
- To restrict access to the office network only, narrow it with `ALLOWED_NETWORKS`, e.g. `set ALLOWED_NETWORKS=127.0.0.1/32,172.30.1.0/24`.
- Do not set up port forwarding on the router. That would expose the server to the internet.
- If other PCs can't connect, check that the Windows firewall allows Python on the "private network".

### Sending account

- **Default sender account**: saved once and used for every send. Save it with **기본 계정으로 저장** (save as default account), which checks that the login works first and does not send any mail. It is stored in `sender.json`, which is gitignored.
  - `sender.json` holds the password in plain text, so keep it only on a server that only trusted people can access.
  - With no `sender.json`, the `MAIL_USERNAME` / `MAIL_PASSWORD` environment variables are the default account.
- **Sending from another account just this once**: click **다른 계정으로 보내기** (send from another account) and enter that address and password. The default account stays the same.
  - Tick **이 계정을 기본 발송 계정으로 저장** (save this account as the default sender) and, after a successful send, that account becomes the default.

### Sending mail server (SMTP)

The server is chosen automatically from the sender address's domain. For other domains, enter the server and port directly (465 = SSL, other ports = STARTTLS).

| Domain | SMTP server |
|---|---|
| gmail.com | smtp.gmail.com:465 |
| naver.com | smtp.naver.com:465 |
| daum.net, hanmail.net | smtp.daum.net:465 |
| kakao.com | smtp.kakao.com:465 |
| nate.com | smtp.mail.nate.com:465 |
| outlook.com, hotmail.com, live.com | smtp.office365.com:587 |
| icloud.com, me.com | smtp.mail.me.com:587 |
| yahoo.com | smtp.mail.yahoo.com:465 |

Naver and Daum require turning on SMTP in their mail settings first.

| Environment variable | Default | Description |
|---|---|---|
| `HOST` | `127.0.0.1` | Bind address. `0.0.0.0` = office sharing mode |
| `ALLOWED_NETWORKS` | localhost + private ranges | Networks allowed to connect (CIDRs, comma-separated) |
| `PORT` | `5000` | Port |
| `API_TOKEN` | (none) | Optional. When set, `/api/*` requests require `Authorization: Bearer <token>` |
| `MAIL_USERNAME` / `MAIL_PASSWORD` | (none) | Default sender account when there is no `sender.json` |
| `SMTP_HOST` / `SMTP_PORT` | (auto) | SMTP server for domains not in the list above |

### API

`POST /api/send` (send) and `POST /api/preview` (preview only) take `multipart/form-data`.

| Field | Description |
|---|---|
| `mail_username` / `mail_password` | Sender account. When omitted, the default account is used |
| `save_default` | `1` saves this account as the default sender after a successful send |
| `smtp_host` / `smtp_port` | SMTP server (when omitted, chosen automatically from the sender address) |
| `subject` | Subject (`{name}` is substituted) |
| `body` | Body (`{name}` is substituted) |
| `recipients` | Recipients as text. One `name,email` or `email` per line |
| `recipients_file` | Recipients CSV file. In `recipients.csv` format, it also fills an empty subject/body |
| `attachments` | Attachments (multiple allowed, 25MB total) |

```bash
curl -X POST http://127.0.0.1:5000/api/send \
  -F 'mail_username=you@naver.com' -F 'mail_password=...' \
  -F 'subject={name}님, 안내드립니다' \
  -F 'body=안녕하세요 {name}님.' \
  -F 'recipients=홍길동,hong@example.com' \
  -F 'recipients_file=@recipients.csv' \
  -F 'attachments=@report.pdf'
```

`POST /api/default-sender`: checks that the `mail_username` / `mail_password` (/ `smtp_host` / `smtp_port`) login works, then saves it as the default sender account.

Response: `{"sent": 2, "failed": 0, "results": [{"name": ..., "email": ..., "ok": true}, ...]}`

## CLI / GitHub Actions

`python send_custom_mail.py` sends to every recipient in `recipients.csv`, just as before. The GitHub Actions workflow (`send_email.yml`) is unchanged.
