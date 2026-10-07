import os
import csv
import html
import io
import re
import smtplib
import sys
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Windows 콘솔(cp949)에서 ✅/❌ 출력 시 UnicodeEncodeError 로 발송 결과가 실패 처리되지 않도록
sys.stdout.reconfigure(errors="replace")

SENDER_EMAIL = os.environ.get("MAIL_USERNAME")
SENDER_PASSWORD = os.environ.get("MAIL_PASSWORD")

# 🔹 기본 SMTP 설정 (Gmail, SSL 465번 포트). SMTP_HOST / SMTP_PORT 환경변수로 바꿀 수 있다
SMTP_SERVER = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))

# 보내는 주소의 도메인으로 SMTP 서버를 자동 선택한다
# 465 는 SSL 로 바로 연결, 그 외 포트(587 등)는 STARTTLS 로 연결한다
SMTP_PRESETS = {
    "gmail.com": ("smtp.gmail.com", 465),
    "googlemail.com": ("smtp.gmail.com", 465),
    "naver.com": ("smtp.naver.com", 465),
    "daum.net": ("smtp.daum.net", 465),
    "hanmail.net": ("smtp.daum.net", 465),
    "kakao.com": ("smtp.kakao.com", 465),
    "nate.com": ("smtp.mail.nate.com", 465),
    "outlook.com": ("smtp.office365.com", 587),
    "hotmail.com": ("smtp.office365.com", 587),
    "live.com": ("smtp.office365.com", 587),
    "icloud.com": ("smtp.mail.me.com", 587),
    "me.com": ("smtp.mail.me.com", 587),
    "yahoo.com": ("smtp.mail.yahoo.com", 465),
}


def smtp_preset(email):
    """보내는 주소에 맞는 (서버, 포트) 를 돌려준다. 모르는 도메인이면 None."""
    domain = (email or "").rsplit("@", 1)[-1].strip().lower()
    return SMTP_PRESETS.get(domain)


def connect_smtp(host, port):
    if port == 465:
        return smtplib.SMTP_SSL(host, port, timeout=30)
    server = smtplib.SMTP(host, port, timeout=30)
    server.starttls()
    return server

CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recipients.csv")


def load_campaign(path=CSV_PATH):
    """recipients.csv 에서 제목/본문 템플릿과 수신자 목록을 읽어온다."""
    with open(path, mode="r", encoding="utf-8-sig") as file:
        return parse_campaign_csv(file.read())


def parse_campaign_csv(text):
    """CSV 텍스트를 파싱한다.

    recipients.csv 형식([SUBJECT]/[BODY]/name,email 헤더)과
    단순 수신자 목록(한 줄에 '이름,이메일' 또는 '이메일')을 모두 지원한다.
    """
    subject_template = ""
    body_template = ""
    recipients = []

    reader = csv.reader(io.StringIO(text))

    is_recipient_list = False
    for row in reader:
        if not row:
            continue

        if row[0] == "[SUBJECT]":
            # 따옴표 없이 쉼표가 들어간 경우를 대비해 나머지 칸을 다시 합친다
            subject_template = ",".join(row[1:])
        elif row[0] == "[BODY]":
            body_template = ",".join(row[1:])
        elif row[:2] == ["name", "email"]:
            is_recipient_list = True
            continue

        elif is_recipient_list and len(row) >= 2:
            name, email = row[0].strip(), row[1].strip()
            if email:
                recipients.append({"name": name, "email": email})
        elif len(row) >= 2 and "@" in row[1]:
            recipients.append({"name": row[0].strip(), "email": row[1].strip()})
        elif "@" in row[0]:
            # '이메일' 만 있거나 '이메일,이름' 순서인 경우
            name = row[1].strip() if len(row) >= 2 else ""
            recipients.append({"name": name, "email": row[0].strip()})

    return {"subject": subject_template, "body": body_template, "recipients": recipients}


def render(template, name):
    return template.replace("{name}", name)


# 본문에 이미지를 넣는 표시: {image:파일명.png}, 너비 지정은 {image:파일명.png|300} (px) 또는 {image:파일명.png|50%}
IMAGE_RE = re.compile(r"\{image:([^{}|\r\n]+?)\s*(?:\|\s*(\d+)\s*(%|px)?\s*)?\}")
# {image: 로 시작하지만 위 형식에 맞지 않는 표시를 찾기 위한 것
IMAGE_START_RE = re.compile(r"\{image:")
# 본문 이미지로 쓸 수 있는 확장자와 MIME 하위 형식
IMAGE_TYPES = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "gif": "gif", "webp": "webp"}
MAX_IMAGE_PX = 2000


def image_refs(body):
    """본문의 {image:...} 표시 목록. (파일명, 너비 숫자 또는 None, '%' 또는 'px')"""
    return [(m.group(1).strip(), int(m.group(2)) if m.group(2) else None, m.group(3) or "px")
            for m in IMAGE_RE.finditer(body)]


# 본문에 링크를 넣는 표시: {link:보일 글자|https://주소}
LINK_RE = re.compile(r"\{link:([^{}|\r\n]+)\|\s*([^{}|\s]+)\s*\}")
LINK_START_RE = re.compile(r"\{link:")
LINK_SCHEMES = ("http://", "https://", "mailto:")
# 본문에 그냥 적은 주소(https://...)도 HTML 메일에서는 눌리는 링크로 만든다. 끝의 문장부호는 빼고
URL_RE = re.compile(r"https?://[^\s<>\"'{}]*[^\s<>\"'{}.,!?)\]]")
# 이미지와 링크 표시를 한 번에 찾는다
TAG_RE = re.compile(f"{IMAGE_RE.pattern}|{LINK_RE.pattern}")


def check_tags(body):
    """{image:...}, {link:...} 표시의 형식과 값이 올바른지 확인한다. 잘못되면 ValueError."""
    if len(IMAGE_START_RE.findall(body)) != len(IMAGE_RE.findall(body)):
        raise ValueError("이미지 표시 형식이 잘못됐습니다. {image:파일명}, {image:파일명|300}, "
                         "{image:파일명|50%} 처럼 써 주세요.")
    if len(LINK_START_RE.findall(body)) != len(LINK_RE.findall(body)):
        raise ValueError("링크 표시 형식이 잘못됐습니다. {link:보일 글자|https://주소} 처럼 써 주세요.")
    for text, url in LINK_RE.findall(body):
        if not url.lower().startswith(LINK_SCHEMES):
            raise ValueError(f"링크 주소는 http://, https:// 또는 mailto: 로 시작해야 합니다: {text.strip()}|{url}")
    for filename, width, unit in image_refs(body):
        if width is None:
            continue
        limit = 100 if unit == "%" else MAX_IMAGE_PX
        if not 1 <= width <= limit:
            raise ValueError(f"이미지 너비는 1~{limit}{unit} 사이로 써 주세요: {filename}|{width}{unit}")


def build_body(body_template, name, images=()):
    """본문 MIME 파트를 만든다.

    {image:...}, {link:...} 표시가 없으면 지금처럼 일반 텍스트 메일이고,
    있으면 그 자리에 이미지/링크가 보이는 HTML 메일(텍스트 버전 포함)로 만든다.
    images: (파일명, bytes) 튜플 목록. 파일명은 대소문자를 구분하지 않고 찾는다.
    """
    if not TAG_RE.search(body_template):
        return MIMEText(render(body_template, name), "plain", "utf-8")

    by_name = {fn.lower(): (fn, data) for fn, data in images}
    # 수신자 이름 안의 {image:...} 등이 표시로 바뀌지 않도록, 표시 사이의 글자 부분만 {name} 을 치환한다
    plain, rich, cids = [], [], {}

    def to_html(text):
        """글자를 HTML 로 바꾼다. 줄바꿈은 <br>, 그냥 적은 https:// 주소는 링크로."""
        out, pos = [], 0
        for u in URL_RE.finditer(text):
            out.append(html.escape(text[pos:u.start()]))
            out.append(f'<a href="{html.escape(u.group())}">{html.escape(u.group())}</a>')
            pos = u.end()
        out.append(html.escape(text[pos:]))
        return "".join(out).replace("\r\n", "\n").replace("\n", "<br>\n")

    def add_text(piece):
        text = render(piece, name)
        plain.append(text)
        rich.append(to_html(text))

    pos = 0
    for m in TAG_RE.finditer(body_template):
        add_text(body_template[pos:m.start()])
        pos = m.end()
        if m.group(4):  # {link:글자|주소}
            text, url = render(m.group(4).strip(), name), m.group(5)
            plain.append(f"{text} ({url})")
            rich.append(f'<a href="{html.escape(url)}">{html.escape(text)}</a>')
            continue
        filename, _ = by_name[m.group(1).strip().lower()]
        cid = cids.setdefault(filename.lower(), f"img{len(cids) + 1}")
        plain.append(f"[이미지: {filename}]")
        # width 속성은 Outlook 데스크톱용, style 은 그 밖의 메일 프로그램용. 화면보다 크면 줄어든다
        size = ""
        if m.group(2):
            width = m.group(2) + ("%" if m.group(3) == "%" else "")
            size = f' width="{width}"'
            width_css = width if width.endswith("%") else width + "px"
            style = f"width:{width_css};max-width:100%;height:auto"
        else:
            style = "max-width:100%"
        rich.append(f'<img src="cid:{cid}" alt="{html.escape(filename)}"{size} style="{style}">')
    add_text(body_template[pos:])

    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText("".join(plain), "plain", "utf-8"))
    alternative.attach(MIMEText(
        '<div style="font-family:sans-serif;white-space:normal">' + "".join(rich) + "</div>",
        "html", "utf-8"))
    if not cids:
        return alternative

    related = MIMEMultipart("related")
    related.attach(alternative)
    for key, cid in cids.items():
        filename, data = by_name[key]
        part = MIMEImage(data, _subtype=IMAGE_TYPES[filename.rsplit(".", 1)[-1].lower()])
        part.add_header("Content-ID", f"<{cid}>")
        part.add_header("Content-Disposition", "inline", filename=("utf-8", "", filename))
        related.attach(part)
    return related


def login_smtp(host, port, email, password):
    """SMTP 서버에 연결해 로그인한 연결을 돌려준다. 실패하면 예외를 던진다."""
    server = connect_smtp(host, port)
    try:
        server.login(email, password)
        print(f"✅ SMTP 로그인 성공! ({host}:{port})")
    except Exception as e:
        print(f"❌ 로그인 실패: {e}")
        server.close()
        raise e
    return server


def send_emails(subject_template, body_template, recipients, attachments=(),
                sender_email=None, sender_password=None, smtp_host=None, smtp_port=None,
                images=()):
    """수신자별로 개인화된 메일을 발송하고, 각 수신자의 결과 목록을 반환한다.

    attachments: (파일명, bytes) 튜플 목록. 모든 메일에 동일하게 첨부된다.
    images: (파일명, bytes) 튜플 목록. 본문의 {image:파일명} 자리에 표시된다.
    sender_email / sender_password 를 주지 않으면 MAIL_USERNAME / MAIL_PASSWORD 환경변수를 쓴다.
    smtp_host / smtp_port 를 주지 않으면 보내는 주소로 자동 선택하고, 모르는 도메인이면 기본값(Gmail)을 쓴다.
    """
    sender_email = sender_email or SENDER_EMAIL
    sender_password = sender_password or SENDER_PASSWORD
    if not smtp_host:
        smtp_host, smtp_port = smtp_preset(sender_email) or (SMTP_SERVER, SMTP_PORT)

    server = login_smtp(smtp_host, smtp_port or 465, sender_email, sender_password)

    results = []
    try:
        for person in recipients:
            name = person["name"]
            email = person["email"]

            custom_subject = render(subject_template, name)

            msg = MIMEMultipart()
            msg["From"] = sender_email
            msg["To"] = email
            msg["Subject"] = custom_subject
            msg.attach(build_body(body_template, name, images))
            for filename, data in attachments:
                part = MIMEApplication(data)
                part.add_header("Content-Disposition", "attachment", filename=("utf-8", "", filename))
                msg.attach(part)

            try:
                server.send_message(msg)
                print(f"✅ 발송 성공: {name} ({email}) - 제목: {custom_subject}")
                results.append({"name": name, "email": email, "ok": True})
            except Exception as e:
                print(f"❌ 발송 실패: {email} - {e}")
                results.append({"name": name, "email": email, "ok": False, "error": str(e)})
    finally:
        server.quit()

    return results


def send_custom_emails():
    campaign = load_campaign()
    send_emails(campaign["subject"], campaign["body"], campaign["recipients"])


if __name__ == "__main__":
    send_custom_emails()
