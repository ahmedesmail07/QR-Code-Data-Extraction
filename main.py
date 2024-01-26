from datetime import datetime
import ftplib
import cv2
import os
import psycopg2
import smtplib
from email.mime.text import MIMEText
import numpy as np
import fitz
from pyzbar.pyzbar import decode
from PIL import Image
from dotenv import load_dotenv

load_dotenv()

# Define constants for file extensions and email settings
PDF_EXTENSIONS = (".pdf",)
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")

# Email server settings
SMTP_SERVER = os.environ["SMTP_SERVER"]
SMTP_PORT = int(os.environ["SMTP_PORT"])

# FTP Parameters
ftp_host = os.environ["FTP_HOST"]
ftp_user = os.environ["FTP_USER"]
ftp_pass = os.environ["FTP_PASS"]

# PostgreSQL Parameters
db_host = os.environ["DB_HOST"]
db_user = os.environ["DB_USER"]
db_pass = os.environ["DB_PASS"]
db_name = os.environ["DB_NAME"]

# Email settings
email_from = os.environ["EMAIL_FROM"]
password = os.environ["EMAIL_PASSWORD"]
email_to = os.environ["EMAIL_TO"]

# Initialize global FTP and database connection objects
ftp_conn = None
db_conn = None
db_cursor = None


def send_error_email(file_name, error):
    message = MIMEText(f"Error processing file {file_name}: {error}")
    message["Subject"] = f"Document Processing Error for {file_name}"
    message["From"] = email_from
    message["To"] = email_to

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(email_from, password)
        server.send_message(message)
        server.quit()
    except Exception as e:
        print(f"Error sending email: {e}")


def extract_qr_data_from_image(image):
    image = cv2.imdecode(np.frombuffer(image, np.uint8), -1)
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(image)
    decoded_objects = decode(pil_img)
    for obj in decoded_objects:
        if obj.type == "QRCODE":
            return obj.data.decode("utf-8")


def extract_qr_data_from_pdf(pdf_file):
    doc = fitz.open(stream=pdf_file, filetype="pdf")
    page = doc[0]
    images = page.get_images(full=True)
    for img in images:
        xref = img[0]
        img_data = doc.extract_image(xref)
        data = extract_qr_data_from_image(img_data["image"])
        if data:
            return data


def extract_qr_data(file_name, file_data):
    if file_name.lower().endswith(PDF_EXTENSIONS):
        return extract_qr_data_from_pdf(file_data)
    elif file_name.lower().endswith(IMAGE_EXTENSIONS):
        return extract_qr_data_from_image(file_data)
    else:
        return None


def setup_ftp_connection():
    global ftp_conn
    try:
        ftp_conn = ftplib.FTP(ftp_host)
        ftp_conn.login(ftp_user, ftp_pass)
    except ftplib.all_errors as e:
        print(f"FTP connection error: {e}")
        send_error_email("FTP Connection", str(e))


def setup_db_connection():
    global db_conn, db_cursor
    try:
        db_conn = psycopg2.connect(
            host=db_host, user=db_user, password=db_pass, database=db_name
        )
        db_cursor = db_conn.cursor()
    except psycopg2.Error as e:
        print(f"Database connection error: {e}")
        send_error_email("Database Connection", str(e))


def check_processed(file_name):
    db_cursor.execute(
        "SELECT processed FROM processed_files WHERE file_name = %s;", (file_name,)
    )
    result = db_cursor.fetchone()
    return result[0] if result else False


def update_database(file_name, qr_data=None):
    if qr_data:
        file_type, person_identifier, start_validity, end_validity = qr_data.split(",")
        start_validity_date = datetime.strptime(start_validity, "%Y-%m-%d").date()
        end_validity_date = datetime.strptime(end_validity, "%Y-%m-%d").date()

        db_cursor.execute(
            """
            INSERT INTO processed_files (file_name, file_type, person_identifier, start_validity, end_validity, processed)
            VALUES (%s, %s, %s, %s, %s, TRUE)
            ON CONFLICT (file_name) DO UPDATE
            SET file_type = EXCLUDED.file_type,
                person_identifier = EXCLUDED.person_identifier,
                start_validity = EXCLUDED.start_validity,
                end_validity = EXCLUDED.end_validity,
                processed = TRUE,
                processed_at = CURRENT_TIMESTAMP;
            """,
            (
                file_name,
                file_type,
                person_identifier,
                start_validity_date,
                end_validity_date,
            ),
        )
        db_conn.commit()
    else:
        db_cursor.execute(
            """
            INSERT INTO processed_files (file_name, processed)
            VALUES (%s, FALSE)
            ON CONFLICT (file_name) DO NOTHING;
            """,
            (file_name,),
        )
        db_conn.commit()


def process_files(file_list):
    for file_name in file_list:
        try:
            if check_processed(file_name):
                print(f"File {file_name} has already been processed.")
                continue

            file_data = None
            if file_name.lower().endswith(PDF_EXTENSIONS + IMAGE_EXTENSIONS):
                file_data = bytearray()
                ftp_conn.retrbinary(f"RETR {file_name}", file_data.extend)

            qr_data = extract_qr_data(file_name, file_data) if file_data else None
            update_database(file_name, qr_data)

        except ftplib.all_errors as e:
            print(f"FTP file read error for {file_name}: {e}")
            send_error_email(file_name, str(e))
        except Exception as e:
            print(f"General error for {file_name}: {e}")
            send_error_email(file_name, str(e))


def upload_file_to_ftp(file_path, ftp_host, ftp_user, ftp_pass):
    with ftplib.FTP(ftp_host) as ftp:
        ftp.login(ftp_user, ftp_pass)
        file_name = os.path.basename(file_path)
        try:
            with open(file_path, "rb") as file:
                ftp.storbinary(f"STOR {file_name}", file)
                print(f"File {file_name} uploaded to FTP successfully.")
        except ftplib.all_errors as e:
            print(f"Error uploading file to FTP: {e}")
            send_error_email("FTP File Upload", str(e))


def main():
    setup_ftp_connection()
    setup_db_connection()

    upload_file_to_ftp("path/to/your/file", ftp_host, ftp_user, ftp_pass)
    upload_file_to_ftp("path/to/your/file", ftp_host, ftp_user, ftp_pass)

    if ftp_conn and db_conn:
        try:
            file_list = ftp_conn.nlst()
            process_files(file_list)
        except ftplib.all_errors as e:
            print(f"Error retrieving file list from FTP: {e}")
            send_error_email("FTP File List", str(e))
        finally:
            ftp_conn.quit()
            db_cursor.close()
            db_conn.close()


if __name__ == "__main__":
    main()
