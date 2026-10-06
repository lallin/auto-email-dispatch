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
