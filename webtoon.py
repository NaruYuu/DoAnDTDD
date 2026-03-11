import sys
import subprocess
import importlib.util

# 1. Khai báo danh sách các thư viện cần thiết (Tên dùng để pip install)
REQUIRED_MODULES = [
    "flask",
    "requests",
    "werkzeug",
    "secure" # Thêm các thư viện khác của bạn vào đây
]

def check_and_install_packages():
    """Kiểm tra và tự động cài đặt thư viện bằng thư viện lõi của Python"""
    missing_packages = []
    
    # Quét từng thư viện xem máy đã có chưa
    for module in REQUIRED_MODULES:
        if importlib.util.find_spec(module) is None:
            missing_packages.append(module)
    
    if missing_packages:
        print(f"📦 Phát hiện thư viện chưa cài đặt: {', '.join(missing_packages)}")
        print("⏳ Đang tự động tải và cài đặt. Vui lòng đợi trong giây lát...")
        try:
            # Chạy ngầm lệnh cài đặt qua pip
            subprocess.check_call([sys.executable, "-m", "pip", "install", *missing_packages])
            print("✅ Cài đặt hoàn tất! Bắt đầu chạy chương trình...\n" + "-"*40)
        except subprocess.CalledProcessError as e:
            print(f"❌ Lỗi trong quá trình cài đặt: {e}")
            sys.exit(1) # Dừng nếu lỗi

# 2. GỌI HÀM NÀY ĐẦU TIÊN
check_and_install_packages()

import os
import zipfile
import re
import json
import hashlib
import mimetypes
import shutil
import socket
import threading
import time
import logging
from collections import deque
from flask import Flask, send_file, render_template_string, abort, request, make_response, redirect, jsonify, session, url_for
from io import BytesIO
from functools import wraps
from werkzeug.utils import secure_filename

# --- CẤU HÌNH LOGGING (CONSOLE 15 DÒNG + GHI FILE ERROR) ---
ERROR_LOG_FILE = "/var/mobile/Documents/webtoon_error.log"

class Console15LinesHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        # Giữ tối đa 15 dòng trong bộ nhớ
        self.log_queue = deque(maxlen=15)

    def emit(self, record):
        try:
            msg = self.format(record)
            # Tách các dòng nếu log chứa nhiều dòng (vd: lỗi traceback)
            for line in msg.split('\n'):
                self.log_queue.append(line)
            
            # Xóa màn hình mượt bằng mã ANSI và in lại 15 dòng mới nhất
            print('\033[2J\033[H', end='') 
            print('\n'.join(self.log_queue))
        except Exception:
            self.handleError(record)

# 1. Cấu hình file log chỉ bắt các lỗi (ERROR)
file_handler = logging.FileHandler(ERROR_LOG_FILE, encoding='utf-8')
file_handler.setLevel(logging.ERROR)
file_handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s in %(module)s:\n%(message)s\n' + '-'*40))

# 2. Cấu hình console log giới hạn 15 dòng
console_handler = Console15LinesHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter('[%(asctime)s] %(message)s', datefmt='%H:%M:%S'))

# 3. Ghi đè hệ thống log của Flask (Werkzeug)
log = logging.getLogger('werkzeug')
log.setLevel(logging.INFO)
log.handlers = [] # Xóa log mặc định
log.addHandler(console_handler)
log.addHandler(file_handler)

app = Flask(__name__)
# 4. Gắn log vào App
app.logger.handlers = []
app.logger.addHandler(console_handler)
app.logger.addHandler(file_handler)
app.secret_key = 'manga_server_secret_key'

# --- CẤU HÌNH ---
ADMIN_USER = "admin"
ADMIN_PASS = "naruyuu2203"
ROOT_DIR = "/var/mobile/Documents/Manga"
DB_PROGRESS_FILE = "/var/mobile/Documents/reading_progress_v2.json"

# --- PHÁT SÓNG IP QUA UDP BROADCAST ---
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

def udp_broadcast():
    broadcaster = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    broadcaster.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        try:
            ip = get_local_ip()
            message = f"MANGA_SERVER|{ip}|5000".encode('utf-8')
            broadcaster.sendto(message, ('255.255.255.255', 5555))
        except:
            pass
        time.sleep(5)

threading.Thread(target=udp_broadcast, daemon=True).start()

# --- THUẬT TOÁN SẮP XẾP TỰ NHIÊN  ---
def chapter_sort_key(item):
    """
    Hàm này bóc tách số Vol và số Ch từ tên chương.
    Trả về một tuple (vol_num, ch_num) để Python ưu tiên sắp xếp Vol trước, Ch sau.
    """
    # Nếu danh sách của bạn là list các dictionary (ví dụ: item['title']), hãy đổi 'item' thành 'item["title"]'
    # Nếu danh sách của bạn là list các chuỗi (string), giữ nguyên 'item'
    title = str(item) 
    
    # 1. Tìm số Volume (Bắt các dạng: Vol. 10, Vol. 2.5)
    vol_match = re.search(r'Vol\.\s*([0-9\.]+)', title, re.IGNORECASE)
    vol_num = float(vol_match.group(1)) if vol_match else 0.0
    
    # 2. Tìm số Chapter (Bắt các dạng: Ch. 82, Ch. 20.5)
    ch_match = re.search(r'Ch\.\s*([0-9\.]+)', title, re.IGNORECASE)
    ch_num = float(ch_match.group(1)) if ch_match else 0.0
    
    # Python sẽ sắp xếp theo phần tử đầu tiên (vol) trước, nếu trùng sẽ xét tiếp (ch)
    return (vol_num, ch_num)

def manga_sort_key(s):
    match = re.search(r'(\d+(\.\d+)?)', s)
    if match:
        return (float(match.group(1)), s.lower())
    return (float('inf'), s.lower())

# --- UTILS ---
def generate_id(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()[:8]

def get_real_path_from_id(parent_path, target_id):
    if not os.path.exists(parent_path): return None
    for item in os.listdir(parent_path):
        if generate_id(item) == target_id: return item
    return None

def is_image(filename):
    return filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif'))

def get_chapter_list(series_path):
    items = []
    if os.path.exists(series_path):
        for f in os.listdir(series_path):
            full_path = os.path.join(series_path, f)
            if os.path.isdir(full_path) or (os.path.isfile(full_path) and f.lower().endswith(('.cbz', '.zip'))):
                items.append(f)
    # CHỈ SỬA DÒNG DƯỚI NÀY: Đổi manga_sort_key thành chapter_sort_key
    return sorted(items, key=chapter_sort_key)

# --- DATABASE ---
def load_db():
    if not os.path.exists(DB_PROGRESS_FILE): return {}
    try:
        with open(DB_PROGRESS_FILE, 'r', encoding='utf-8') as f: return json.load(f)
    except: return {}

def save_db(data):
    try:
        with open(DB_PROGRESS_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)
    except: pass

# --- DECORATOR CHECK ADMIN ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

# KHU VỰC API SYNC (CHO TOOL MÁY TÍNH)
@app.route('/api/get_ip', methods=['GET'])
@admin_required
def get_ip():
    return jsonify({'ip': request.remote_addr})

@app.route('/api/sync/check_auth', methods=['POST'])
def api_check_auth():
    data = request.json
    if not data or data.get('password') != ADMIN_PASS:
        return jsonify({"status": "error", "message": "Sai mật khẩu"}), 401
    return jsonify({"status": "ok", "message": "Kết nối thành công"})

@app.route('/api/sync/list_files', methods=['POST'])
def api_list_files():
    data = request.json
    if not data or data.get('password') != ADMIN_PASS:
        return jsonify({"error": "Unauthorized"}), 401
    
    rel_path = data.get('path', '')
    full_path = os.path.join(ROOT_DIR, rel_path)
    
    if not os.path.exists(full_path):
        return jsonify({"files": []})
        
    files = [f for f in os.listdir(full_path) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))]
    return jsonify({"files": files})

@app.route('/api/sync/upload', methods=['POST'])
def api_upload():
    if request.form.get('password') != ADMIN_PASS:
        return jsonify({"error": "Unauthorized"}), 401
        
    rel_path = request.form.get('path', '')
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
        
    if file:
        filename = secure_filename(file.filename)
        save_dir = os.path.join(ROOT_DIR, rel_path)
        
        if not os.path.exists(save_dir):
            os.makedirs(save_dir, exist_ok=True)
            
        file.save(os.path.join(save_dir, filename))
        return jsonify({"status": "uploaded", "file": filename})

# --- GIAO DIỆN & JS ---
CSS_STYLE = """
<style>
    body { background-color: #000; color: #ccc; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 0; overflow-x: hidden; }
    a { text-decoration: none; color: #bb86fc; }
    .reader-wrapper { width: 100%; max-width: 800px; margin: 0 auto; background: #000; padding-top: 50px; }
    .chapter-container img { width: 100% !important; height: auto !important; display: block; border: none; margin: 0; padding: 0; }
    .overlay-nav { position: fixed; top: 0; left: 0; width: 100%; height: 50px; background: rgba(20, 20, 20, 0.98); border-bottom: 1px solid #333; display: flex; align-items: center; justify-content: space-between; padding: 0 10px; box-sizing: border-box; z-index: 1000; transition: transform 0.3s ease; }
    .overlay-nav.hidden { transform: translateY(-100%); }
    .nav-left, .nav-right { display: flex; align-items: center; } .nav-center { width: 50%; text-align: center; }
    .nav-title { font-size: 14px; color: white; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-weight: bold; }
    .home-btn { font-size: 22px; padding: 5px; }
    .chap-selector { background: #333; color: white; border: 1px solid #555; border-radius: 5px; padding: 5px; font-size: 13px; max-width: 100%; }
    .gamepad-icon { font-size: 20px; color: #555; transition: color 0.3s; margin-right: 10px; }
    .gamepad-icon.active { color: #bb86fc; text-shadow: 0 0 10px #bb86fc; }
    .chap-separator { width: 100%; padding: 40px 20px; background: #111; color: #888; text-align: center; border-top: 1px solid #333; border-bottom: 1px solid #333; }
    .loading-spinner { margin: 20px auto; width: 30px; height: 30px; border: 3px solid #333; border-top: 3px solid #bb86fc; border-radius: 50%; animation: spin 0.8s linear infinite; display: none; }
    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    /* ADMIN STYLES */
    .admin-container { max-width: 900px; margin: 20px auto; padding: 20px; background: #1e1e1e; border-radius: 8px; }
    .admin-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; border-bottom: 1px solid #333; padding-bottom: 10px; }
    .file-table { width: 100%; border-collapse: collapse; margin-top: 10px; }
    .file-table th, .file-table td { text-align: left; padding: 12px; border-bottom: 1px solid #333; }
    .file-table tr:hover { background: #2a2a2a; }
    .icon { margin-right: 8px; }
    .btn { padding: 8px 15px; border-radius: 5px; background: #3700b3; color: white; border: none; cursor: pointer; font-size: 14px; }
    .btn-danger { background: #cf6679; color: black; }
    .btn-sm { padding: 4px 8px; font-size: 12px; }
    .breadcrumb { margin-bottom: 15px; font-size: 14px; color: #888; }
    .breadcrumb a { color: #bb86fc; }
    .form-group { margin-bottom: 15px; border: 1px solid #444; padding: 15px; border-radius: 5px; }
    .form-control { padding: 8px; width: 100%; box-sizing: border-box; background: #333; border: 1px solid #555; color: white; margin-top: 5px; }
    .login-box { max-width: 400px; margin: 100px auto; padding: 30px; background: #1e1e1e; border-radius: 10px; text-align: center; }
</style>
"""

JS_READER_SCRIPT = """
<script>
    let currentSeriesId = "";
    let isLoading = false;
    let loadedChapters = [];
    let isNavVisible = true;
    let lastScrollY = 0;
    let allChaptersData = []; 
    let gamepadIndex = null;
    let buttonPressState = {}; 

    function initReader(seriesId, initialChapId, initialChapName, initialNextId, allChapsJson) {
        currentSeriesId = seriesId;
        loadedChapters.push(initialChapId);
        allChaptersData = JSON.parse(allChapsJson);
        buildChapterSelector(initialChapId);
        window.addEventListener('scroll', handleScroll);
        window.addEventListener("gamepadconnected", (e) => { gamepadIndex = e.gamepad.index; document.getElementById('gp-icon').classList.add('active'); requestAnimationFrame(gamepadLoop); });
        window.addEventListener("gamepaddisconnected", (e) => { gamepadIndex = null; document.getElementById('gp-icon').classList.remove('active'); });
    }

    function handleScroll() {
        const scrollY = window.scrollY;
        const nav = document.getElementById('top-nav');
        if (scrollY > lastScrollY && scrollY > 50) { if (isNavVisible) { nav.classList.add('hidden'); isNavVisible = false; } } 
        else { if (!isNavVisible) { nav.classList.remove('hidden'); isNavVisible = true; } }
        lastScrollY = scrollY;

        if ((document.documentElement.scrollHeight - window.innerHeight - scrollY) < 1500) {
            let lastChapter = document.querySelector('.chapter-container:last-child');
            if (lastChapter) {
                let nextId = lastChapter.dataset.nextId;
                if (nextId && nextId !== 'None' && !isLoading && !loadedChapters.includes(nextId)) loadNextChapter(nextId);
            }
        }
        detectCurrentState();
    }

    function gamepadLoop() {
        if (gamepadIndex === null) return;
        const gp = navigator.getGamepads()[gamepadIndex];
        if (!gp) return;
        let stickY = gp.axes[3]; 
        if (Math.abs(stickY) > 0.1) window.scrollBy(0, stickY * 25);
        if (gp.buttons[12].pressed) window.scrollBy(0, -15);
        if (gp.buttons[13].pressed) window.scrollBy(0, 15);
        if (gp.buttons[5].pressed) { if (!buttonPressState['r1']) { jumpNextChapter(); buttonPressState['r1'] = true; } } else buttonPressState['r1'] = false;
        if (gp.buttons[4].pressed) { if (!buttonPressState['l1']) { jumpPrevChapter(); buttonPressState['l1'] = true; } } else buttonPressState['l1'] = false;
        if (gp.buttons[3].pressed) { if (!buttonPressState['tri']) { toggleNav(); buttonPressState['tri'] = true; } } else buttonPressState['tri'] = false;
        requestAnimationFrame(gamepadLoop);
    }
    
    function jumpNextChapter() { const s = document.getElementById('chap-select'); if (s.selectedIndex < s.options.length - 1) { s.selectedIndex++; jumpToChapter(s); } }
    function jumpPrevChapter() { const s = document.getElementById('chap-select'); if (s.selectedIndex > 0) { s.selectedIndex--; jumpToChapter(s); } }
    function toggleNav() { const n = document.getElementById('top-nav'); isNavVisible = !isNavVisible; if(isNavVisible) n.classList.remove('hidden'); else n.classList.add('hidden'); }
    function buildChapterSelector(id) { const s = document.getElementById('chap-select'); s.innerHTML=""; allChaptersData.forEach(c => { let o = document.createElement('option'); o.value=c.id; o.text=c.name; if(c.id===id) o.selected=true; s.appendChild(o); }); }
    function jumpToChapter(obj) { if(obj.value) window.location.href = `/read/${currentSeriesId}/${obj.value}`; }
    
    function detectCurrentState() {
        const chapters = document.querySelectorAll('.chapter-container');
        let activeChap = null;
        chapters.forEach(chap => { const r = chap.getBoundingClientRect(); if (r.top <= window.innerHeight/2 && r.bottom >= window.innerHeight/2) activeChap = chap; });
        if (activeChap) {
            const id = activeChap.dataset.chapId;
            if (!window.location.href.includes(id)) {
                history.replaceState(null, '', `/read/${currentSeriesId}/${id}`);
                document.getElementById('nav-title').innerText = activeChap.dataset.chapName;
                fetch('/api/save_progress', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ series_id: currentSeriesId, chap_id: id }) });
                const s = document.getElementById('chap-select'); if(s.value !== id) s.value = id;
            }
        }
    }

    async function loadNextChapter(id) {
        isLoading = true; document.getElementById('spinner').style.display = 'block';
        try {
            const res = await fetch(`/api/chapter_data/${currentSeriesId}/${id}`);
            const data = await res.json();
            if (data.error) return;
            const wrap = document.getElementById('reader-wrapper');
            const sep = document.createElement('div'); sep.className = 'chap-separator'; sep.innerHTML = `<p>Finished</p><h3>${data.prev_chap_name}</h3><br><p>Next</p><div class="next-highlight">${data.chap_name}</div>`;
            wrap.appendChild(sep);
            const con = document.createElement('div'); con.className = 'chapter-container'; con.id = 'chap-'+id; con.dataset.chapId=id; con.dataset.nextId=data.next_id; con.dataset.chapName=data.chap_name;
            data.images.forEach(u => { const i = document.createElement('img'); i.src=u; con.appendChild(i); });
            wrap.appendChild(con);
            loadedChapters.push(id);
        } catch(e) { console.error(e); } finally { isLoading = false; document.getElementById('spinner').style.display = 'none'; }
    }
</script>
"""

# --- ADMIN ROUTES ---

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form['username'] == ADMIN_USER and request.form['password'] == ADMIN_PASS:
            session['is_admin'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            return render_template_string(f"""<html><head><title>Login</title><meta name="viewport" content="width=device-width, initial-scale=1">{CSS_STYLE}</head><body><div class="login-box"><h2 style="color:white">Admin Login</h2><p style="color:red">Sai mật khẩu!</p><form method="POST"><input type="text" name="username" class="form-control" placeholder="User" required><br><input type="password" name="password" class="form-control" placeholder="Pass" required><br><br><button type="submit" class="btn">Đăng nhập</button></form></div></body></html>""")
            
    return render_template_string(f"""<html><head><title>Login</title><meta name="viewport" content="width=device-width, initial-scale=1">{CSS_STYLE}</head><body><div class="login-box"><h2 style="color:white">Admin Login</h2><form method="POST"><input type="text" name="username" class="form-control" placeholder="User" required><br><input type="password" name="password" class="form-control" placeholder="Pass" required><br><br><button type="submit" class="btn">Đăng nhập</button></form></div></body></html>""")

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect('/')

@app.route('/admin')
@app.route('/admin/browse/', defaults={'path': ''})
@app.route('/admin/browse/<path:path>')
@admin_required
def admin_dashboard(path=""):
    abs_root = os.path.abspath(ROOT_DIR)
    abs_path = os.path.abspath(os.path.join(ROOT_DIR, path))
    if not abs_path.startswith(abs_root): return "Access Denied"
    
    items = []
    if os.path.exists(abs_path):
        for f in sorted(os.listdir(abs_path), key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]):
            full_p = os.path.join(abs_path, f)
            rel_p = os.path.join(path, f)
            is_dir = os.path.isdir(full_p)
            size = "-"
            if not is_dir: size = f"{os.path.getsize(full_p) / 1024:.1f} KB"
            items.append({'name': f, 'is_dir': is_dir, 'path': rel_p, 'size': size})
    parent_path = os.path.dirname(path)
    
    return render_template_string(f"""
    <html><head><title>Admin Manager</title><meta name="viewport" content="width=device-width, initial-scale=1">{CSS_STYLE}</head><body>
    <div class="admin-container">
        <div class="admin-header"><h2 style="color:white; margin:0">File Manager</h2><div><a href="/" class="btn btn-sm">Về Webtoon</a> <a href="/admin/logout" class="btn btn-sm btn-danger">Logout</a></div></div>
        <div class="breadcrumb">📍 <a href="/admin">Root</a> / {path} {f'<a href="/admin/browse/{parent_path}">[⬆ Lên 1 cấp]</a>' if path else ''}</div>
        <div class="form-group"><form action="/admin/create_folder" method="POST" style="display:flex; gap:10px;"><input type="hidden" name="current_path" value="{path}"><input type="text" name="folder_name" class="form-control" placeholder="Folder mới..." style="margin:0" required><button type="submit" class="btn">Tạo</button></form></div>
        <div class="form-group"><form action="/admin/upload" method="POST" enctype="multipart/form-data"><input type="hidden" name="current_path" value="{path}"><label style="color:#ccc">Upload (Ảnh/ZIP):</label><input type="file" name="files" class="form-control" multiple required><button type="submit" class="btn" style="margin-top:10px">Upload</button></form></div>
        <table class="file-table"><thead><tr style="color:#888"><th>Tên</th><th>Kích thước</th><th>Hành động</th></tr></thead>
        <tbody>{''.join([f'''<tr><td><span class="icon">{'📁' if i['is_dir'] else '📄'}</span><a href="/admin/browse/{i['path']}" style="color:{'#fff' if i['is_dir'] else '#aaa'}">{i['name']}</a></td><td style="color:#666">{i['size']}</td><td><form action="/admin/delete" method="POST" onsubmit="return confirm('Xóa?');" style="margin:0"><input type="hidden" name="path" value="{i['path']}"><button type="submit" class="btn btn-danger btn-sm">Xóa</button></form></td></tr>''' for i in items])}</tbody></table>
    </div></body></html>""")

@app.route('/admin/create_folder', methods=['POST'])
@admin_required
def create_folder():
    full_path = os.path.join(ROOT_DIR, request.form.get('current_path', ''), request.form.get('folder_name'))
    try: os.makedirs(full_path, exist_ok=True)
    except Exception as e: return f"Error: {e}"
    return redirect(f"/admin/browse/{request.form.get('current_path', '')}")

@app.route('/admin/delete', methods=['POST'])
@admin_required
def delete_item():
    path = request.form.get('path'); full_path = os.path.join(ROOT_DIR, path)
    try: shutil.rmtree(full_path) if os.path.isdir(full_path) else os.remove(full_path)
    except Exception as e: return f"Error: {e}"
    return redirect(f"/admin/browse/{os.path.dirname(path)}")

@app.route('/admin/upload', methods=['POST'])
@admin_required
def upload_files():
    target_dir = os.path.join(ROOT_DIR, request.form.get('current_path', ''))
    for file in request.files.getlist('files'):
        if file.filename == '': continue
        save_path = os.path.join(target_dir, secure_filename(file.filename))
        file.save(save_path)
        if file.filename.lower().endswith('.zip'):
            try:
                with zipfile.ZipFile(save_path, 'r') as z:
                    ext = os.path.join(target_dir, os.path.splitext(file.filename)[0])
                    os.makedirs(ext, exist_ok=True); z.extractall(ext)
                os.remove(save_path)
            except: pass
    return redirect(f"/admin/browse/{request.form.get('current_path', '')}")

# --- USER ROUTES ---

@app.route('/login', methods=['POST'])
def login(): r=redirect('/'); r.set_cookie('username',request.form.get('username'),max_age=3e7); return r

@app.route('/logout')
def logout(): r=redirect('/'); r.set_cookie('username','',expires=0); return r

@app.route('/api/save_progress', methods=['POST'])
def save_progress():
    u=request.cookies.get('username'); d=request.json; db=load_db()
    if u: db.setdefault(u,{})[d['series_id']]=d['chap_id']; save_db(db)
    return jsonify({'ok':True})

@app.route('/api/chapter_data/<series_id>/<chap_id>')
def api_chapter_data(series_id, chap_id):
    rn=get_real_path_from_id(ROOT_DIR, series_id)
    if not rn: return jsonify({"error": "Not found"})
    rc=get_real_path_from_id(os.path.join(ROOT_DIR, rn), chap_id)
    if not rc: return jsonify({"error": "Not found"})
    l=get_chapter_list(os.path.join(ROOT_DIR, rn)); idx=l.index(rc)
    nid=generate_id(l[idx+1]) if idx<len(l)-1 else "None"; prev=l[idx-1] if idx>0 else "Start"
    imgs=sorted([f for f in os.listdir(os.path.join(ROOT_DIR,rn,rc)) if is_image(f)], key=manga_sort_key) 
    return jsonify({"chap_name": rc, "prev_chap_name": prev, "next_id": nid, "images": [f"/image/{series_id}/{chap_id}/{i}" for i in imgs]})

@app.route('/')
def home():
    user = request.cookies.get('username'); headers = {'Cache-Control': 'no-cache'}
    if not user: return render_template_string(f"""<html><head><meta name="viewport" content="width=device-width,initial-scale=1">{CSS_STYLE}</head><body style="padding:20px;text-align:center"><form action="/login" method="POST"><h3 style="color:#fff">Tên bạn?</h3><input name="username"><button>Lưu</button></form></body></html>""")
    db=load_db(); prog=db.get(user,{})
    h=""
    if os.path.exists(ROOT_DIR):
        for d in sorted(os.listdir(ROOT_DIR)):
            if os.path.isdir(os.path.join(ROOT_DIR, d)):
                sid=generate_id(d); last=prog.get(sid); extra=""
                if last: extra=f"<br><small style='color:#bb86fc'>Đọc tiếp: {get_real_path_from_id(os.path.join(ROOT_DIR,d), last)}</small>"
                h+=f'<div style="background:#111;margin:10px;padding:15px;border-radius:8px"><a href="/series/{sid}">📁 {d}</a> {extra}</div>'
    return render_template_string(f"""<html><head><meta name="viewport" content="width=device-width,initial-scale=1">{CSS_STYLE}</head><body style="padding:20px;max-width:800px;margin:0 auto;background:#000;"><div style="display:flex;justify-content:space-between;align-items:center"><h2 style="color:#fff">📚 Kho Truyện</h2><a href="/admin" style="font-size:12px;color:#555">Admin</a></div>{h}</body></html>""", headers=headers)

@app.route('/series/<series_id>')
def view_series(series_id):
    headers = {'Cache-Control': 'no-cache, no-store, must-revalidate'}
    real_name = get_real_path_from_id(ROOT_DIR, series_id)
    if not real_name: abort(404)
    chaps = get_chapter_list(os.path.join(ROOT_DIR, real_name))
    user = request.cookies.get('username')
    last_id = load_db().get(user, {}).get(series_id)
    html_list = ""
    for c in chaps:
        c_id = generate_id(c)
        style = "background:#2a2a2a;border-left:4px solid #bb86fc;" if c_id == last_id else "background:#1e1e1e;"
        html_list += f'<a href="/read/{series_id}/{c_id}" style="display:block;padding:15px;margin-bottom:10px;border-radius:8px;text-decoration:none;color:#ccc;{style}">📄 {c}</a>'
    return render_template_string(f"""<html><head><title>{real_name}</title><meta name="viewport" content="width=device-width,initial-scale=1">{CSS_STYLE}</head><body style="padding:20px;max-width:800px;margin:0 auto;background:#000;"><h3><a href="/" style="color:#bb86fc;text-decoration:none;">⬅ Home</a> / {real_name}</h3>{html_list}</body></html>""", headers=headers)

@app.route('/read/<series_id>/<chap_id>')
def read_chapter(series_id, chap_id):
    data_response = api_chapter_data(series_id, chap_id)
    data = data_response.json if not isinstance(data_response, dict) else data_response
    if data.get('error'): abort(404)
    real_series = get_real_path_from_id(ROOT_DIR, series_id)
    all_chaps_raw = get_chapter_list(os.path.join(ROOT_DIR, real_series))
    all_chaps_json_obj = []
    for c in all_chaps_raw: all_chaps_json_obj.append({"id": generate_id(c), "name": c})
    all_chaps_json_str = json.dumps(all_chaps_json_obj)
    next_id = data['next_id'] if data['next_id'] else "None"
    img_tags = "".join([f'<img src="{url}" />' for url in data['images']])
    html = f"""<html><head><title>{data['chap_name']}</title><meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=0">{CSS_STYLE}</head><body>
        <div id="top-nav" class="overlay-nav"><div class="nav-left"><a href="/series/{series_id}" class="home-btn">🏠</a></div><div class="nav-center"><div class="nav-title" id="nav-title">{data['chap_name']}</div></div><div class="nav-right"><div id="gp-icon" class="gamepad-icon">🎮</div><select id="chap-select" class="chap-selector" onchange="jumpToChapter(this)"></select></div></div>
        <div class="reader-wrapper" id="reader-wrapper"><div class="chapter-container" id="chap-{chap_id}" data-chap-id="{chap_id}" data-next-id="{next_id}" data-chap-name="{data['chap_name']}">{img_tags}</div></div><div id="spinner" class="loading-spinner"></div>
        {JS_READER_SCRIPT}<script>initReader("{series_id}", "{chap_id}", "{data['chap_name']}", "{next_id}", '{all_chaps_json_str}');</script></body></html>"""
    return render_template_string(html)

@app.route('/image/<series_id>/<chap_id>/<path:filename>')
def serve_image(series_id, chap_id, filename):
    rn=get_real_path_from_id(ROOT_DIR, series_id)
    if not rn: abort(404)
    rc=get_real_path_from_id(os.path.join(ROOT_DIR, rn), chap_id)
    if not rc: abort(404)
    try: return send_file(os.path.join(ROOT_DIR, rn, rc, filename))
    except: abort(404)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)