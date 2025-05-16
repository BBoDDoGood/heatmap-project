document.addEventListener('DOMContentLoaded', ()=>{
  const vid = location.pathname.split('/').pop();
  let cellChartInstance = null; 
  // 1) 대시보드 수치 로드
  fetch(`/api/dashboard/${vid}`)
    .then(res=>res.json())
    .then(data=>{
      document.getElementById('totalMoves').innerText = data.total_moves;
      document.getElementById('avgDwell').innerText  = data.avg_dwell.toFixed(1);
      const ul = document.getElementById('top5List');
      data.top5.forEach(c=>{
        const li = document.createElement('li');
        li.className = 'list-group-item';
        li.innerText = `(${c.x},${c.y}) — ${c.count}`;
        ul.append(li);
      });
    });
   const GRID_PIXELS = 20;
  // 2) 이미지 클릭 → 셀 API & 모달
  const img = document.getElementById('globalHeatmap');
  img.addEventListener('click', e=>{
    // 1) 화면 상의 이미지 위치/크기
    const rect = img.getBoundingClientRect();
    // 2) 클릭 위치 → 이미지의 실제 픽셀 위치로 변환
    const x_img = (e.clientX - rect.left) * (img.naturalWidth  / rect.width);
    const y_img = (e.clientY - rect.top)  * (img.naturalHeight / rect.height);
    // 3) 셀 인덱스 계산
    const gx = Math.floor(x_img / GRID_PIXELS);  
    const gy = Math.floor(y_img / GRID_PIXELS);

    console.log(`셀(${gx},${gy}) 클릭`);
    fetch(`/api/cell/${vid}/${gx}/${gy}`)
      .then(r=>r.json())
      .then(j=>{
        document.getElementById('modalTitle').innerText = `셀 (${gx},${gy}) 방문 이력`;
        const ctx = document.getElementById('cellChart').getContext('2d');

        // 이미 그려진 차트가 있으면 파괴
        if (cellChartInstance) {
          cellChartInstance.destroy();
        }

        // 새 차트 생성 후 인스턴스 저장
        cellChartInstance = new Chart(ctx, {
          type: 'line',
          data: {
            labels: j.series.map(d=>d.t),
            datasets: [{
              label: '방문자 수',
              data:  j.series.map(d=>d.count),
              fill: false,
              tension: 0.1
            }]
          },
          options: {
            scales: {
              x: { title: { display: true, text: '시간(초)' } },
              y: { title: { display: true, text: '방문 횟수' }, beginAtZero: true }
            }
          }
        });

        const snapsDiv = document.getElementById('snapshots');
        snapsDiv.innerHTML = '';
        j.snaps.forEach(url=>{
          const thumb = document.createElement('img');
          thumb.src = url; thumb.className='img-thumbnail';
          snapsDiv.append(thumb);
        });
//        new bootstrap.Modal(document.getElementById('cellModal')).show();

        // 모달 띄우기 (bootstrap 번들이 로드된 상태여야 함)
        const modal = new bootstrap.Modal(
          document.getElementById('cellModal')
        );
        modal.show();
      });
  });
});
