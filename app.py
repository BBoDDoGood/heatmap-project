import os, re, subprocess
from flask import Flask, render_template, request, jsonify, send_file, Response, url_for
import pymysql
from heatmap_core import generate_heatmap

app = Flask(__name__, static_folder='static')

DB_CONFIG = {
  'host':     os.environ.get('DB_HOST', 'localhost'),
  'user':     os.environ.get('DB_USER', 'heatmapuser'),
  'password': os.environ.get('DB_PASSWORD', '9431'),
  'db':       os.environ.get('DB_NAME', 'heatmapdb'),
  'charset':  'utf8mb4',
  'autocommit': True
}

BASE_DIR      = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
OUTPUT_FOLDER = os.path.join(BASE_DIR, 'static', 'outputs')
SNAPSHOT_DIR  = os.path.join(BASE_DIR, 'static', 'snaps')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

# H.264+faststart 변환
# ... (convert_to_web_mp4, safe_convert, stream_video 함수 동일) ...

def convert_to_web_mp4(src: str, dst: str):
    subprocess.run([
        'ffmpeg','-y','-i',src,
        '-c:v','libx264','-profile:v','baseline','-level','3.0',
        '-movflags','+faststart',
        dst
    ], check=True)


def safe_convert(src: str, out_name: str) -> str:
    out_path = os.path.join(OUTPUT_FOLDER, out_name)
    try:
        convert_to_web_mp4(src, out_path)
        return out_name
    except Exception:
        os.replace(src, out_path)
        return os.path.basename(src)


def stream_video(path: str):
    size = os.path.getsize(path)
    range_hdr = request.headers.get('Range', None)
    if not range_hdr:
        return send_file(path, mimetype='video/mp4')
    m = re.match(r'bytes=(\d+)-(\d*)', range_hdr)
    if not m:
        return send_file(path, mimetype='video/mp4')
    start = int(m.group(1))
    end   = int(m.group(2)) if m.group(2) else size-1
    end   = min(end, size-1)
    length = end-start+1
    with open(path,'rb') as f:
        f.seek(start)
        chunk = f.read(length)
    resp = Response(chunk, status=206, mimetype='video/mp4', direct_passthrough=True)
    resp.headers.add('Content-Range', f'bytes {start}-{end}/{size}')
    resp.headers.add('Accept-Ranges','bytes')
    resp.headers.add('Content-Length',str(length))
    return resp

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    vidfile = request.files.get('video')
    if not vidfile:
        return jsonify(error="No video"), 400

    raw_name = vidfile.filename
    base,_   = os.path.splitext(raw_name)
    raw_path = os.path.join(UPLOAD_FOLDER, raw_name)
    vidfile.save(raw_path)

    conv_name = f"converted_{base}.mp4"
    conv_path = os.path.join(UPLOAD_FOLDER, conv_name)
    try:
        convert_to_web_mp4(raw_path, conv_path)
    except:
        os.replace(raw_path, conv_path)

    vid, det_f, hm_f, ov_f, glob_f = generate_heatmap(
        conv_path, OUTPUT_FOLDER, db_config=DB_CONFIG
    )
    # 스냅샷 생성
    conn = pymysql.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT time_window_start_sec FROM heatmaps WHERE video_id=%s", (vid,))
    for (t,) in cur.fetchall():
        snapshot_path = os.path.join(SNAPSHOT_DIR, f"{vid}_{t}.jpg")
        if not os.path.exists(snapshot_path):
            subprocess.run([
              'ffmpeg', '-y',
              '-ss', str(t),
              '-i', conv_path,
              '-frames:v', '1',
              snapshot_path
        ], check=True)
    conn.close()

    web_det = safe_convert(os.path.join(OUTPUT_FOLDER,det_f), f"web_{det_f}")
    web_ov  = safe_convert(os.path.join(OUTPUT_FOLDER,ov_f),  f"web_{ov_f}")
    web_hm  = safe_convert(os.path.join(OUTPUT_FOLDER,hm_f),  f"web_{hm_f}")

    conn = pymysql.connect(**DB_CONFIG)
    cur  = conn.cursor()
    # 전체 이동 횟수 (trajectory 레코드 수)
    cur.execute("SELECT COUNT(*) FROM trajectories WHERE video_id=%s", (vid,))
    total_moves = cur.fetchone()[0] or 0
 
    # 평균 체류 시간: time_window_start_sec 별 count 의 평균
    cur.execute("""
        SELECT AVG(cnt) 
          FROM (
            SELECT COUNT(*) AS cnt
              FROM heatmaps
             WHERE video_id=%s
             GROUP BY time_window_start_sec
          ) t
    """, (vid,))
    avg_dwell = float(cur.fetchone()[0] or 0)
 
    # Top-5 고밀도 구역
    cur.execute("""
         SELECT x_grid, y_grid, SUM(count) AS cnt
         FROM heatmaps
         WHERE video_id=%s
         GROUP BY x_grid, y_grid
         ORDER BY cnt DESC
         LIMIT 5
    """, (vid,))
    top5 = [
        {'x': x, 'y': y, 'count': cnt}
        for x, y, cnt in cur.fetchall()
    ]
    cur.close()
    conn.close()

    return jsonify({
        'detected_video': url_for('serve_detected', filename=web_det),
        'overlay_video':  url_for('serve_detected', filename=web_ov),
        'heatmap_video':  url_for('serve_heatmap',  filename=web_hm),
        'global_heatmap': url_for('static', filename=f"outputs/{glob_f}"),
        'video_id': vid,
        'total_moves':    total_moves,
        'avg_dwell':      avg_dwell,
        'top5':           top5
    })

@app.route('/dashboard/<int:vid>')
def dashboard(vid):
    return render_template('dashboard.html', vid=vid)

@app.route('/api/dashboard/<int:vid>')
def api_dashboard(vid):
    conn = pymysql.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM trajectories WHERE video_id=%s", (vid,))
    total_moves = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT person_id) FROM trajectories WHERE video_id=%s", (vid,))
    persons = cur.fetchone()[0]
    cur.execute("SELECT x_grid, y_grid, SUM(count) FROM heatmaps WHERE video_id=%s GROUP BY x_grid,y_grid ORDER BY SUM(count) DESC LIMIT 5", (vid,))
    top5 = [{'x':x,'y':y,'count':cnt} for x,y,cnt in cur.fetchall()]
    avg_dwell = total_moves/(persons or 1)/10
    conn.close()
    return jsonify(total_moves=total_moves, top5=top5, avg_dwell=avg_dwell)

@app.route('/api/cell/<int:vid>/<int:x>/<int:y>')
def api_cell(vid, x, y):
    conn = pymysql.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""SELECT time_window_start_sec, count
                   FROM heatmaps 
                   WHERE video_id=%s AND x_grid=%s AND y_grid=%s 
                   ORDER BY time_window_start_sec""", (vid,x,y))
    series=[]; snaps=[]
    for t,cnt in cur.fetchall():
        series.append({'t':t,'count':cnt})
        snap_fn = f"{vid}_{t}.jpg"
        snap_path = os.path.join(SNAPSHOT_DIR, snap_fn)
        if os.path.exists(snap_path):
            snaps.append(
              url_for('static', filename=f"snaps/{snap_fn}")
            )
    conn.close()
    return jsonify(series=series, snaps=snaps)

@app.route('/videos/detected/<filename>')
def serve_detected(filename):
    return stream_video(os.path.join(OUTPUT_FOLDER, filename))

@app.route('/videos/heatmap/<filename>')
def serve_heatmap(filename):
    return stream_video(os.path.join(OUTPUT_FOLDER, filename))

@app.route('/api/videos')
def api_videos():
    conn = pymysql.connect(**DB_CONFIG)
    cur  = conn.cursor()
    cur.execute("SELECT id, filename FROM videos ORDER BY id DESC")
    videos = [{'id': vid, 'name': fn} for vid, fn in cur.fetchall()]
    conn.close()
    return jsonify(videos)

@app.route('/api/results/<int:vid>')
def api_results(vid):
    # 1) 비디오·히트맵 파일 URL (web_ 버전 우선, 없으면 원본)
    def pick_file(prefix, ext, route_fn):
        web_fn = f"web_{prefix}_{vid}.{ext}"
        orig_fn = f"{prefix}_{vid}.{ext}"
        # OUTPUT_FOLDER 는 전역에 정의되어 있습니다.
        if os.path.exists(os.path.join(OUTPUT_FOLDER, web_fn)):
            fn = web_fn
        else:
            fn = orig_fn
        return url_for(route_fn, filename=fn)

    det_url = pick_file('detected', 'mp4', 'serve_detected')
    ovl_url = pick_file('overlay',  'mp4', 'serve_detected')
    hm_url  = pick_file('heatmap',  'mp4', 'serve_heatmap')
    gh_url  = url_for('static', filename=f"outputs/global_heatmap_{vid}.png")


    # 2) 통계(기존 api_dashboard 로직 재사용)
    conn = pymysql.connect(**DB_CONFIG)
    cur  = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM trajectories WHERE video_id=%s", (vid,))
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT person_id) FROM trajectories WHERE video_id=%s", (vid,))
    persons = cur.fetchone()[0] or 1
    avg_dwell = total / persons / 10  # 예시 계산
    cur.execute("""
      SELECT x_grid, y_grid, SUM(count) AS cnt
        FROM heatmaps
       WHERE video_id=%s
       GROUP BY x_grid,y_grid
       ORDER BY cnt DESC
       LIMIT 5
    """, (vid,))
    top5 = [{'x':x,'y':y,'count':c} for x,y,c in cur.fetchall()]
    conn.close()

    return jsonify({
      'video_id':        vid,
      'detected_video':  det_url,
      'overlay_video':   ovl_url,
      'heatmap_video':   hm_url,
      'global_heatmap':  gh_url,
      'total_moves':    total,
      'avg_dwell':      avg_dwell,
      'top5':           top5
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

