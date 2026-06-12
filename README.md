\#B1: Chạy terminal

uv pip install -r requirements.txt

\#B2: Đổi tên file example.env thành .env và config các biến môi trường
GEMINI\_API\_KEY : Gemini Api Key
PDF\_FOLDER\_ID : ID của folder chứa các file pdf cần analyst
SPREADSHEET\_ID : ID của google sheet
SHEET\_NAME : Tên subsheet cần điền
MODEL\_NAME : Tên gemini moddel
RENAME\_FOLDER\_ID : Tên folder chứa các file cần đổi tên

\#Để chạy tác vụ rename chạy bash: python AutoRename.py
\#Để chạy tác vụ push data chạy bash: python AutoPushData.py

