import os
import csv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

SENDER_EMAIL = os.environ.get("MAIL_USERNAME")
SENDER_PASSWORD = os.environ.get("MAIL_PASSWORD")

# 🔹 Gmail 전용 SMTP 설정 (SSL 465번 포트)
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465

def send_custom_emails():
    # Gmail SSL 465 포트 보안 연결
    server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
    
    try:
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        print("✅ Gmail SMTP 로그인 성공!")
    except Exception as e:
        print(f"❌ 로그인 실패: {e}")
        raise e

    subject_template = ""
    body_template = ""
    recipients = []

    with open("recipients.csv", mode="r", encoding="utf-8-sig") as file:
        reader = csv.reader(file)
        
        is_recipient_list = False
        for row in reader:
            if not row:
                continue

            if row[0] == "[SUBJECT]":
                subject_template = row[1]
            elif row[0] == "[BODY]":
                body_template = row[1]
            elif row[0] == "name" and row[1] == "email":
                is_recipient_list = True
                continue
            
            elif is_recipient_list and len(row) >= 2:
                name, email = row[0].strip(), row[1].strip()
                if email:
                    recipients.append({"name": name, "email": email})

    for person in recipients:
        name = person["name"]
        email = person["email"]

        custom_subject = subject_template.replace("{name}", name)
        custom_body = body_template.replace("{name}", name)

        msg = MIMEMultipart()
        msg["From"] = SENDER_EMAIL
        msg["To"] = email
        msg["Subject"] = custom_subject
        msg.attach(MIMEText(custom_body, "plain", "utf-8"))

        try:
            server.send_message(msg)
            print(f"✅ 발송 성공: {name} ({email}) - 제목: {custom_subject}")
        except Exception as e:
            print(f"❌ 발송 실패: {email} - {e}")

    server.quit()

if __name__ == "__main__":
    send_custom_emails()
