import hmac
import ipaddress
import json
import os
import re
import socket
from urllib.parse import urlsplit

from flask import Flask, jsonify, request, send_from_directory

from send_custom_mail import (
    IMAGE_TYPES, SMTP_PRESETS, check_tags, image_refs, login_smtp, parse_campaign_csv,
    render, send_emails, smtp_preset,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def read_api_token():
    """외부 접속 비밀번호. API_TOKEN 환경변수, 없으면 api_token.txt 파일."""
    if os.environ.get("API_TOKEN"):
        return os.environ["API_TOKEN"].strip()
    try:
        with open(os.path.join(BASE_DIR, "api_token.txt"), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


API_TOKEN = read_api_token()
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")
# 접속을 허용할 네트워크. 기본값은 이 컴퓨터 + 사설망(사무실 내부망) 주소
ALLOWED_NETWORKS = [
    ipaddress.ip_network(n.strip())
    for n in os.environ.get(
        "ALLOWED_NETWORKS", "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    ).split(",")
    if n.strip()
]
# 기본 발송 계정 저장 파일 (비밀번호가 들어 있으므로 .gitignore 에 포함)
SENDER_FILE = os.path.join(BASE_DIR, "sender.json")

app = Flask(__name__, static_folder="static")
# Gmail 첨부 한도(25MB)에 맞춘 요청 크기 제한
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024


def clean_filename(name):
    """경로와 제어문자만 제거한다 (secure_filename 은 한글을 지워버려서 쓰지 않음)."""
    name = re.split(r"[\\/]", name)[-1]
    return re.sub(r"[\x00-\x1f\x7f\"]", "", name).strip() or "attachment"


@app.before_request
def check_network():
    """사무실 내부망(ALLOWED_NETWORKS)에서 온 접속만 허용한다. 인터넷에서 온 접속은 거절."""
    try:
        ip = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        ip = None
    if ip is not None and getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    if ip is None or not any(ip in net for net in ALLOWED_NETWORKS):
        return jsonify(error="허용된 네트워크(사무실 내부망)에서만 접속할 수 있습니다."), 403
    return None


def is_external():
    """Cloudflare Tunnel 등 프록시를 거쳐 들어온(=사무실 밖에서 온) 요청인지."""
    return bool(request.headers.get("Cf-Connecting-Ip") or request.headers.get("X-Forwarded-For"))


@app.before_request
def check_token():
    """사무실 밖에서 온 /api 요청에는 접속 비밀번호(API_TOKEN)를 요구한다. 사무실 내부망은 그대로 통과."""
    if not request.path.startswith("/api/") or not is_external():
        return None
    if not API_TOKEN:
        return jsonify(error="외부 접속이 꺼져 있습니다. 서버에 접속 비밀번호(api_token.txt)를 설정하세요."), 403
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied.encode(), API_TOKEN.encode()):
        return jsonify(error="접속 비밀번호가 올바르지 않습니다."), 401
    return None


@app.before_request
def check_origin():
    """다른 웹사이트가 브라우저를 통해 몰래 보내는 요청(CSRF)을 막는다."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    origin = request.headers.get("Origin")
    if origin and urlsplit(origin).netloc != request.host:
        return jsonify(error="다른 사이트에서 온 요청은 허용되지 않습니다."), 403
    return None


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="요청이 너무 큽니다 (최대 25MB)."), 413


def read_request():
    """multipart 폼에서 제목, 본문, 수신자, 첨부파일을 읽고 검증한다.

    폼 필드:
      subject          제목 ({name} 치환 가능)
      body             본문 ({name} 치환 가능)
      recipients       텍스트 수신자 목록 (한 줄에 '이름,이메일' 또는 '이메일')
      recipients_file  수신자 CSV 파일 (recipients.csv 형식이면 제목/본문도 기본값으로 사용)
      attachments      첨부파일 (여러 개 가능)
      images           본문 이미지 (여러 개 가능, 본문의 {image:파일명} 자리에 표시)
    """
    form = request.form
    subject = form.get("subject", "")
    body = form.get("body", "")

    recipients = parse_campaign_csv(form.get("recipients", ""))["recipients"]
    upload = request.files.get("recipients_file")
    if upload and upload.filename:
        raw = upload.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            # 한국어 Windows 엑셀의 'CSV (쉼표로 분리)' 저장은 CP949 인코딩이다
            try:
                text = raw.decode("cp949")
            except UnicodeDecodeError:
                raise ValueError("수신자 파일을 읽을 수 없습니다. 엑셀에서 'CSV UTF-8' 로 저장해 주세요.")
        parsed = parse_campaign_csv(text)
        recipients += parsed["recipients"]
        subject = subject or parsed["subject"]
        body = body or parsed["body"]

    if not subject.strip():
        raise ValueError("제목을 입력하세요.")
    if "\n" in subject or "\r" in subject:
        raise ValueError("제목에는 줄바꿈을 넣을 수 없습니다.")
    if not body.strip():
        raise ValueError("본문을 입력하세요.")

    # 같은 주소가 여러 번 나오면 한 번만 보내고, 이름이 있는 쪽을 쓴다
    by_email = {}
    for person in recipients:
        email = person["email"]
        if not EMAIL_RE.match(email):
            raise ValueError(f"잘못된 이메일 주소: {email}")
        key = email.lower()
        if key not in by_email or (person["name"] and not by_email[key]["name"]):
            by_email[key] = person
    # 이름이 없으면 {name} 자리에 이메일 아이디를 넣는다
    cleaned = [{"name": p["name"] or p["email"].split("@")[0], "email": p["email"]}
               for p in by_email.values()]
    if not cleaned:
        raise ValueError("수신자가 없습니다.")

    attachments = [
        (clean_filename(f.filename), f.read())
        for f in request.files.getlist("attachments")
        if f and f.filename
    ]
    images = read_images(body)
    return subject, body, cleaned, attachments, images


def read_images(body):
    """본문 이미지(images 필드)를 읽고, 본문의 {image:파일명} 표시와 맞는지 확인한다."""
    images = [
        (clean_filename(f.filename), f.read())
        for f in request.files.getlist("images")
        if f and f.filename
    ]
    uploaded = set()
    for filename, _ in images:
        if "." not in filename or filename.rsplit(".", 1)[-1].lower() not in IMAGE_TYPES:
            raise ValueError(f"본문 이미지는 PNG, JPG, GIF, WEBP 파일만 넣을 수 있습니다: {filename}")
        if filename.lower() in uploaded:
            raise ValueError(f"같은 이름의 본문 이미지가 두 개 있습니다: {filename}")
        uploaded.add(filename.lower())

    check_tags(body)
    refs = {ref.lower(): ref for ref, _, _ in image_refs(body)}
    missing = [ref for key, ref in refs.items() if key not in uploaded]
    if missing:
        raise ValueError("본문에 쓴 이미지가 올라오지 않았습니다: " + ", ".join(missing)
                         + " (본문 이미지 칸에 같은 이름의 파일을 넣어 주세요)")
    unused = [fn for fn, _ in images if fn.lower() not in refs]
    if unused:
        raise ValueError("본문에서 쓰지 않은 이미지가 있습니다: " + ", ".join(unused)
                         + " (본문에 {image:파일명} 을 넣거나 이미지를 빼 주세요)")
    return images


def load_default_sender():
    """기본 발송 계정. sender.json 이 우선이고, 없으면 MAIL_USERNAME / MAIL_PASSWORD 환경변수."""
    try:
        with open(SENDER_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("email") and data.get("password"):
            return data
    except (OSError, ValueError):
        pass
    if os.environ.get("MAIL_USERNAME") and os.environ.get("MAIL_PASSWORD"):
        return {
            "email": os.environ["MAIL_USERNAME"],
            "password": os.environ["MAIL_PASSWORD"],
            "smtp_host": os.environ.get("SMTP_HOST"),
            "smtp_port": int(os.environ.get("SMTP_PORT", "465")) if os.environ.get("SMTP_HOST") else None,
        }
    return None


def save_default_sender(account):
    with open(SENDER_FILE, "w", encoding="utf-8") as f:
        json.dump(account, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(SENDER_FILE, 0o600)
    except OSError:
        pass


def resolve_account():
    """폼 입력과 기본 계정을 합쳐 실제로 쓸 발송 계정을 정한다.

    - 보내는 주소를 비우거나 기본 계정 주소 그대로 두고 비밀번호를 비우면 → 기본 계정
    - 다른 주소를 입력하면 → 그 주소와 비밀번호 (이번 발송에만 사용)
    - SMTP 서버를 직접 입력하면 그 서버, 아니면 기본 계정 설정 또는 주소로 자동 선택
    """
    default = load_default_sender()
    email = request.form.get("mail_username", "").strip() or (default or {}).get("email", "")
    password = request.form.get("mail_password", "")
    if not email:
        raise ValueError("보내는 메일 주소를 입력하세요. (저장된 기본 계정이 없습니다)")
    if not EMAIL_RE.match(email):
        raise ValueError(f"잘못된 보내는 주소: {email}")

    is_default = bool(default) and email.lower() == default["email"].lower()
    if not password:
        if not is_default:
            raise ValueError(f"{email} 의 비밀번호를 입력하세요.")
        password = default["password"]

    host = request.form.get("smtp_host", "").strip()
    port = request.form.get("smtp_port", "").strip()
    if host:
        if not HOST_RE.match(host):
            raise ValueError(f"잘못된 SMTP 서버 주소: {host}")
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError("SMTP 포트를 숫자로 입력하세요 (보통 465 또는 587).")
        smtp = (host, int(port))
    elif is_default and default.get("smtp_host"):
        smtp = (default["smtp_host"], default["smtp_port"])
    else:
        smtp = smtp_preset(email)
        if not smtp:
            domain = email.rsplit("@", 1)[-1]
            raise ValueError(f"{domain} 메일 서버를 자동으로 찾지 못했습니다. SMTP 서버 주소와 포트를 직접 입력하세요.")

    # Google 앱 비밀번호는 'abcd efgh ...' 처럼 띄어 써서 보여주므로 공백을 뺀다
    if smtp[0] == "smtp.gmail.com":
        password = password.replace(" ", "")
    # 자동 선택된 서버는 저장하지 않고, 직접 입력한 서버만 기본 계정에 저장한다
    manual = smtp != smtp_preset(email)
    return {
        "email": email,
        "password": password,
        "smtp_host": smtp[0] if manual else None,
        "smtp_port": smtp[1] if manual else None,
    }, smtp


def wants_save_default():
    return request.form.get("save_default") in ("1", "on", "true")


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/health")
def health():
    return jsonify(status="ok")


@app.get("/api/config")
def get_config():
    """기본 발송 계정 정보(비밀번호 제외)와 SMTP 자동 선택 목록."""
    default = load_default_sender()
    return jsonify(
        default_sender=default and {
            "email": default["email"],
            "smtp_host": default.get("smtp_host") or (smtp_preset(default["email"]) or [None, None])[0],
            "smtp_port": default.get("smtp_port") or (smtp_preset(default["email"]) or [None, None])[1],
        },
        smtp_presets=SMTP_PRESETS,
    )


@app.post("/api/default-sender")
def set_default_sender():
    """로그인이 되는지 확인한 뒤 기본 발송 계정으로 저장한다."""
    try:
        account, smtp = resolve_account()
    except ValueError as e:
        return jsonify(error=str(e)), 400
    try:
        login_smtp(smtp[0], smtp[1], account["email"], account["password"]).quit()
    except Exception as e:
        return jsonify(error=f"로그인 실패로 저장하지 않았습니다: {e}"), 502
    save_default_sender(account)
    return jsonify(email=account["email"], smtp_host=smtp[0], smtp_port=smtp[1])


@app.post("/api/preview")
def preview():
    try:
        subject, body, recipients, attachments, images = read_request()
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(
        attachments=[name for name, _ in attachments],
        images=[name for name, _ in images],
        messages=[
            {"name": p["name"], "email": p["email"],
             "subject": render(subject, p["name"]), "body": render(body, p["name"])}
            for p in recipients
        ],
    )


@app.post("/api/send")
def send():
    try:
        subject, body, recipients, attachments, images = read_request()
    except ValueError as e:
        return jsonify(error=str(e)), 400
    try:
        account, smtp = resolve_account()
    except ValueError as e:
        return jsonify(error=str(e)), 400

    try:
        results = send_emails(subject, body, recipients, attachments,
                              account["email"], account["password"], smtp[0], smtp[1],
                              images=images)
    except Exception as e:
        return jsonify(error=f"SMTP 오류: {e}"), 502
    # 로그인에 성공했으므로, 요청했다면 이 계정을 기본으로 저장한다
    if wants_save_default():
        save_default_sender(account)
    return jsonify(
        sender=account["email"],
        saved_default=wants_save_default(),
        sent=sum(r["ok"] for r in results),
        failed=sum(not r["ok"] for r in results),
        results=results,
    )


def lan_ip():
    """이 컴퓨터의 사무실 내부망 IP (직원들에게 알려줄 주소)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))  # 실제로 패킷을 보내지 않고 나가는 인터페이스만 확인
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    if host != "127.0.0.1":
        print("사무실 공유 모드: 아래 주소를 같은 사무실 네트워크의 직원들에게 알려주세요.")
        print(f"  http://{lan_ip()}:{port}")
        print("허용 네트워크:", ", ".join(str(n) for n in ALLOWED_NETWORKS))
    app.run(host=host, port=port, threaded=True)
