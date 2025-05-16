from ultralytics import YOLO
import cv2, numpy as np
import pymysql, os
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from collections import defaultdict

# ─── 한글 폰트 설정 ───
font_path = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
if os.path.exists(font_path):
    fm.fontManager.addfont(font_path)
    plt.rcParams["font.family"] = "NanumGothic"
    plt.rcParams["axes.unicode_minus"] = False
# ──────────────────────


def generate_heatmap(video_path: str, output_dir: str, db_config: dict = None):
    # 0) tracks 디렉토리
    tracks_dir = os.path.join(output_dir, "tracks")
    os.makedirs(tracks_dir, exist_ok=True)

    # 1) 모델 & 비디오 열기
    model = YOLO("yolov8m.pt")
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError("영상 열기 실패")

    # 2) 환경 설정
    fps = cap.get(cv2.CAP_PROP_FPS)
    det_interval = 1  # 1프레임마다 detect
    hm_interval = 10  # 10프레임마다 heatmap 집계
    w, h = 1280, 720
    grid = 20
    H, W = h // grid, w // grid

    heatmaps = defaultdict(lambda: np.zeros((H, W), dtype=np.uint32))
    global_map = np.zeros((H, W), dtype=np.uint32)
    tracks = defaultdict(list)

    # 3) DB (옵션)
    vid = 1
    cursor = db = None
    if db_config:
        # db_config 에 charset/autocommit 이 이미 있으면 중복 피해야 합니다
        db = pymysql.connect(**db_config)
        cursor = db.cursor()
        fn = os.path.basename(video_path)
        cursor.execute("INSERT INTO videos (filename) VALUES (%s)", (fn,))
        vid = cursor.lastrowid

    # 4) 출력 파일명 & VideoWriter 세팅
    det_fname = f"detected_{vid}.mp4"
    ov_fname = f"overlay_{vid}.mp4"
    hm_fname = f"heatmap_{vid}.mp4"
    global_fname = f"global_heatmap_{vid}.png"

    det_path = os.path.join(output_dir, det_fname)
    ov_path = os.path.join(output_dir, ov_fname)
    hm_path = os.path.join(output_dir, hm_fname)

    out_det = cv2.VideoWriter(det_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    out_ov = cv2.VideoWriter(ov_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))

    frame_no = 0
    hm_idx = 0

    # 5) 프레임 루프: 매 프레임 detect, 10프레임마다 heatmap 집계
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_no += 1
        resized = cv2.resize(frame, (w, h))

        # 5-1) 매 프레임 detect → 바운딩 박스 + trajectory
        if frame_no % det_interval == 0:
            res = model.track(resized, persist=True, verbose=False)[0]
            if res.boxes.id is not None:
                ids = res.boxes.id.cpu().numpy()
                bboxes = res.boxes.xyxy.cpu().numpy()
                classes = res.boxes.cls.cpu().numpy()

                for i, cls in enumerate(classes):
                    if int(cls) != 0:
                        continue

                    x1, y1, x2, y2 = bboxes[i]
                    pid = int(ids[i])
                    cx, cy = int((x1 + x2) / 2), int((y1 + y2) / 2)
                    gx, gy = cx // grid, cy // grid

                    # 5-2) 10프레임 단위 heatmap에만 집계
                    if frame_no % hm_interval == 0:
                        heatmaps[hm_idx][gy, gx] += 1
                        global_map[gy, gx] += 1

                        if cursor:
                            cursor.execute(
                                "INSERT INTO heatmaps "
                                "(video_id, time_window_start_sec, x_grid, y_grid, count) "
                                "VALUES (%s, %s, %s, %s, %s)",
                                (vid, frame_no, gx, gy, int(heatmaps[hm_idx][gy, gx])),
                            )

                    # 궤적 저장
                    tracks[pid].append((cx, cy))
                    if cursor:
                        cursor.execute(
                            "INSERT INTO trajectories "
                            "(video_id, person_id, frame_number, x, y) "
                            "VALUES (%s, %s, %s, %s, %s)",
                            (vid, pid, frame_no, cx, cy),
                        )

                    # 바운딩 박스 & ID 텍스트
                    cv2.rectangle(
                        resized, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2
                    )
                    cv2.putText(
                        resized,
                        f"ID {pid}",
                        (int(x1), int(y1) - 10),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (255, 255, 255),
                        1,
                    )

                # 10프레임마다 적용된 후에 인덱스 올리기
                if frame_no % hm_interval == 0:
                    hm_idx += 1

        # 5-3) 검출된 프레임 쓰기
        out_det.write(resized)

        # 5-4) 실시간 오버레이 (누적 global_map)
        norm = cv2.normalize(
            global_map.astype(np.float32), None, 0, 255, cv2.NORM_MINMAX
        ).astype(np.uint8)
        heat = cv2.resize(norm, (w, h), interpolation=cv2.INTER_NEAREST)
        heatc = cv2.applyColorMap(heat, cv2.COLORMAP_JET)
        overlay = cv2.addWeighted(resized, 0.7, heatc, 0.3, 0)
        out_ov.write(overlay)

    cap.release()
    out_det.release()
    out_ov.release()

    # 6) 10프레임 단위 히트맵 비디오 생성 (원본 fps + 프레임 복제)
    out_hm = cv2.VideoWriter(
        hm_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h)  # 원본 fps 사용
    )

    for k in sorted(heatmaps.keys()):
        hm = heatmaps[k].astype(np.float32)
        norm8 = cv2.normalize(hm, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        up = cv2.resize(norm8, (w, h), interpolation=cv2.INTER_NEAREST)
        heatc2 = cv2.applyColorMap(up, cv2.COLORMAP_HOT)
        # 각 heatmap 프레임을 hm_interval 횟수만큼 반복 기록
        for _ in range(hm_interval):
            out_hm.write(heatc2)
    out_hm.release()

    # 7) 전체 히트맵 이미지 (정확한 1280×720 해상도, 크롭 비활성)
    global_path = os.path.join(output_dir, global_fname)
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    ax = plt.gca()
    ax.axis("off")
    ax.imshow(global_map, cmap="hot", interpolation="nearest")
    ax.set_title("전체 히트맵")
    fig.savefig(global_path, dpi=dpi, bbox_inches=None, pad_inches=0)  # 크롭 비활성
    plt.close(fig)

    # 8) ID별 궤적 저장
    for pid, pts in tracks.items():
        np.save(os.path.join(tracks_dir, f"ID_{pid}.npy"), np.array(pts))

    if cursor:
        cursor.close()
    if db:
        db.commit()  # ← 여기서 한 번만 커밋
        db.close()

    return vid, det_fname, hm_fname, ov_fname, global_fname
