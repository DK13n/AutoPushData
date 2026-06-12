import os
import io
import json
import time
from dotenv import load_dotenv
from datetime import datetime
import re

# Google API Libraries
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import gspread  # Thư viện chuyên dụng cho Google Sheets

# Gemini SDK
from google import genai
from google.genai import types

load_dotenv()
# ==================== CẤU HÌNH HỆ THỐNG ====================
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
PDF_FOLDER_ID = os.getenv('PDF_FOLDER_ID')
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
SHEET_NAME = os.getenv('SHEET_NAME')
MODEL_NAME = os.getenv('MODEL_NAME')

MAX_RETRIES = 3  # <--- SỐ LẦN THỬ LẠI NGAY LẬP TỨC CHO FILE LỖI
RETRY_DELAY = 5  # <--- SỐ GIÂY CHỜ GIỮA CÁC LẦN THỬ LẠI

PROMPT_JSON = """
Nhiệm vụ: Đọc tài liệu đính kèm, trích xuất và tóm tắt các thông tin cốt lõi. Trả về DUY NHẤT một object JSON hợp lệ (không kèm theo bất kỳ văn bản giải thích hay markdown code block nào).

QUY TẮC BẮT BUỘC:
1. Ngôn ngữ: TOÀN BỘ CÁC TRƯỜNG DỮ LIỆU ĐỀU PHẢI ĐƯỢC TÓM TẮT VÀ TRÌNH BÀY BẰNG TIẾNG VIỆT.
2. Chính xác & Cô đọng: Đi thẳng vào trọng tâm, sử dụng từ ngữ chuyên ngành chính xác, ngắn gọn.
3. Dữ liệu đánh dấu (Boolean): Chỉ xuất ra kiểu số nguyên 1 (nếu Có/Đúng/Liên quan) hoặc 0 (nếu Không/Sai/Không liên quan).
4. Cấu trúc JSON bắt buộc phải tuân thủ chính xác các key và định dạng như sau:

{
  "ten_van_ban": "Trích xuất nguyên văn tên chính thức của văn bản",
  "year_issued": "Năm ban hành văn bản/chính sách (ví dụ: 2019)",
  "years_in_effect": "Ghi rõ ngày/tháng/năm có hiệu lực và trạng thái hiện tại (ví dụ: 2020 - nay, hoặc 2018 - 2022)",
  "policy": "Tên gọi chính thức của chính sách/thông tư/nghị định",
  "education_levels": "Cấp bậc giáo dục áp dụng (ví dụ: Tất cả các cấp bậc, Đại học, Phổ thông, Mầm non...)",
  "key_words": "Cung cấp 3-5 từ khóa hoặc cụm từ ngắn gọn tóm tắt chủ đề cốt lõi, cách nhau bằng dấu chấm phẩy",
  "key_initiatives": "Tóm tắt các thay đổi, quy định hoặc hành động quan trọng nhất bằng 2-3 gạch đầu dòng ngắn gọn. Dùng ký tự '-' cho mỗi gạch đầu dòng.",
  "notes": "Các ghi chú quan trọng, bối cảnh, thông tin thay thế cho luật cũ hoặc ngày hiệu lực cụ thể trong 1-2 câu",
  "vn_pdf": 1,
  "en_pdf": 0,
  "related_quality": "Điền 1 nếu liên quan đến Chất lượng giáo dục, ngược lại điền 0",
  "related_inequality": "Điền 1 nếu liên quan đến Bất bình đẳng, ngược lại điền 0",
  "related_teacher_training": "Điền 1 nếu liên quan đến Đào tạo giáo viên, ngược lại điền 0",
  "related_early_childhood": "Điền 1 nếu liên quan đến Giáo dục mầm non, ngược lại điền 0",
  "related_curriculum": "Điền 1 nếu liên quan đến Chương trình học/Thi cử/Sách giáo khoa, ngược lại điền 0"
}
"""
# ==========================================================

SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets'
]


def get_credentials():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return creds


def download_file_from_drive(drive_service, file_id, local_path):
    request = drive_service.files().get_media(fileId=file_id)
    fh = io.FileIO(local_path, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    fh.close()


def format_cell_value(val):
    if isinstance(val, list):
        return "\n".join([str(v) for v in val])
    if val is None:
        return ""
    return val


def extract_file_number(file_dict):
    name = file_dict.get('name', '')
    match = re.search(r'\d+', name)
    return int(match.group()) if match else 0


def main():
    print("--- Khởi động hệ thống quét toàn diện (Hỗ trợ thư mục lớn) ---")
    creds = get_credentials()

    drive_service = build('drive', 'v3', credentials=creds)
    gs_client = gspread.authorize(creds)
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)

    try:
        spreadsheet = gs_client.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.worksheet(SHEET_NAME)
    except Exception as e:
        print(f"[LỖI] Không thể kết nối tới Google Sheet. Chi tiết: {e}")
        return

    print("Đang quét Google Sheet để lập danh sách chống trùng lặp...")
    all_rows = sheet.get_all_values()

    processed_ids = set()
    processed_names = set()

    for row in all_rows[2:]:
        if len(row) >= 2 and row[1]:
            processed_names.add(str(row[1]).strip())
        if len(row) >= 17 and row[16]:
            processed_ids.add(str(row[16]).strip())

    print(f"Hệ thống ghi nhận: {len(processed_names)} tên file và {len(processed_ids)} mã ID đã tồn tại trong bảng.")

    print("Đang quét toàn bộ thư mục Drive (đang lấy tất cả các trang file)...")
    all_available_pdfs = []
    page_token = None
    query = f"'{PDF_FOLDER_ID}' in parents and mimeType = 'application/pdf' and trashed = false"

    while True:
        results = drive_service.files().list(
            q=query,
            fields="nextPageToken, files(id, name)",
            pageSize=1000,
            pageToken=page_token
        ).execute()

        items = results.get('files', [])
        all_available_pdfs.extend(items)

        page_token = results.get('nextPageToken')
        if not page_token:
            break

    print(f"Tổng số file PDF tìm thấy thực tế trong thư mục Drive: {len(all_available_pdfs)}")

    new_pdfs = []
    for f in all_available_pdfs:
        f_id = f['id']
        f_name = f['name']
        if f_id not in processed_ids and f_name not in processed_names:
            new_pdfs.append(f)

    if not new_pdfs:
        print("Không phát hiện file PDF mới nào chưa được điền. Hệ thống dừng.")
        return

    print("Đang tiến hành sắp xếp các file mới theo thứ tự văn bản...")
    new_pdfs.sort(key=extract_file_number)

    print(f"Phát hiện {len(new_pdfs)} file mới sẽ được xử lý cuốn chiếu theo thứ tự:")
    for f in new_pdfs:
        print(f"  - {f['name']}")

    # 5. Vòng lặp xử lý chính thức (Đã nâng cấp cơ chế Retry ngay lập tức)
    for pdf in new_pdfs:
        pdf_id = pdf['id']
        pdf_name = pdf['name']
        temp_pdf = f"temp_{pdf_id}.pdf"

        success = False  # Biến đánh dấu trạng thái xử lý của file hiện tại

        # Vòng lặp thử lại cục bộ cho riêng file này
        for attempt in range(1, MAX_RETRIES + 1):
            uploaded_file = None
            if attempt == 1:
                print(f"\n[Đang xử lý] -> {pdf_name}")
            else:
                print(f"  [Thử lại lần {attempt}/{MAX_RETRIES}] Sau Lỗi Quy Trình...")
                time.sleep(RETRY_DELAY)

            try:
                # Chỉ tải file từ Drive về nếu file tạm chưa tồn tại cục bộ
                if not os.path.exists(temp_pdf):
                    download_file_from_drive(drive_service, pdf_id, temp_pdf)

                uploaded_file = gemini_client.files.upload(file=temp_pdf)

                response = gemini_client.models.generate_content(
                    model=MODEL_NAME,
                    contents=[uploaded_file, PROMPT_JSON],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                    )
                )

                try:
                    data = json.loads(response.text)
                except json.JSONDecodeError:
                    print("  [Lỗi] Gemini không trả về chuỗi JSON hợp lệ ở lượt này.")
                    # Xóa file trên bộ nhớ tạm Gemini trước khi sang lượt retry kế tiếp
                    if uploaded_file:
                        gemini_client.files.delete(name=uploaded_file.name)
                    continue  # Nhảy sang lần thử tiếp theo (attempt + 1)

                current_total_rows = len(sheet.get_all_values())
                stt = current_total_rows - 1 if current_total_rows > 2 else 1

                file_url = f"https://drive.google.com/file/d/{pdf_id}/view"
                hyperlink_formula = f'=HYPERLINK("{file_url}", "{pdf_name}")'

                row_to_append = [
                    stt,
                    hyperlink_formula,
                    format_cell_value(data.get('year_issued', '')),
                    format_cell_value(data.get('years_in_effect', '')),
                    format_cell_value(data.get('policy', '')),
                    format_cell_value(data.get('education_levels', '')),
                    format_cell_value(data.get('key_words', '')),
                    format_cell_value(data.get('key_initiatives', '')),
                    format_cell_value(data.get('notes', '')),
                    format_cell_value(data.get('vn_pdf', 1)),
                    format_cell_value(data.get('en_pdf', 0)),
                    format_cell_value(data.get('related_quality', 0)),
                    format_cell_value(data.get('related_inequality', 0)),
                    format_cell_value(data.get('related_teacher_training', 0)),
                    format_cell_value(data.get('related_early_childhood', 0)),
                    format_cell_value(data.get('related_curriculum', 0)),
                    pdf_id
                ]

                sheet.append_row(row_to_append, value_input_option='USER_ENTERED')
                print(f"  [Thành công] Đã đồng bộ vào Google Sheets.")

                success = True
                # Giải phóng tài nguyên tạm trên Gemini sau khi xong
                gemini_client.files.delete(name=uploaded_file.name)
                break  # Thoát khỏi vòng lặp thử lại vì đã thành công

            except Exception as ex:
                print(f"  [Lỗi Thao Tác lần {attempt}]: {ex}")
            finally:
                # Đảm bảo file được giải phóng trên Gemini nếu có lỗi xảy ra ở giữa tiến trình
                if not success and uploaded_file:
                    try:
                        gemini_client.files.delete(name=uploaded_file.name)
                    except:
                        pass

        # Dọn dẹp file PDF tạm lưu ở máy cục bộ sau khi kết thúc chuỗi thử lại của file đó
        if os.path.exists(temp_pdf):
            os.remove(temp_pdf)

        # Nếu đã thử hết số lần cho phép (MAX_RETRIES) mà vẫn thất bại hoàn toàn
        if not success:
            print(
                f"\n[DỪNG TIẾN TRÌNH THỰC THI] Đã thử liên tiếp {MAX_RETRIES} lần trên file '{pdf_name}' nhưng không thành công.")
            print("Chương trình dừng lại tại đây để bảo toàn chính xác thứ tự dòng dữ liệu cuốn chiếu.")
            break  # Thoát hẳn vòng lặp danh sách file PDF, không chạy các file phía sau

        time.sleep(2)  # Giảm tải cho Rate Limit của các file tiếp theo

    print("\n--- Tiến trình xử lý kết thúc ---")


if __name__ == '__main__':
    main()