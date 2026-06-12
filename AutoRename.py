import os
import io
import time
from dotenv import load_dotenv
import re
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import PyPDF2  # Đảm bảo import thư viện đọc PDF

# Import thư viện Gemini SDK mới
from google import genai

load_dotenv()

# ==================== CẤU HÌNH HỆ THỐNG ====================
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
FOLDER_ID = os.getenv('RENAME_FOLDER_ID')
MODEL_NAME = os.getenv('MODEL_NAME', 'gemini-3.1-flash-lite')

MAX_RETRIES = 3  # Số lần thử lại ngay lập tức tại chỗ nếu một file gặp lỗi quy trình
RETRY_DELAY = 5  # Số giây chờ giữa các lần thử lại
# ==========================================================

SCOPES = ['https://www.googleapis.com/auth/drive']


def authenticate_drive():
    """Xác thực Google Drive API"""
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
    return build('drive', 'v3', credentials=creds)

def download_file_from_drive(service, file_id, local_path):
    """Tải file từ Google Drive về máy cục bộ"""
    request = service.files().get_media(fileId=file_id)
    fh = io.FileIO(local_path, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while done is False:
        status, done = downloader.next_chunk()
    fh.close()

def extract_text_from_pdf_stream(pdf_stream, max_pages=2):
    """Trích xuất text từ luồng dữ liệu PDF (chỉ lấy vài trang đầu)"""
    try:
        reader = PyPDF2.PdfReader(pdf_stream)
        text = ""
        num_pages = min(len(reader.pages), max_pages)
        for i in range(num_pages):
            text += reader.pages[i].extract_text() + "\n"
        return text
    except Exception as e:
        print(f"Lỗi khi đọc PDF: {e}")
        return None


def generate_filename_with_ai(client, model_name, old_name, text, calculated_stt):
    """Sử dụng AI để tạo tên file dựa trên STT tính toán từ hệ thống"""
    if not text or len(text.strip()) == 0:
        return None

    # Prompt tối ưu: Ép mô hình sử dụng tiền tố H và xử lý rút gọn khi thiếu số hiệu
    prompt_instruction = f"""
    Nhiệm vụ của bạn là kiểm tra và chuẩn hóa tên file PDF về chính sách giáo dục theo quy tắc nghiêm ngặt dưới đây.

    SỐ THỨ TỰ CỐ ĐỊNH CHO FILE NÀY LÀ: {calculated_stt} (Bắt buộc phải sử dụng số này làm tiền tố H)

    QUY TẮC ĐẶT TÊN FILE:
    - TRƯỜNG HỢP 1 (Nếu CÓ số hiệu/luật số): Định dạng tên là: H{calculated_stt}. [Loại văn bản bằng tiếng Anh] [Số hiệu lực hoặc Luật số]
      Ví dụ: H{calculated_stt}. Decision 557/QĐ-UBND
    - TRƯỜNG HỢP 2 (Nếu KHÔNG CÓ số hiệu/luật số): Rút ngắn gọn định dạng tên lại chỉ còn: H{calculated_stt}. [Loại văn bản bằng tiếng Anh]
      Ví dụ: H{calculated_stt}. Decision

    Chi tiết các thành phần cấu trúc:
    1. [Loại văn bản bằng tiếng Anh]: Xác định loại văn bản từ nội dung và dịch sang tiếng Anh theo đúng danh sách chuẩn sau:
       - Nghị quyết -> Resolution
       - Quyết định -> Decision
       - Thông tư -> Circular
       - Thông tư liên tịch -> Inter-ministerial Circular
       - Kế hoạch -> Implementation Plan
       - Chỉ thị -> Directive
       - Hướng dẫn -> Guidance Document
       - Thông báo -> Announcement
       - Công điện -> Official Telegram
       - Báo cáo -> Report
       - Nghị định -> Decree
       - Luật giáo dục sửa đổi -> Law on Education
       - Luật Giáo dục đại học sửa đổi -> Law on Higher Education
       Nếu xuất hiện loại văn bản khác, hãy tự xác định và dịch sang tiếng Anh chuyên ngành tương đương.
    2. [Số hiệu lực/Luật số]: Tìm ở phần kí hiệu "Số:" hoặc "Số hiệu:" trong nội dung văn bản. Nếu tài liệu hoàn toàn KHÔNG có thông tin số hiệu, áp dụng ngay TRƯỜNG HỢP 2 (bỏ hoàn toàn phần số hiệu).

    YÊU CẦU ĐẦU RA:
    - CHỈ trả về duy nhất chuỗi tên file chuẩn thu được (KHÔNG kèm đuôi .pdf, KHÔNG giải thích dông dài).

    DỮ LIỆU ĐẦU VÀO PHÂN TÍCH:
    - Tên file cũ hiện tại: {old_name}
    - Nội dung văn bản trích xuất:
    {text[:3000]}
    """

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=prompt_instruction
        )
        new_name = response.text.strip()

        # Dọn dẹp ký tự cấm hệ thống (Giữ lại dấu / cho số hiệu trên Drive)
        invalid_chars = '<>:"\\|?*'
        for char in invalid_chars:
            new_name = new_name.replace(char, '')
        return new_name
    except Exception as e:
        print(f"Lỗi khi gọi AI API: {e}")
        return None


def extract_file_number(file_dict):
    """Bóc tách số thứ tự xuất hiện trong tên file cũ để sắp xếp đúng thứ tự xuất hiện ban đầu"""
    name = file_dict.get('name', '')
    match = re.search(r'\d+', name)
    return int(match.group()) if match else 0


def main():
    service = authenticate_drive()

    if not GEMINI_API_KEY:
        print("Lỗi: Không tìm thấy GEMINI_API_KEY trong biến môi trường.")
        return
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)

    # 1. Quét toàn bộ file trong folder hiện tại bằng cơ chế Page Token
    print("Đang tải danh sách file từ Google Drive...")
    all_items = []
    page_token = None
    query = f"'{FOLDER_ID}' in parents and mimeType='application/pdf' and trashed=false"

    while True:
        results = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name)",
            pageSize=1000,
            pageToken=page_token
        ).execute()
        all_items.extend(results.get('files', []))
        page_token = results.get('nextPageToken')
        if not page_token:
            break

    if not all_items:
        print('Không tìm thấy file PDF nào trong thư mục.')
        return

    # 2. PHÂN LOẠI CỤC BỘ: Tách file đã sửa tên (H...) và file chưa sửa tên
    print("Hệ thống đang kiểm tra trạng thái cấu trúc các file...")
    unrenamed_files = []
    max_stt = 0
    renamed_count = 0

    for item in all_items:
        # Sử dụng Regex kiểm tra xem tên file cũ đã có định dạng bắt đầu bằng "H[Số]." hay chưa
        match = re.match(r'^H(\d+)\.\s+', item['name'])
        if match:
            renamed_count += 1
            stt_value = int(match.group(1))
            if stt_value > max_stt:
                max_stt = stt_value  # Tìm ra số thứ tự H lớn nhất hiện tại
        else:
            unrenamed_files.append(item)

    print(f"-> Phát hiện: {renamed_count} file ĐÃ ĐƯỢC ĐỔI TÊN theo cấu trúc chuẩn H từ trước.")

    # 3. TỰ ĐỘNG XÁC ĐỊNH STT BẮT ĐẦU HOẶC YÊU CẦU NHẬP THỦ CÔNG
    if max_stt > 0:
        start_index = max_stt + 1
        print(f"-> Tìm thấy STT lớn nhất là H{max_stt}. Các file mới sẽ tự động nối tiếp từ STT: H{start_index}")
    else:
        print("-> Thư mục trống hoặc không phát hiện file mẫu dạng 'H...' nào.")
        try:
            # Yêu cầu người dùng nhập số bắt đầu trực tiếp từ bàn phím ở Terminal
            start_index = int(input("Vui lòng nhập Số Thứ Tự (STT) bắt đầu cho thư mục này: "))
        except ValueError:
            print("[Cảnh báo] Số nhập vào không hợp lệ. Hệ thống mặc định bắt đầu từ STT: 1")
            start_index = 1

    # Nếu không còn file nào cần sửa tên thì dừng chương trình luôn
    if not unrenamed_files:
        print("\nToàn bộ file trong thư mục này đã được chuẩn hóa thành công. Không cần xử lý thêm!")
        return

    # 4. Sắp xếp danh sách các file CHƯA ĐỔI TÊN theo đúng số thứ tự xuất hiện gốc ban đầu
    print("Đang sắp xếp danh sách các file chưa đổi tên theo thứ tự xuất hiện...")
    unrenamed_files.sort(key=extract_file_number)

    print(f"\nTìm thấy {len(unrenamed_files)} file chưa chuẩn hóa. Bắt đầu xử lý cuốn chiếu tuần tự:\n")

    # 5. Vòng lặp xử lý chính thức (Chỉ xử lý các file chưa đổi tên)
    for idx, item in enumerate(unrenamed_files):
        file_id = item['id']
        old_name = item['name']

        # Tính toán STT liên tục, cộng dồn tuyến tính từ mốc start_index
        current_stt = start_index + idx
        temp_pdf = f"temp_{file_id}.pdf"
        success = False

        # Vòng lặp thử lại (Retry) tại chỗ nếu gặp sự cố quy trình
        for attempt in range(1, MAX_RETRIES + 1):
            uploaded_file = None
            if attempt == 1:
                print(f"[{idx + 1}/{len(unrenamed_files)}] Đang xử lý: {old_name} (Sẽ gán STT: H{current_stt})")
            else:
                print(f"  [Thử lại lần {attempt}/{MAX_RETRIES}] Do lỗi phát sinh trước đó...")
                time.sleep(RETRY_DELAY)

            try:
                if not os.path.exists(temp_pdf):
                    download_file_from_drive(service, file_id, temp_pdf)

                uploaded_file = gemini_client.files.upload(file=temp_pdf)

                # Gọi AI xử lý nội dung văn bản
                new_name = generate_filename_with_ai(
                    client=gemini_client,
                    model_name=MODEL_NAME,
                    old_name=old_name,
                    text=extract_text_from_pdf_stream(temp_pdf),
                    calculated_stt=current_stt
                )

                if new_name:
                    final_name = f"{new_name}.pdf"

                    # Tiến hành cập nhật tên mới trực tiếp lên Google Drive
                    body = {'name': final_name}
                    service.files().update(fileId=file_id, body=body).execute()
                    print(f"  -> Đổi tên thành công: {final_name}")

                    success = True
                    gemini_client.files.delete(name=uploaded_file.name)
                    break  # Thoát vòng lặp thử lại vì đã xử lý thành công
                else:
                    print("  [Lỗi] AI không trả về chuỗi tên hợp lệ ở lượt này.")
                    if uploaded_file:
                        gemini_client.files.delete(name=uploaded_file.name)

            except Exception as ex:
                print(f"  [Lỗi hệ thống ở lần thử {attempt}]: {ex}")
            finally:
                if not success and uploaded_file:
                    try:
                        gemini_client.files.delete(name=uploaded_file.name)
                    except:
                        pass

        # Xóa file tạm cục bộ trên máy tính
        if os.path.exists(temp_pdf):
            os.remove(temp_pdf)

        # Cơ chế bảo toàn thứ tự: Nếu lỗi hoàn toàn sau 3 lần thử, dừng toàn bộ script
        if not success:
            print(f"\n[DỪNG TIẾN TRÌNH] Đã thử liên tiếp {MAX_RETRIES} lần trên file '{old_name}' nhưng thất bại.")
            print("Hệ thống dừng lại để đảm bảo tính liên tục của chuỗi số thứ tự dòng dữ liệu.")
            break

        print("-" * 30)
        time.sleep(1)  # Giãn cách an toàn cho Rate Limit

    print("\n--- Hoàn tất tiến trình xử lý thư mục ---")


if __name__ == '__main__':
    main()